"""多盘识别、跨日统计、保存恢复与界面集成回归测试。"""
import copy
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import load_workbook

import config_manager
import csv_logger
import monitor_service
import smart_reader
from monitor_service import MonitorService, advance_state
from process_io_tracker import compute_delta


def config():
    result = copy.deepcopy(config_manager.DEFAULT_CONFIG)
    result["track_processes"] = False
    return result


def nvme(serial="SERIAL-A", units=100, model="NVMe SSD"):
    return {
        "model_name": model, "serial_number": serial,
        "device": {"protocol": "NVMe"},
        "user_capacity": {"bytes": 1_000_000_000_000},
        "nvme_smart_health_information_log": {
            "data_units_written": units, "temperature": 42, "percentage_used": 2,
        },
    }


class PatchedTestCase(unittest.TestCase):
    def enter_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result


class SmartReaderTests(unittest.TestCase):
    def test_nvme_units_and_zero(self):
        for units in (0, 1, 10**18):
            result = smart_reader.parse_smart(nvme(units=units), "/dev/sda", "nvme")
            self.assertEqual(result["total_bytes"], units * 512000)
            self.assertTrue(result["supported"])

    def test_identity_survives_path_change(self):
        first = smart_reader.parse_smart(nvme(), "/dev/sda", "nvme")
        moved = smart_reader.parse_smart(nvme(), "/dev/sdb", "nvme")
        other = smart_reader.parse_smart(nvme(serial="SERIAL-B"), "/dev/sda", "nvme")
        self.assertEqual(first["disk_id"], moved["disk_id"])
        self.assertNotEqual(first["disk_id"], other["disk_id"])

    def test_missing_identity_is_not_tracked(self):
        result = smart_reader.parse_smart(nvme(serial=""), "/dev/sda", "nvme")
        self.assertIsNone(result["disk_id"])
        self.assertFalse(result["supported"])

    def test_decimal_and_uncertain_sata_units(self):
        data = {"model_name": "SATA", "serial_number": "123", "rotation_rate": 0,
                "device": {"protocol": "ATA"}, "ata_smart_attributes": {"table": [
                    {"id": 241, "name": "Host_Writes_32MiB", "raw": {"value": 5}}
                ]}}
        result = smart_reader.parse_smart(data, "/dev/sda", "ata")
        self.assertEqual(result["total_bytes"], 5 * 32 * 1024**2)
        data["ata_smart_attributes"]["table"][0]["name"] = "Total_LBAs_Written"
        self.assertIsNone(smart_reader.parse_smart(data, "/dev/sda", "ata")["total_bytes"])

    def test_health_warning_exit_bits_do_not_discard_valid_data(self):
        data = nvme()
        data["smartctl"] = {"exit_status": 8}
        completed = type("Result", (), {"stdout": json.dumps(data), "returncode": 8})()
        with patch.object(smart_reader, "find_smartctl", return_value="smartctl"), \
                patch.object(smart_reader.subprocess, "run", return_value=completed):
            self.assertTrue(smart_reader.read_smart("/dev/sda", "nvme")["supported"])

    def test_device_open_error_is_reported(self):
        data = {"smartctl": {"exit_status": 2, "messages": [{"string": "Permission denied"}]}}
        completed = type("Result", (), {"stdout": json.dumps(data), "returncode": 2})()
        with patch.object(smart_reader, "find_smartctl", return_value="smartctl"), \
                patch.object(smart_reader.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(smart_reader.SmartError, "Permission denied"):
                smart_reader.read_smart("/dev/sda", "nvme")

    def test_volume_path_mapping(self):
        for path, number in (("/dev/sda", 0), ("/dev/sdz", 25), ("/dev/sdaa", 26),
                             (r"\\.\PhysicalDrive3", 3), ("/dev/pd12", 12), ("/dev/nvme0", None)):
            self.assertEqual(smart_reader.physical_disk_number(path), number)


class StatisticsTests(unittest.TestCase):
    def test_first_sample_and_same_day(self):
        at = datetime(2026, 10, 20, 10)
        state, sample = advance_state(None, 5000, at)
        self.assertEqual((state["today_bytes"], sample["delta_bytes"], state["readings"]), (0, 0, 1))
        state, sample = advance_state(state, 5300, at + timedelta(minutes=10))
        self.assertEqual((state["today_bytes"], sample["delta_bytes"], state["readings"]), (300, 300, 2))

    def test_multiday_gap_is_preserved_without_assigning_to_today(self):
        state, _ = advance_state(None, 1000, datetime(2026, 10, 20, 23, 50))
        state, sample = advance_state(state, 3000, datetime(2026, 10, 23, 9))
        self.assertEqual(state["today_bytes"], 0)
        self.assertEqual(state["unallocated_bytes"], 2000)
        self.assertEqual(sample["delta_bytes"], 2000)
        self.assertEqual(sample["interval_start"][:10], "2026-10-20")
        state, _ = advance_state(state, 3100, datetime(2026, 10, 23, 9, 10))
        self.assertEqual(state["today_bytes"], 100)
        self.assertEqual(state["unallocated_bytes"], 2000)

    def test_reset_counter_does_not_add_old_writes(self):
        at = datetime(2026, 10, 20, 10)
        state, _ = advance_state(None, 1000, at)
        state, _ = advance_state(state, 1500, at + timedelta(minutes=10))
        state, sample = advance_state(state, 5, at + timedelta(minutes=20))
        self.assertEqual(state["today_bytes"], 500)
        self.assertEqual(sample["delta_bytes"], 0)
        self.assertIn("回退", sample["note"])
        state, _ = advance_state(state, 105, at + timedelta(minutes=30))
        self.assertEqual(state["today_bytes"], 600)

    def test_clock_backward_keeps_baseline(self):
        state, _ = advance_state(None, 100, datetime(2026, 10, 20, 10))
        with self.assertRaises(ValueError):
            advance_state(state, 200, datetime(2026, 10, 20, 9))

    def test_reused_process_pid_is_excluded(self):
        old = {12: {"name": "old", "create_time": 1, "write_bytes": 100}}
        new = {12: {"name": "new", "create_time": 2, "write_bytes": 500}}
        self.assertEqual(compute_delta(old, new), [])


class ServiceTests(PatchedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.at = datetime(2026, 10, 20, 10)
        self.devices = {"/dev/sda": nvme(), "/dev/sdb": nvme(serial="SERIAL-B", units=200)}
        self.saved = None
        self.enter_patch(patch.object(monitor_service.LOG, "exception"))
        self.enter_patch(patch.object(monitor_service.LOG, "warning"))
        self.service = self.make_service(config())

    def reader(self, path, dtype):
        value = self.devices[path]
        if isinstance(value, Exception):
            raise value
        return smart_reader.parse_smart(value, path, dtype)

    def save(self, cfg, state):
        target = self.root / "config.json"
        config_manager._atomic_write_text(str(target), json.dumps(cfg))
        self.saved = copy.deepcopy(cfg)

    def make_service(self, cfg):
        return MonitorService(
            cfg, scanner=lambda: [{"device": path, "dtype": "nvme"} for path in self.devices],
            reader=self.reader, volumes=lambda: {0: ["C:", "D:"], 1: ["E:"]},
            saver=self.save, exporter=lambda batches: csv_logger.flush_batches(batches, self.root / "v2"),
            clock=lambda: self.at,
        )

    def advance(self, minutes=10):
        self.at += timedelta(minutes=minutes)

    def states(self):
        return self.service.config["_state"]["disks"]

    def read_book(self, filename, columns):
        book = load_workbook(self.root / "v2" / filename)
        try:
            return csv_logger._records(book.active, columns)
        finally:
            book.close()

    def test_two_disks_independent_and_new_disk_baseline(self):
        self.service.collect()
        self.advance()
        self.devices["/dev/sda"] = nvme(units=110)
        self.devices["/dev/sdb"] = nvme(serial="SERIAL-B", units=230)
        self.service.collect()
        self.assertEqual(sorted(s["today_bytes"] for s in self.states().values()), [10 * 512000, 30 * 512000])
        self.devices["/dev/sdc"] = nvme(serial="SERIAL-C", units=999999)
        self.advance()
        self.service.collect()
        new_id = smart_reader.parse_smart(self.devices["/dev/sdc"], "/dev/sdc", "nvme")["disk_id"]
        self.assertEqual(self.states()[new_id]["today_bytes"], 0)
        rows = self.read_book("trend_summary.xlsx", csv_logger.TREND_COLUMNS)
        self.assertEqual(len(rows), 3)

    def test_path_move_retains_alias_and_same_day_count(self):
        self.service.collect()
        disk_id = smart_reader.parse_smart(nvme(), "/dev/sda", "nvme")["disk_id"]
        self.service.update_disk(disk_id, alias="系统盘")
        self.devices["/dev/sdc"] = self.devices.pop("/dev/sda")
        self.devices["/dev/sdc"]["nvme_smart_health_information_log"]["data_units_written"] += 10
        self.advance()
        self.service.collect()
        disk = self.service.config["disks"][disk_id]
        self.assertEqual((disk["device"], disk["alias"]), ("/dev/sdc", "系统盘"))
        self.assertEqual(self.states()[disk_id]["today_bytes"], 10 * 512000)

    def test_replacement_at_same_path_gets_own_history(self):
        self.service.collect()
        old_id = smart_reader.parse_smart(nvme(), "/dev/sda", "nvme")["disk_id"]
        self.devices["/dev/sda"] = nvme(serial="NEW-SSD", units=1)
        self.advance()
        self.service.collect()
        self.assertEqual(len(self.states()), 3)
        self.assertEqual(self.service.config["disks"][old_id]["status"], "offline")
        new_id = smart_reader.parse_smart(self.devices["/dev/sda"], "/dev/sda", "nvme")["disk_id"]
        self.assertEqual(self.states()[new_id]["today_bytes"], 0)

    def test_one_read_failure_does_not_stop_another_disk(self):
        self.service.collect()
        self.devices["/dev/sda"] = RuntimeError("unplugged")
        self.devices["/dev/sdb"] = nvme(serial="SERIAL-B", units=210)
        self.advance()
        self.service.collect()
        self.assertEqual(sorted(s["readings"] for s in self.states().values()), [1, 2])
        self.assertEqual(sum(s["today_bytes"] for s in self.states().values()), 10 * 512000)

    def test_disabled_disk_does_not_log_samples(self):
        self.service.collect()
        disk_id = next(iter(self.states()))
        self.service.update_disk(disk_id, enabled=False)
        self.advance()
        self.service.collect()
        self.assertEqual(self.states()[disk_id]["readings"], 1)
        self.assertEqual(len(self.read_book("write_log-2026-10.xlsx", csv_logger.SAMPLE_COLUMNS)), 3)

    def test_duplicate_identity_is_not_counted(self):
        self.devices["/dev/sdb"] = nvme()
        self.service.collect()
        self.assertEqual(self.states(), {})
        self.assertIn("重复", next(iter(self.service.config["disks"].values()))["error"])

    def test_midnight_summary_uses_correct_date_and_preserves_zero_day(self):
        self.at = datetime(2026, 10, 20, 23, 50)
        self.service.collect()
        self.at = datetime(2026, 10, 23, 9)
        self.devices["/dev/sda"] = nvme(units=110)
        self.service.collect()
        rows = self.read_book("daily_summary.xlsx", csv_logger.DAILY_COLUMNS)
        self.assertEqual({r["date"] for r in rows}, {"2026-10-20", "2026-10-23"})
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["written_gib"] == 0 for r in rows))
        self.assertGreater(sum(r["unallocated_gib"] for r in rows), 0)

    def test_export_failure_survives_restart_and_replays_without_duplicates(self):
        real = self.service.exporter
        def fail_after_writes(batches):
            real(batches)
            raise PermissionError("simulate crash before acknowledgement")
        self.service.exporter = fail_after_writes
        self.service.collect()
        self.assertEqual(len(self.service.config["_state"]["pending_batches"]), 1)
        recovered = json.loads((self.root / "config.json").read_text())
        self.service = self.make_service(recovered)
        self.service.flush_pending()
        self.assertEqual(self.service.config["_state"]["pending_batches"], [])
        self.assertEqual(len(self.read_book("write_log-2026-10.xlsx", csv_logger.SAMPLE_COLUMNS)), 2)
        self.assertEqual(len(self.read_book("daily_summary.xlsx", csv_logger.DAILY_COLUMNS)), 2)

    def test_locked_workbook_keeps_queue_then_retries(self):
        original = csv_logger._safe_save
        def locked(book, path, **kwargs):
            if Path(path).name == "daily_summary.xlsx":
                raise PermissionError("Excel is open")
            original(book, path, **kwargs)
        with patch.object(csv_logger, "_safe_save", side_effect=locked):
            self.service.collect()
        self.assertEqual(len(self.service.config["_state"]["pending_batches"]), 1)
        self.service.flush_pending()
        self.assertEqual(self.service.snapshot()["pending"], 0)
        self.assertEqual(len(self.read_book("write_log-2026-10.xlsx", csv_logger.SAMPLE_COLUMNS)), 2)

    def test_failed_state_save_does_not_advance_memory_baseline(self):
        self.service.collect()
        before = copy.deepcopy(self.states())
        self.devices["/dev/sda"] = nvme(units=120)
        self.advance()
        with patch.object(self.service, "saver", side_effect=OSError("disk full")):
            self.service.collect()
        self.assertEqual(self.states(), before)
        self.advance()
        self.service.collect()
        self.assertEqual(sum(s["today_bytes"] for s in self.states().values()), 20 * 512000)

    def test_scan_failure_keeps_existing_records(self):
        self.service.collect()
        before = copy.deepcopy(self.states())
        with patch.object(self.service, "scanner", side_effect=RuntimeError("scan failed")):
            self.service.collect()
        self.assertEqual(self.states(), before)
        self.assertIn("scan failed", self.service.error)

    def test_formula_like_name_is_written_as_text(self):
        self.service.collect()
        disk_id = next(iter(self.states()))
        self.service.update_disk(disk_id, alias="=1+1")
        self.advance()
        self.service.collect()
        book = load_workbook(self.root / "v2" / "daily_summary.xlsx")
        try:
            cells = [row[2] for row in list(book.active.rows)[1:] if row[2].value == "=1+1"]
            self.assertTrue(cells)
            self.assertEqual(cells[0].data_type, "s")
        finally:
            book.close()

    def test_monthly_logs(self):
        self.service.collect()
        self.at = datetime(2026, 11, 1, 12)
        self.service.collect()
        self.assertTrue((self.root / "v2" / "write_log-2026-10.xlsx").exists())
        self.assertTrue((self.root / "v2" / "write_log-2026-11.xlsx").exists())



class ExportTests(unittest.TestCase):
    def test_average_uses_calendar_window_and_does_not_mix_disks(self):
        with tempfile.TemporaryDirectory() as tmp:
            def row(day, disk_id, gib):
                return {
                    "date": day, "disk_id": disk_id, "name": disk_id,
                    "written_bytes": gib * 1024**3, "unallocated_bytes": 0,
                    "start_bytes": 0, "end_bytes": 10**12, "readings": 3,
                    "sampled_at": day + "T12:00:00", "note": "",
                }
            days = [
                row("2026-10-01", "a", 999),
                row("2026-10-19", "a", 10), row("2026-10-20", "a", 20),
                row("2026-10-20", "b", 100),
            ]
            batch = {"id": "test", "samples": [], "days": days, "processes": []}
            csv_logger.flush_batches([batch], tmp)
            book = load_workbook(Path(tmp) / "trend_summary.xlsx")
            try:
                records = csv_logger._records(book.active, csv_logger.TREND_COLUMNS)
                recent = next(r for r in records if r["disk_id"] == "a" and r["date"] == "2026-10-20")
                self.assertEqual(recent["avg_7d_gib"], 15)
                self.assertEqual(recent["sample_days"], 2)
            finally:
                book.close()

    def test_older_pending_snapshot_cannot_overwrite_newer_daily_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = dict(date="2026-10-20", disk_id="a", name="a", written_bytes=200,
                       unallocated_bytes=0, start_bytes=1000, end_bytes=1200, readings=3,
                       sampled_at="2026-10-20T12:00:00", note="")
            latest = {"id": "latest", "samples": [], "days": [row]}
            older = {"id": "older", "samples": [], "days": [
                dict(row, written_bytes=100, sampled_at="2026-10-20T11:00:00")
            ]}
            csv_logger.flush_batches([latest], tmp)
            csv_logger.flush_batches([older], tmp)
            book = load_workbook(Path(tmp) / "daily_summary.xlsx")
            try:
                result = csv_logger._records(book.active, csv_logger.DAILY_COLUMNS)[0]
                self.assertAlmostEqual(result["written_gib"], 200 / 1024**3)
            finally:
                book.close()

    def test_bad_workbook_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daily_summary.xlsx"
            original = b"not a valid zip"
            path.write_bytes(original)
            with self.assertRaises(ValueError):
                csv_logger._open_book(path, csv_logger.DAILY_COLUMNS)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(path.with_suffix(".xlsx.bad").read_bytes(), original)

    def test_failed_replace_leaves_existing_workbook_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daily_summary.xlsx"
            book = csv_logger._open_book(path, csv_logger.DAILY_COLUMNS)
            csv_logger._safe_save(book, path)
            before = path.read_bytes()
            book.active.append(["new data"])
            with patch.object(csv_logger.os, "replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    csv_logger._safe_save(book, path, retries=1)
            book.close()
            self.assertEqual(path.read_bytes(), before)


class ConfigTests(PatchedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "config.json"
        self.enter_patch(patch.object(config_manager, "DATA_DIR", str(self.root)))
        self.enter_patch(patch.object(config_manager, "CONFIG_FILE", str(self.path)))

    def test_legacy_migration_backs_up_and_does_not_guess_identity(self):
        old = {"disk": r"\\.\PHYSICALDRIVE0", "interval_minutes": 10,
               "_state": {"last_total_bytes": 123, "today_bytes": 45}}
        original = json.dumps(old)
        self.path.write_text(original)
        (self.root / "write_log.xlsx").write_bytes(b"original workbook")
        updated = config_manager.load_config()
        backup = Path(updated["legacy"]["backup_directory"])
        self.assertEqual((backup / "config.json").read_text(), original)
        self.assertEqual((backup / "write_log.xlsx").read_bytes(), b"original workbook")
        self.assertEqual(updated["_state"]["disks"], {})
        self.assertEqual(updated["legacy"]["state"], old["_state"])
        self.assertEqual(config_manager.load_config(), updated)

    def test_missing_directory_first_run_and_mutable_defaults(self):
        first = config_manager.load_config()
        first["disks"]["changed"] = {}
        self.assertEqual(config_manager.load_config()["disks"], {})
        config_manager.save_config(config())
        self.assertTrue(self.path.exists())

    def test_atomic_failure_preserves_disk_and_memory(self):
        cfg = config()
        config_manager.save_config(cfg)
        before = self.path.read_bytes()
        with patch.object(config_manager.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                config_manager.save_state(cfg, {"disks": {"new": {}}, "pending_batches": []})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(cfg["_state"]["disks"], {})
        self.assertEqual(list(self.root.glob(".config-*.tmp")), [])

    def test_corrupt_config_is_preserved(self):
        self.path.write_text("broken json")
        updated = config_manager.load_config()
        self.assertEqual(updated["schema_version"], 2)
        self.assertEqual(next(self.root.glob("*.bad")).read_text(), "broken json")
        self.assertIn("recovery_notice", updated)

    def test_backup_failure_aborts_migration(self):
        original = json.dumps({"disk": "old", "_state": None})
        self.path.write_text(original)
        with patch.object(config_manager.shutil, "copy2", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                config_manager.load_config()
        self.assertEqual(self.path.read_text(), original)


class TooltipHealthTests(unittest.TestCase):
    def disk(self, name="系统盘", **changes):
        item = dict(alias=name, enabled=True, status="ok", today_bytes=3 * 1024**3,
                    health_percent=99, temperature=46, critical_warning=0,
                    state={"last_total_bytes": 30 * 1024**4})
        item.update(changes)
        return item

    def test_both_disks_have_individual_values(self):
        from tray_text import build_tooltip, utf16_length
        text = build_tooltip({"disks": [self.disk(), self.disk("游戏盘", health_percent=100)]})
        blocks = text.split("\n\n")
        self.assertEqual(len(blocks), 3)
        for block, name, health in zip(blocks[1:], ("系统盘", "游戏盘"), (99, 100)):
            lines = block.splitlines()
            self.assertEqual(lines[0], name)
            self.assertIn("今日：3.00GiB", lines)
            self.assertIn("累计：30.00TiB", lines)
            self.assertIn(f"健康：{health}%", lines)
            self.assertIn("温度：46℃", lines)
        self.assertLessEqual(utf16_length(text), 127)

    def test_many_disks_and_surrogate_pairs_stay_in_windows_limit(self):
        from tray_text import build_tooltip, utf16_length
        disks = [self.disk("游戏🎮盘" * 8 + str(n)) for n in range(12)]
        text = build_tooltip({"disks": disks, "pending": 100, "error": "failure"})
        self.assertLessEqual(utf16_length(text), 127)
        self.assertIn("双击查看", text)

    def test_offline_does_not_advertise_stale_temperature_and_health(self):
        from tray_text import build_tooltip
        text = build_tooltip({"disks": [self.disk(status="offline")]})
        self.assertIn("离线", text)
        self.assertNotIn("46℃", text)
        self.assertNotIn("健康：99%", text)

    def test_nvme_health_estimate_and_warnings(self):
        for used, expected in ((0, 100), (1, 99), (100, 0), (150, 0), (None, None)):
            data = nvme()
            data["nvme_smart_health_information_log"].update(percentage_used=used, critical_warning=4)
            parsed = smart_reader.parse_smart(data, "/dev/sda", "nvme")
            self.assertEqual(parsed["health_percent"], expected)
            self.assertIn("可靠性", parsed["health_warning"])

    def test_windows_ui_displays_health_and_management_controls(self):
        if os.name != "nt":
            self.skipTest("Windows UI")
        import tkinter as tk
        from disk_monitor import MonitorApp
        service = MonitorService(config(), scanner=lambda: [{"device": "/dev/sda", "dtype": "nvme"}],
                                 reader=lambda *args: smart_reader.parse_smart(nvme(), *args),
                                 volumes=lambda: {0: ["C:", "D:"]}, exporter=lambda batches: None,
                                 saver=lambda cfg, state: None)
        service.collect()
        root = tk.Tk()
        root.withdraw()
        try:
            app = MonitorApp(root, service)
            app.refresh()
            root.update_idletasks()
            self.assertEqual(len(app.table.get_children()), 1)
            row = app.table.item(app.table.get_children()[0], "values")
            self.assertIn("98%", row)
            self.assertIn("C:, D:", row)
            menu = app.build_menu(service.snapshot())
            self.assertTrue(list(menu.items))
        finally:
            root.destroy()


class UpgradeLockTests(unittest.TestCase):
    def test_frozen_build_recognizes_legacy_python_process(self):
        if os.name != "nt":
            self.skipTest("Windows locking")
        from unittest.mock import Mock
        from disk_monitor import SingleInstance
        import disk_monitor
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "data").mkdir()
            lock = root / "data" / "ssd_monitor.lock"
            lock.write_text("987654")
            process = Mock()
            process.cmdline.return_value = ["pythonw.exe", str(root / "disk_monitor.py")]
            process.cwd.return_value = str(root)
            process.exe.return_value = "pythonw.exe"
            with patch.object(disk_monitor.psutil, "Process", return_value=process), \
                    patch.object(disk_monitor.sys, "frozen", True, create=True):
                self.assertFalse(SingleInstance(str(lock)).acquire())
            self.assertEqual(lock.read_text(), "987654")


if __name__ == "__main__":
    unittest.main()
