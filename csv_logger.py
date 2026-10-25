"""批量写入多盘 xlsx；持久化待写队列由 MonitorService 管理。"""
from collections import defaultdict
from datetime import date, datetime, timedelta
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

from config_manager import DATA_DIR

LOG = logging.getLogger(__name__)
EXPORT_DIR = Path(DATA_DIR) / "v2"
EXPORT_LOCK = threading.RLock()
GIB = 1024 ** 3
TIB = 1024 ** 4

SAMPLE_COLUMNS = [
    ("record_id", "记录ID"), ("timestamp", "采样时间"), ("disk_id", "硬盘ID"),
    ("name", "硬盘名称"), ("model", "型号"), ("serial", "序列号"), ("device", "设备路径"),
    ("total_gib", "累计写入GiB"), ("delta_gib", "区间增量GiB"),
    ("today_gib", "今日已确认GiB"), ("unallocated_gib", "区间未分配GiB"),
    ("temperature", "温度℃"), ("interval_start", "区间开始"), ("note", "说明"),
    ("total_bytes", "累计写入字节"), ("delta_bytes", "区间增量字节"),
    ("health_percent", "健康度估计%"), ("percentage_used", "寿命已用%"),
    ("health_warning", "设备健康告警"),
]
DAILY_COLUMNS = [
    ("date", "日期"), ("disk_id", "硬盘ID"), ("name", "硬盘名称"),
    ("written_gib", "当日已确认GiB"), ("unallocated_gib", "本日发现未分配GiB"),
    ("start_tib", "首个基线TiB"), ("end_tib", "末次累计TiB"),
    ("readings", "采样次数"), ("sampled_at", "更新时刻"), ("note", "统计说明"),
]
TREND_COLUMNS = [
    ("date", "日期"), ("disk_id", "硬盘ID"), ("name", "硬盘名称"),
    ("written_gib", "当日已确认GiB"), ("avg_7d_gib", "近7日已采样日均GiB"),
    ("sample_days", "近7日采样天数"), ("unallocated_gib", "本日发现未分配GiB"),
    ("end_tib", "累计写入TiB"), ("sampled_at", "更新时刻"), ("note", "统计说明"),
]
PROCESS_COLUMNS = [
    ("record_id", "记录ID"), ("timestamp", "采样时间"), ("name", "进程名称"),
    ("pid", "PID"), ("delta_gib", "进程IO增量GiB"), ("scope", "统计范围"),
]


def _safe_save(wb, filepath, retries=3, delay=0.15):
    """原子替换失败时明确抛出异常，让调用者保留待写队列。"""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        fd, tmp = tempfile.mkstemp(prefix=".xlsx-", suffix=".xlsx", dir=filepath.parent)
        os.close(fd)
        try:
            wb.save(tmp)
            with open(tmp, "rb+") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, filepath)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise PermissionError(f"{filepath.name} 无法保存，请关闭占用它的 Excel 窗口")
            time.sleep(delay)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def _open_book(path, columns):
    path = Path(path)
    if path.exists():
        try:
            book = load_workbook(path)
        except PermissionError:
            raise
        except Exception as exc:
            # 不把读取失败一律当成损坏，也不删除历史文件。
            backup = path.with_suffix(path.suffix + ".bad")
            if not backup.exists():
                shutil.copy2(path, backup)
            raise ValueError(f"{path.name} 无法读取，已保留原文件，请修复或恢复备份") from exc
        sheet = book.active
        expected = [label for _, label in columns]
        if [cell.value for cell in sheet[1]] != expected:
            book.close()
            raise ValueError(f"{path.name} 表头不匹配，未覆盖文件")
    else:
        book = Workbook()
        sheet = book.active
        sheet.append([label for _, label in columns])
    sheet.freeze_panes = "A2"
    for index, (key, _) in enumerate(columns, 1):
        width = 26 if key in ("timestamp", "sampled_at", "interval_start") else 20
        if key in ("disk_id", "record_id", "note", "scope"):
            width = 36
        sheet.column_dimensions[get_column_letter(index)].width = width
    return book


def _assign(sheet, row_number, columns, values):
    for index, (key, _) in enumerate(columns, 1):
        value = values.get(key)
        cell = sheet.cell(row_number, index)
        cell.value = value
        # 名称和序列号属于普通文本，避免以 = 开头时变成 Excel 公式。
        if isinstance(value, str):
            cell.data_type = "s"
        elif isinstance(value, float):
            cell.number_format = "0.0000"


def _numbered_records(sheet, columns):
    keys = [key for key, _ in columns]
    return [
        (number, dict(zip(keys, values)))
        for number, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2)
        if any(value is not None for value in values)
    ]


def _records(sheet, columns):
    return [record for _, record in _numbered_records(sheet, columns)]


def _upsert(path, columns, incoming, keys, update_field=None, transform=None):
    if not incoming:
        return
    book = _open_book(path, columns)
    try:
        sheet = book.active
        existing = _numbered_records(sheet, columns)
        row_numbers = {tuple(record[key] for key in keys): index for index, record in existing}
        changed = False
        for record in incoming:
            key = tuple(record[field] for field in keys)
            row = row_numbers.get(key)
            if row is not None:
                if not update_field:
                    continue
                column = [field for field, _ in columns].index(update_field) + 1
                saved_at = sheet.cell(row, column).value or ""
                if saved_at > record[update_field]:
                    continue
            else:
                row = sheet.max_row + 1
                row_numbers[key] = row
            _assign(sheet, row, columns, record)
            changed = True
        if changed:
            if transform:
                transform(sheet, columns)
            sheet.auto_filter.ref = sheet.dimensions
            _safe_save(book, path)
    finally:
        book.close()


def _averages(sheet, columns):
    """日期窗口内有采样的日均；没有记录的日期不伪装成零写入。"""
    rows = _numbered_records(sheet, columns)
    by_disk = defaultdict(dict)
    for _, row in rows:
        by_disk[row["disk_id"]][date.fromisoformat(row["date"])] = float(row["written_gib"] or 0)
    for index, row in rows:
        day = date.fromisoformat(row["date"])
        window = by_disk[row["disk_id"]]
        values = [
            window[candidate] for candidate in (day - timedelta(days=n) for n in range(7))
            if candidate in window
        ]
        row["avg_7d_gib"] = sum(values) / len(values) if values else 0
        row["sample_days"] = len(values)
        _assign(sheet, index, columns, row)


def flush_batches(batches, directory=None):
    """幂等导出：任一文件失败可重放整批，不重复累计或追加明细。"""
    root = Path(directory) if directory is not None else EXPORT_DIR
    with EXPORT_LOCK:
        root.mkdir(parents=True, exist_ok=True)
        samples, processes, days = defaultdict(list), defaultdict(list), []
        for batch in batches:
            for item in batch["samples"]:
                row = dict(item)
                for raw, target in (
                    ("total_bytes", "total_gib"), ("delta_bytes", "delta_gib"),
                    ("today_bytes", "today_gib"), ("unallocated_bytes", "unallocated_gib"),
                ):
                    row[target] = item[raw] / GIB
                # Excel 数值只有约 15 位有效精度，原始计数以文本保留。
                row["total_bytes"], row["delta_bytes"] = str(item["total_bytes"]), str(item["delta_bytes"])
                month = datetime.fromisoformat(item["timestamp"]).strftime("%Y-%m")
                samples[month].append(row)
            for item in batch["days"]:
                days.append(dict(
                    item, written_gib=item["written_bytes"] / GIB,
                    unallocated_gib=item["unallocated_bytes"] / GIB,
                    start_tib=item["start_bytes"] / TIB, end_tib=item["end_bytes"] / TIB,
                ))
            for item in batch.get("processes", []):
                month = datetime.fromisoformat(item["timestamp"]).strftime("%Y-%m")
                processes[month].append(dict(
                    item, delta_gib=item["delta_bytes"] / GIB,
                    scope="整机进程 I/O 参考，未归属到具体硬盘",
                ))
        for month, rows in samples.items():
            _upsert(root / f"write_log-{month}.xlsx", SAMPLE_COLUMNS, rows, ("record_id",))
        _upsert(root / "daily_summary.xlsx", DAILY_COLUMNS, days, ("date", "disk_id"), "sampled_at")
        _upsert(root / "trend_summary.xlsx", TREND_COLUMNS, days, ("date", "disk_id"),
                "sampled_at", _averages)
        for month, rows in processes.items():
            _upsert(root / f"process_activity-{month}.xlsx", PROCESS_COLUMNS, rows, ("record_id",))


def get_recent_daily(today, directory=None):
    """读取含今天在内的七天日汇总；只在后台调用，不修改工作簿。"""
    root = Path(directory) if directory is not None else EXPORT_DIR
    path = root / "daily_summary.xlsx"
    with EXPORT_LOCK:
        if not path.exists():
            return []
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = book.active
            if [cell.value for cell in sheet[1]] != [label for _, label in DAILY_COLUMNS]:
                raise ValueError("日汇总表头不匹配")
            cutoff, end = (today - timedelta(days=6)).isoformat(), today.isoformat()
            return [row for row in _records(sheet, DAILY_COLUMNS)
                    if cutoff <= row["date"] <= end]
        finally:
            book.close()


def get_recent_trend(disk_id=None, days=30, directory=None):
    root = Path(directory) if directory is not None else EXPORT_DIR
    path = root / "trend_summary.xlsx"
    if not path.exists():
        return []
    with EXPORT_LOCK:
        book = _open_book(path, TREND_COLUMNS)
        try:
            cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
            return [
                row for row in _records(book.active, TREND_COLUMNS)
                if row["date"] >= cutoff and (disk_id is None or row["disk_id"] == disk_id)
            ]
        finally:
            book.close()
