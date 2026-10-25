"""打包后自检：只使用临时数据目录，不修改正在使用的配置和日志。"""
import copy
import json
import logging
from pathlib import Path
import tempfile
import threading
import tkinter as tk

from config_manager import DEFAULT_CONFIG, _atomic_write_text
from csv_logger import flush_batches
from monitor_service import MonitorService
from smart_reader import find_smartctl
from tray_text import build_tooltip


def run_self_test(report_path):
    report = {"ok": False}
    root = None
    icon = None
    tray_thread = None
    try:
        from disk_monitor import MonitorApp, create_icon_image
        import pystray
        with tempfile.TemporaryDirectory(prefix="ssd-monitor-selftest-") as folder:
            base = Path(folder)
            cfg = copy.deepcopy(DEFAULT_CONFIG)
            cfg["track_processes"] = False
            def save(updated, state):
                _atomic_write_text(str(base / "config.json"), json.dumps(updated))
            service = MonitorService(
                cfg, saver=save, exporter=lambda batches: flush_batches(batches, base / "logs"),
            )
            service.collect()
            snapshot = service.snapshot()
            if snapshot["error"] or snapshot["export_error"]:
                raise RuntimeError(snapshot["error"] or snapshot["export_error"])
            readable = [disk for disk in snapshot["disks"] if disk.get("supported")]
            if not readable:
                raise RuntimeError("本机未发现可读取的 SSD；打包组件已加载，请检查设备权限")
            root = tk.Tk()
            root.withdraw()
            app = MonitorApp(root, service)
            app.refresh()
            root.update_idletasks()
            report["ui_rows"] = len(app.table.get_children())
            ready = threading.Event()
            icon = pystray.Icon("SSDMonitorSelfTest", create_icon_image(),
                                build_tooltip(snapshot), app.build_menu(snapshot))
            # 建立真实 Windows 托盘后端但不显示图标，不干扰现有监控。
            tray_thread = threading.Thread(target=lambda: icon.run(setup=lambda _: ready.set()), daemon=True)
            tray_thread.start()
            if not ready.wait(10):
                raise RuntimeError("托盘后端未能初始化")
            report.update(
                ok=True, smartctl=find_smartctl(), tooltip=build_tooltip(snapshot),
                workbooks=sorted(path.name for path in (base / "logs").glob("*.xlsx")),
                disks=[{key: disk.get(key) for key in (
                    "model", "protocol", "volumes", "health_percent", "temperature", "supported",
                )} for disk in snapshot["disks"]],
                pending=snapshot["pending"],
            )
    except Exception as exc:
        report["error"] = str(exc)
        logging.exception("自检失败")
    finally:
        if icon:
            icon.stop()
        if tray_thread:
            tray_thread.join(timeout=10)
        if root:
            root.destroy()
        _atomic_write_text(str(report_path), json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1
