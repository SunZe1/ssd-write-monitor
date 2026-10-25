"""Windows 多 SSD 写入监控：托盘、硬盘管理和采集调度。"""
import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from PIL import Image, ImageDraw
import psutil
import pystray
from pystray import MenuItem as Item

from auto_start import is_auto_start_enabled, set_auto_start
from config_manager import load_config, DATA_DIR, ensure_dirs
from csv_logger import get_recent_trend
from monitor_service import MonitorService
from tray_text import build_tooltip

LOG = logging.getLogger(__name__)
GIB = 1024 ** 3
TIB = 1024 ** 4
STATUS = {
    "ok": "正常", "offline": "未连接", "error": "读取失败",
    "unsupported": "暂不支持", "disabled": "已停用", "pending": "等待检测",
    "warning": "健康告警",
}


def create_icon_image():
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 12, 56, 52), radius=7, fill="#3498db", outline="#2980b9", width=3)
    draw.text((18, 23), "SSD", fill="white")
    draw.ellipse((45, 41, 50, 46), fill="#b5ffc8")
    return image


class SingleInstance:
    """操作系统文件锁；同时识别没有持锁的旧版监控进程。"""
    def __init__(self, path):
        self.path = path
        self.stream = None

    def acquire(self):
        import msvcrt
        stream = open(self.path, "a+b")
        try:
            stream.seek(0)
            pid_text = stream.read().decode("ascii", errors="ignore").strip()
            if pid_text.isdigit() and int(pid_text) != os.getpid():
                try:
                    process = psutil.Process(int(pid_text))
                    args = process.cmdline()
                    script_paths = {
                        os.path.normcase(os.path.abspath(__file__)),
                        os.path.normcase(os.path.abspath(os.path.join(
                            os.path.dirname(os.path.dirname(self.path)), "disk_monitor.py"
                        ))),
                    }
                    same_script = any(
                        os.path.normcase(os.path.abspath(os.path.join(process.cwd(), arg))) in script_paths
                        for arg in args[1:]
                    )
                    same_exe = getattr(sys, "frozen", False) and os.path.normcase(process.exe()) == os.path.normcase(sys.executable)
                    if same_script or same_exe:
                        stream.close()
                        return False
                except (psutil.Error, OSError):
                    pass
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            stream.close()
            return False
        stream.seek(0)
        stream.truncate()
        stream.write(str(os.getpid()).encode("ascii"))
        stream.flush()
        self.stream = stream
        return True

    def close(self):
        if self.stream:
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            self.stream.close()
            self.stream = None


class MonitorApp:
    def __init__(self, root, service):
        self.root, self.service = root, service
        self.actions = queue.Queue()
        self.stop_event = threading.Event()
        self.request_event = threading.Event()
        self.paused = threading.Event()
        self.worker = None
        self.icon = None
        self.menu_signature = None
        self.row_data = {}
        self.closing = False
        self.root.title("SSD 写入监控 · 硬盘管理")
        self.root.geometry("1120x580")
        self.root.minsize(900, 460)
        self.root.protocol("WM_DELETE_WINDOW", self.root.withdraw)
        self._build_window()
        self.root.withdraw()

    def _build_window(self):
        panel = ttk.Frame(self.root, padding=16)
        panel.pack(fill="both", expand=True)
        self.summary = ttk.Label(panel, text="正在扫描固态硬盘…", font=("Microsoft YaHei UI", 12, "bold"))
        self.summary.pack(anchor="w")
        self.notice = ttk.Label(panel, text="", wraplength=1060, foreground="#8a4b00")
        self.notice.pack(anchor="w", pady=(6, 10))
        controls = ttk.Frame(panel)
        controls.pack(fill="x", pady=(0, 10))
        for label, action in (
            ("立即采集 / 重新扫描", self.request_collect),
            ("启用 / 停用所选", self.toggle_selected),
            ("重命名", self.rename_selected),
            ("查看所选盘趋势", self.show_history),
            ("打开日志目录", self.open_logs),
        ):
            ttk.Button(controls, text=label, command=lambda fn=action: self.invoke(fn)).pack(side="left", padx=(0, 8))
        columns = ("name", "volumes", "capacity", "today", "total", "temperature", "health", "status", "last")
        table_frame = ttk.Frame(panel)
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = (
            ("名称", 210), ("盘符", 75), ("容量", 85), ("今日已确认 GiB", 125),
            ("累计 TiB", 100), ("温度 ℃", 75), ("健康度估计", 90), ("状态", 105), ("最后成功采样", 165),
        )
        for column, (label, width) in zip(columns, headings):
            self.table.heading(column, text=label)
            self.table.column(column, width=width, minwidth=60, stretch=column == "name")
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.table.tag_configure("error", foreground="#b42318")
        self.table.tag_configure("warning", foreground="#b35b00")
        self.table.tag_configure("offline", foreground="#777777")
        self.table.bind("<<TreeviewSelect>>", lambda event: self.update_details())
        self.detail = ttk.Label(panel, text="选择硬盘查看型号、序列号和采样详情。", wraplength=1060)
        self.detail.pack(anchor="w", pady=(12, 4))
        self.process_label = ttk.Label(panel, text="", wraplength=1060, foreground="#555555")
        self.process_label.pack(anchor="w", pady=(4, 0))
        ttk.Label(
            panel, text="GiB / TiB 使用 1024 进制。跨日区间的写入单独保留，不强行分配到某一天。",
            foreground="#666666",
        ).pack(anchor="w", pady=(8, 0))

    def invoke(self, action):
        try:
            action()
        except Exception as exc:
            LOG.exception("界面操作失败")
            messagebox.showerror("操作失败", str(exc), parent=self.root)

    def queued(self, action):
        def callback(icon, item):
            self.actions.put(action)
        return callback

    def request_collect(self):
        self.request_event.set()
        self.notice.configure(text="已请求采集和扫描，正在处理…")

    def show_window(self):
        self.refresh()
        self.root.deiconify()
        self.root.lift()

    def open_logs(self):
        os.startfile(DATA_DIR)

    def selected(self):
        selection = self.table.selection()
        if not selection:
            messagebox.showinfo("选择硬盘", "请先选择一块硬盘。", parent=self.root)
            return None
        return selection[0]

    def toggle_disk(self, disk_id):
        disk = next(row for row in self.service.snapshot()["disks"] if row["disk_id"] == disk_id)
        self.service.update_disk(disk_id, enabled=not disk["enabled"])
        self.request_collect()
        self.refresh()

    def toggle_selected(self):
        disk_id = self.selected()
        if disk_id:
            self.toggle_disk(disk_id)

    def rename_selected(self):
        disk_id = self.selected()
        if not disk_id:
            return
        disk = self.row_data[disk_id]
        alias = simpledialog.askstring("硬盘名称", "例如：系统盘、游戏盘、资料盘",
                                       initialvalue=disk["alias"], parent=self.root)
        if alias is not None:
            self.service.update_disk(disk_id, alias=alias)
            self.refresh()

    def toggle_pause(self):
        if self.paused.is_set():
            self.paused.clear()
            self.request_collect()
        else:
            self.paused.set()
        self.refresh()

    def set_interval(self, minutes):
        self.service.set_option("interval_minutes", minutes)
        if not self.paused.is_set():
            self.request_collect()

    def toggle_tracking(self):
        self.service.set_option("track_processes", not self.service.snapshot()["track_processes"])
        self.refresh()

    def toggle_autostart(self):
        enabled = not is_auto_start_enabled()
        if not set_auto_start(enabled):
            raise RuntimeError("开机自启设置失败，请查看运行日志")
        self.service.set_option("auto_start", enabled)

    def _disk_menu_item(self, disk):
        disk_id = disk["disk_id"]
        def checked(item):
            return any(row["disk_id"] == disk_id and row["enabled"] for row in self.service.snapshot()["disks"])
        return Item(
            disk["alias"], self.queued(lambda: self.toggle_disk(disk_id)),
            checked=checked, enabled=bool(disk.get("supported")),
        )

    def _interval_item(self, minutes):
        return Item(
            f"{minutes} 分钟", self.queued(lambda: self.set_interval(minutes)),
            checked=lambda item: self.service.snapshot()["interval_minutes"] == minutes, radio=True,
        )

    def build_menu(self, snapshot):
        disk_items = [self._disk_menu_item(disk) for disk in snapshot["disks"]]
        if not disk_items:
            disk_items = [Item("尚未发现硬盘", None, enabled=False)]
        return pystray.Menu(
            Item("硬盘管理 / 今日统计", self.queued(self.show_window), default=True),
            Item("立即采集 / 重新扫描", self.queued(self.request_collect)),
            Item("监控硬盘", pystray.Menu(*disk_items)),
            Item("打开日志目录", self.queued(self.open_logs)),
            Item(lambda item: "恢复监控" if self.paused.is_set() else "暂停监控",
                 self.queued(self.toggle_pause)),
            Item("采集间隔", pystray.Menu(*(self._interval_item(n) for n in (5, 10, 30, 60)))),
            Item("整机进程 I/O 参考", self.queued(self.toggle_tracking),
                 checked=lambda item: self.service.snapshot()["track_processes"]),
            Item("开机自启", self.queued(self.toggle_autostart),
                 checked=lambda item: is_auto_start_enabled()),
            Item("退出", self.queued(self.exit)),
        )

    def update_details(self):
        selected = self.table.selection()
        disk = self.row_data.get(selected[0]) if selected else None
        if not disk:
            return
        state = disk.get("state", {})
        text = (
            f"型号：{disk.get('model', '未知')}　序列号：{disk.get('serial') or '未提供'}　"
            f"协议：{disk.get('protocol', '未知')}　路径：{disk.get('device', '')}\n"
            f"本日发现的未分配写入：{disk.get('today_unallocated', 0) / GIB:.4f} GiB　"
            f"通电：{disk.get('power_on_hours') if disk.get('power_on_hours') is not None else '—'} 小时　"
            f"寿命已用：{disk.get('percentage_used') if disk.get('percentage_used') is not None else '—'}%\n"
            f"健康度按 100% − NVMe 寿命已用百分比估算。"
            f"{disk.get('health_warning') or disk.get('error') or state.get('day_note', '')}"
        )
        self.detail.configure(text=text)

    def refresh(self):
        snapshot = self.service.snapshot()
        enabled = [disk for disk in snapshot["disks"] if disk.get("enabled")]
        total = sum(disk["today_bytes"] for disk in enabled)
        gap = sum(disk["today_unallocated"] for disk in enabled)
        online = sum(disk.get("status") == "ok" for disk in enabled)
        mode = "已暂停" if self.paused.is_set() else ("采集中" if snapshot["collecting"] else "监控中")
        self.summary.configure(text=f"{mode}　今日已确认：{total / GIB:.3f} GiB　正常 {online} / 启用 {len(enabled)} 块")
        notices = [snapshot["error"], snapshot["export_error"], snapshot["recovery_notice"]]
        if snapshot["pending"]:
            notices.append(f"{snapshot['pending']} 批数据已持久保存，等待写入 Excel。")
        if gap:
            notices.append(f"本日发现跨日未分配写入 {gap / GIB:.4f} GiB，详见明细日志。")
        if snapshot["legacy"]:
            notices.append("旧版记录已备份并保留，因缺少硬盘身份未自动绑定。")
        self.notice.configure(text="  ".join(text for text in notices if text))
        ids = {disk["disk_id"] for disk in snapshot["disks"]}
        for existing in self.table.get_children():
            if existing not in ids:
                self.table.delete(existing)
        self.row_data = {disk["disk_id"]: disk for disk in snapshot["disks"]}
        for disk_id, disk in self.row_data.items():
            state = disk.get("state", {})
            total_bytes = state.get("last_total_bytes", disk.get("total_bytes"))
            capacity = disk.get("capacity_bytes")
            last = state.get("last_sample_at", "")
            temperature = disk.get("temperature")
            status = STATUS.get(disk.get("status"), "等待检测")
            if not disk.get("enabled"):
                status = "已停用" if disk.get("supported") else status
            values = (
                disk["alias"], ", ".join(disk.get("volumes", [])),
                f"{capacity / 10**9:.0f} GB" if capacity else "—",
                f"{disk['today_bytes'] / GIB:.3f}" if state else "—",
                f"{total_bytes / TIB:.3f}" if total_bytes is not None else "—",
                temperature if temperature is not None else "—",
                f"{disk['health_percent']}%" if disk.get("health_percent") is not None else "—", status,
                last[:19].replace("T", " ") if last else "—",
            )
            if self.table.exists(disk_id):
                self.table.item(disk_id, values=values, tags=(disk.get("status", ""),))
            else:
                self.table.insert("", "end", iid=disk_id, values=values, tags=(disk.get("status", ""),))
        if not self.table.selection() and self.table.get_children():
            self.table.selection_set(self.table.get_children()[0])
        self.update_details()
        sources = snapshot["process_sources"]
        self.process_label.configure(text="整机进程 I/O 参考（未归属具体硬盘）： " + (
            "；".join(f"{row['name']} {row['delta_bytes'] / GIB:.3f} GiB" for row in sources)
            if sources else "暂无区间数据"
        ))
        if self.icon:
            self.icon.title = build_tooltip(snapshot, self.paused.is_set())
            signature = tuple((disk["disk_id"], disk["alias"], disk["enabled"], disk.get("supported")) for disk in snapshot["disks"])
            if signature != self.menu_signature:
                self.icon.menu = self.build_menu(snapshot)
                self.menu_signature = signature
            self.icon.update_menu()

    def show_history(self):
        disk_id = self.selected()
        if not disk_id:
            return
        name = self.row_data[disk_id]["alias"]
        self.notice.configure(text="正在读取最近 90 天趋势…")
        def read():
            try:
                rows = get_recent_trend(disk_id, days=90)
                self.actions.put(lambda: self._show_history_rows(name, rows))
            except Exception as exc:
                message = str(exc)
                self.actions.put(lambda: messagebox.showerror("读取趋势失败", message, parent=self.root))
        threading.Thread(target=read, daemon=True, name="history-reader").start()

    def _show_history_rows(self, name, rows):
        window = tk.Toplevel(self.root)
        window.title(f"{name} · 最近 90 天")
        window.geometry("850x440")
        ttk.Label(window, text="日均仅使用最近 7 个自然日内有采样的日期；未分配量不计入日均。",
                  padding=12).pack(anchor="w")
        columns = ("date", "written_gib", "unallocated_gib", "avg_7d_gib", "sample_days")
        table = ttk.Treeview(window, columns=columns, show="headings")
        for column, title in zip(columns, ("日期", "已确认 GiB", "未分配 GiB", "近7日采样日均 GiB", "采样天数")):
            table.heading(column, text=title)
            table.column(column, width=155, anchor="center")
        scrollbar = ttk.Scrollbar(window, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        table.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        for row in sorted(rows, key=lambda item: item["date"], reverse=True):
            table.insert("", "end", values=[
                f"{row[key]:.4f}" if isinstance(row[key], float) else row[key] for key in columns
            ])
        if not rows:
            ttk.Label(window, text="暂无已写入工作簿的趋势记录。", padding=12).pack()

    def _worker_loop(self):
        next_collect = 0
        next_retry = time.monotonic() + 30
        while not self.stop_event.is_set():
            now = time.monotonic()
            requested = self.request_event.is_set()
            if requested or (not self.paused.is_set() and now >= next_collect):
                self.request_event.clear()
                self.service.collect()
                next_collect = time.monotonic() + self.service.snapshot()["interval_minutes"] * 60
                next_retry = time.monotonic() + 30
            elif now >= next_retry:
                self.service.flush_pending()
                next_retry = time.monotonic() + 30
            self.request_event.wait(timeout=1)

    def _pump(self):
        if self.closing:
            return
        while True:
            try:
                action = self.actions.get_nowait()
            except queue.Empty:
                break
            self.invoke(action)
            if self.closing:
                return
        self.root.after(150, self._pump)

    def _refresh_loop(self):
        if not self.closing:
            self.invoke(self.refresh)
            self.root.after(3000, self._refresh_loop)

    def start(self):
        self.icon = pystray.Icon("SSDMonitor", create_icon_image(), "SSD 写入监控",
                                 self.build_menu(self.service.snapshot()))
        # Windows 托盘消息循环与 Tk 主线程分离；所有 GUI 操作通过队列回主线程。
        threading.Thread(target=self.icon.run, daemon=True, name="tray").start()
        self.worker = threading.Thread(target=self._worker_loop, name="ssd-collector")
        self.worker.start()
        self._pump()
        self._refresh_loop()

    def exit(self):
        self.closing = True
        self.stop_event.set()
        self.request_event.set()
        self.summary.configure(text="正在完成当前采集并退出…")
        if self.icon:
            self.icon.stop()
        self._wait_for_worker()

    def _wait_for_worker(self):
        if self.worker and self.worker.is_alive():
            self.root.after(100, self._wait_for_worker)
        else:
            self.root.destroy()


def main():
    ensure_dirs()
    instance = SingleInstance(os.path.join(DATA_DIR, "ssd_monitor.lock"))
    if not instance.acquire():
        return
    handler = RotatingFileHandler(os.path.join(DATA_DIR, "monitor.log"), maxBytes=2_000_000,
                                  backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = None
    app = None
    try:
        root = tk.Tk()
        root.withdraw()
        config = load_config()
        if config.get("auto_start") and not is_auto_start_enabled():
            if not set_auto_start(True):
                LOG.warning("自动启动设置未能恢复")
        app = MonitorApp(root, MonitorService(config))
        app.start()
        root.mainloop()
    except Exception as exc:
        LOG.exception("程序启动或运行失败")
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, str(exc), "SSD 监控启动失败", 0x10)
    finally:
        if app:
            app.stop_event.set()
            app.request_event.set()
            if app.worker:
                app.worker.join()
            if app.icon:
                app.icon.stop()
        if root:
            try:
                root.destroy()
            except tk.TclError:
                pass
        instance.close()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from diagnostics import run_self_test
        sys.exit(run_self_test(sys.argv[2]))
    main()
