"""多硬盘采集与统计。UI 只操作此服务，不直接读写工作簿。"""
import copy
from datetime import datetime
import hashlib
import logging
import threading
import uuid

from config_manager import save_state
from smart_reader import scan_disks, read_smart, volume_map, physical_disk_number
from process_io_tracker import snapshot as io_snapshot, compute_delta
from csv_logger import flush_batches

LOG = logging.getLogger(__name__)


def daily_record(disk_id, disk, state):
    return {
        "disk_id": disk_id, "name": disk.get("alias") or disk["model"],
        "date": state["last_date"], "written_bytes": state["today_bytes"],
        "unallocated_bytes": state.get("unallocated_bytes", 0),
        "start_bytes": state["day_start_bytes"], "end_bytes": state["last_total_bytes"],
        "readings": state["readings"], "sampled_at": state["last_sample_at"],
        "note": state.get("day_note", ""),
    }


def advance_state(previous, total_bytes, now):
    """跨日区间不能精确拆分，单独保存；绝不把未知几天的写入全记为今天。"""
    stamp = now.isoformat(timespec="microseconds")
    day = now.date().isoformat()
    old = copy.deepcopy(previous) if previous else None
    if old and stamp < old["last_sample_at"]:
        raise ValueError("系统时间早于上次采样，请检查系统时钟")
    state = copy.deepcopy(old) if old else {
        "today_bytes": 0, "unallocated_bytes": 0, "readings": 0,
        "day_start_bytes": total_bytes, "day_note": "首次采样，从此刻开始统计",
    }
    delta = 0 if not old else total_bytes - old["last_total_bytes"]
    note = ""
    unallocated = 0
    if old and day != old["last_date"]:
        state.update(today_bytes=0, unallocated_bytes=0, readings=0,
                     day_start_bytes=total_bytes, day_note="含跨日采样缺口，未知部分未计入当日")
        if delta >= 0:
            unallocated = delta
            note = "跨日区间写入，无法准确归属到某一天"
    if delta < 0:
        delta = 0
        note = "累计计数回退，重新建立基线"
        state["day_note"] = "计数回退，当日统计可能不完整"
    elif old and day == old["last_date"]:
        state["today_bytes"] += delta
    elif not old:
        note = "首次采样基线"
    state["unallocated_bytes"] += unallocated
    state.update(last_total_bytes=total_bytes, last_date=day, last_sample_at=stamp,
                 readings=state["readings"] + 1)
    return state, {
        "timestamp": stamp, "interval_start": old["last_sample_at"] if old else stamp,
        "total_bytes": total_bytes, "delta_bytes": delta,
        "today_bytes": state["today_bytes"], "unallocated_bytes": unallocated, "note": note,
    }


class MonitorService:
    def __init__(self, config, scanner=scan_disks, reader=read_smart, volumes=volume_map,
                 exporter=flush_batches, saver=save_state, clock=datetime.now):
        self.config = copy.deepcopy(config)
        for disk in self.config["disks"].values():
            disk.update(status="pending", online=False)
        self.scanner, self.reader, self.volumes = scanner, reader, volumes
        self.exporter, self.saver, self.clock = exporter, saver, clock
        self.lock = threading.RLock()
        self.collect_lock = threading.Lock()
        self.last_io = None
        self.process_sources = []
        self.error = ""
        self.export_error = ""
        self.collecting = False

    def _commit(self, updated):
        self.saver(updated, updated["_state"])
        self.config = updated

    def set_option(self, key, value):
        if key not in ("interval_minutes", "auto_start", "track_processes"):
            raise ValueError("未知设置")
        if key == "interval_minutes" and value not in (5, 10, 30, 60):
            raise ValueError("无效采集间隔")
        with self.lock:
            updated = copy.deepcopy(self.config)
            updated[key] = value
            self._commit(updated)

    def update_disk(self, disk_id, alias=None, enabled=None):
        with self.lock:
            updated = copy.deepcopy(self.config)
            disk = updated["disks"][disk_id]
            if alias is not None:
                alias = alias.strip()
                if not alias or len(alias) > 60 or any(ord(char) < 32 for char in alias):
                    raise ValueError("名称须为 1–60 个可见字符")
                disk["alias"] = alias
            if enabled is not None:
                if enabled and not disk.get("supported"):
                    raise ValueError(disk.get("error") or "此硬盘暂不支持写入量统计")
                disk["enabled"] = bool(enabled)
            self._commit(updated)

    def snapshot(self):
        with self.lock:
            cfg = copy.deepcopy(self.config)
            day = self.clock().date().isoformat()
            rows = []
            for disk_id, disk in cfg["disks"].items():
                state = cfg["_state"]["disks"].get(disk_id, {})
                rows.append(dict(
                    disk, disk_id=disk_id, state=state,
                    today_bytes=state.get("today_bytes", 0) if state.get("last_date") == day else 0,
                    today_unallocated=state.get("unallocated_bytes", 0) if state.get("last_date") == day else 0,
                ))
            return {
                "disks": rows, "interval_minutes": cfg["interval_minutes"],
                "track_processes": cfg["track_processes"], "auto_start": cfg["auto_start"],
                "pending": len(cfg["_state"]["pending_batches"]),
                "legacy": cfg.get("legacy"), "recovery_notice": cfg.get("recovery_notice", ""),
                "error": self.error, "export_error": self.export_error,
                "collecting": self.collecting, "process_sources": copy.deepcopy(self.process_sources),
            }

    def flush_pending(self):
        with self.lock:
            pending = copy.deepcopy(self.config["_state"]["pending_batches"])
        if not pending:
            self.export_error = ""
            return
        try:
            self.exporter(pending)
            completed = {batch["id"] for batch in pending}
            with self.lock:
                updated = copy.deepcopy(self.config)
                updated["_state"]["pending_batches"] = [
                    batch for batch in updated["_state"]["pending_batches"]
                    if batch["id"] not in completed
                ]
                self._commit(updated)
            self.export_error = ""
        except Exception as exc:
            # 任何工作簿失败都保留整批；重放时按记录 ID / 日期+硬盘去重。
            self.export_error = f"日志待补写：{exc}"
            LOG.exception("日志保存失败，待写数据仍保存在配置中")

    def collect(self):
        with self.collect_lock:
            self.collecting = True
            try:
                self._collect()
            except Exception as exc:
                self.error = f"采集失败：{exc}"
                LOG.exception("采集失败")
                # 扫描失败时仍尝试补写上一轮日志。
                self.flush_pending()
            finally:
                self.collecting = False

    def _collect(self):
        devices = self.scanner()
        try:
            letters = self.volumes()
        except Exception:
            LOG.exception("盘符映射失败")
            letters = {}
        results = []
        for device in devices:
            try:
                data = self.reader(device["device"], device["dtype"])
                data["volumes"] = letters.get(physical_disk_number(device["device"]), [])
                results.append((device, data, None, self.clock()))
            except Exception as exc:
                results.append((device, None, str(exc), self.clock()))
                LOG.warning("读取 %s 失败：%s", device["device"], exc)

        counts = {}
        for _, data, _, _ in results:
            if data and data.get("disk_id"):
                counts[data["disk_id"]] = counts.get(data["disk_id"], 0) + 1

        with self.lock:
            tracking = self.config["track_processes"]
        process_rows = []
        if tracking:
            try:
                current = io_snapshot()
                sources = compute_delta(self.last_io, current, top_n=5) if self.last_io is not None else []
                self.last_io = current
                self.process_sources = sources
                stamp = self.clock().isoformat(timespec="microseconds")
                process_rows = [
                    dict(source, timestamp=stamp, record_id=uuid.uuid4().hex) for source in sources
                ]
            except Exception:
                LOG.exception("进程采样失败，硬盘采样继续")
        else:
            self.last_io = None
            self.process_sources = []

        batch = {"id": uuid.uuid4().hex, "samples": [], "days": [], "processes": process_rows}
        with self.lock:
            updated = copy.deepcopy(self.config)
            disks = updated["disks"]
            states = updated["_state"]["disks"]
            for disk in disks.values():
                disk.update(status="offline", error="本轮未发现设备", online=False)
            for device, data, error, sampled_at in results:
                path = device["device"]
                if data is None:
                    matching = [disk for disk in disks.values() if disk.get("device") == path]
                    if not matching:
                        key = "unidentified-" + hashlib.sha256(path.encode()).hexdigest()[:16]
                        disks[key] = dict(device, alias=path, model="读取失败",
                                          enabled=False, supported=False)
                        matching = [disks[key]]
                    for disk in matching:
                        disk.update(status="error", error=error, online=True)
                    continue
                disk_id = data["disk_id"]
                if disk_id is None:
                    disk_id = "unidentified-" + hashlib.sha256(path.encode()).hexdigest()[:16]
                # 识别成功后移除该路径上没有历史的临时错误项。
                for key in list(disks):
                    if key != disk_id and key.startswith("unidentified-") and disks[key].get("device") == path:
                        del disks[key]
                disk = disks.setdefault(disk_id, {
                    "alias": data["model"] + (" · " + data["serial"][-6:] if data["serial"] else ""),
                    "enabled": data["supported"],
                })
                disk.update(data)
                disk.update(online=True, last_seen=sampled_at.isoformat(timespec="seconds"))
                if counts.get(disk_id, 0) > 1:
                    disk.update(status="error", error="发现重复设备身份，暂缓累计以免混盘")
                    continue
                if not data["supported"]:
                    disk["status"] = "unsupported"
                    continue
                if not disk["enabled"]:
                    disk["status"] = "disabled"
                    continue
                previous = states.get(disk_id)
                try:
                    state, sample = advance_state(previous, data["total_bytes"], sampled_at)
                except ValueError as exc:
                    disk.update(status="error", error=str(exc))
                    continue
                if previous and previous["last_date"] != state["last_date"]:
                    batch["days"].append(daily_record(disk_id, disk, previous))
                states[disk_id] = state
                disk.update(status="warning" if disk.get("critical_warning") else "ok", error="")
                batch["samples"].append(dict(
                    sample, record_id=uuid.uuid4().hex, disk_id=disk_id,
                    name=disk["alias"], model=disk["model"], serial=disk["serial"],
                    device=path, temperature=disk["temperature"],
                    health_percent=disk.get("health_percent"),
                    percentage_used=disk.get("percentage_used"),
                    health_warning=disk.get("health_warning", ""),
                ))
                batch["days"].append(daily_record(disk_id, disk, state))
            if batch["samples"] or batch["days"] or batch["processes"]:
                updated["_state"]["pending_batches"].append(batch)
            # 数据与待写队列同一次提交；失败时内存基线也不向前推进。
            self._commit(updated)
        self.error = "" if devices else "未发现可访问的硬盘，请检查 smartctl 和读取权限"
        self.flush_pending()
