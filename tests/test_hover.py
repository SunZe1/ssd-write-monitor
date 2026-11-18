from datetime import date
import tkinter as tk
import unittest
from ctypes import wintypes
from unittest.mock import Mock, patch

from tray_popup import TrayPopup, HoverIcon, LABELS, disk_values, popup_position
from weekly_stats import GIB, weekly_totals
import config_manager
from monitor_service import MonitorService


def disk(name="硬盘一", **changes):
    result = dict(alias=name, enabled=True, status="ok", today_bytes=3 * GIB,
                  week_bytes=21 * GIB, week_days=7, health_percent=99,
                  state={"last_total_bytes": 30 * 1024**4})
    result.update(changes)
    return result


class WeeklyTests(unittest.TestCase):
    def test_seven_calendar_days_sum_latest_rows_without_mixing_disks(self):
        history = [dict(date=day, disk_id=ident, written_gib=value, sampled_at=day + "T10:00:00")
                   for day, ident, value in (("2026-10-18", "a", 900), ("2026-10-19", "a", 2),
                                             ("2026-10-24", "a", 3), ("2026-10-25", "a", 1),
                                             ("2026-10-25", "b", 100), ("2026-10-26", "a", 800))]
        pending = [dict(days=[dict(date="2026-10-24", disk_id="a", written_bytes=4 * GIB,
                                  sampled_at="2026-10-24T12:00:00")])]
        states = {"a": dict(last_date="2026-10-25", today_bytes=5 * GIB,
                             last_sample_at="2026-10-25T12:00:00")}
        result = weekly_totals(history, pending * 2, states, date(2026, 10, 25))
        self.assertEqual(result["a"], {"bytes": 11 * GIB, "days": 3})
        self.assertEqual(result["b"], {"bytes": 100 * GIB, "days": 1})
        self.assertEqual(weekly_totals(history, pending, states, date(2026, 11, 2)), {})

    def test_history_failure_does_not_report_an_incomplete_total_as_complete(self):
        reader = Mock(side_effect=PermissionError("locked"))
        service = MonitorService(config_manager.DEFAULT_CONFIG, history_reader=reader)
        service.refresh_history()
        self.assertFalse(service.history_ready)
        self.assertTrue(service.snapshot()["history_error"])
        reader.side_effect = None
        reader.return_value = []
        service.refresh_history()
        self.assertTrue(service.history_ready)
        self.assertEqual(service.snapshot()["history_error"], "")


class PopupTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.popup = TrayPopup(self.root, lambda: None)

    def test_two_disks_are_columns_and_each_value_is_a_separate_row(self):
        self.popup.update({"disks": [disk(), disk("硬盘二", health_percent=100)]})
        self.popup.render(wintypes.RECT(0, 0, 1920, 1080))
        self.root.update_idletasks()
        cards = self.popup.cards.winfo_children()
        self.assertEqual([card.grid_info()["column"] for card in cards], [0, 1])
        self.assertEqual([card.grid_info()["row"] for card in cards], [0, 0])
        self.assertGreaterEqual(cards[1].winfo_x(), cards[0].winfo_x() + cards[0].winfo_width())
        self.assertEqual(LABELS, ("今日写入：", "近七天写入：", "近七天日均写入：", "健康度：", "总写入："))
        for column, health in zip(self.popup.value_labels, ("99%", "100%")):
            self.assertEqual([label.cget("text") for label in column],
                             ["3.00 GiB", "21.00 GiB", "3.00 GiB", health, "30.00 TiB"])
            self.assertEqual([label.grid_info()["row"] for label in column], [1, 2, 3, 4, 5])
            self.assertEqual(len(column[-1].master.grid_slaves(row=6)), 1)
            for label in column:
                self.assertGreaterEqual(label.winfo_width(), label.winfo_reqwidth())

    def test_offline_and_missing_values_are_explicit(self):
        values = disk_values(disk(status="offline", week_bytes=None, state={}))
        self.assertEqual(values, ("3.00 GiB", "—", "—", "离线", "—"))

    def test_average_uses_seven_calendar_days_even_with_partial_records(self):
        self.assertEqual(disk_values(disk(week_bytes=10 * GIB, week_days=2))[2], "1.43 GiB")
        self.assertEqual(disk_values(disk(week_bytes=0, week_days=0))[2], "0.00 GiB")

    def test_many_disks_wrap_and_scroll_inside_the_work_area(self):
        self.popup.update({"disks": [disk(str(n)) for n in range(18)]})
        self.popup.render(wintypes.RECT(0, 0, 1000, 700))
        self.root.update_idletasks()
        cards = self.popup.cards.winfo_children()
        self.assertEqual(max(card.grid_info()["column"] for card in cards), 2)
        self.assertLess(self.popup.window.winfo_reqwidth(), 1000)
        self.assertLess(self.popup.window.winfo_reqheight(), 700)
        self.assertEqual(self.popup.scrollbar.winfo_manager(), "pack")

    def test_popup_position_handles_left_monitor_and_top_taskbar(self):
        work = wintypes.RECT(-1920, 40, 0, 1080)
        x, y = popup_position(wintypes.RECT(-50, 0, -20, 40), work, 600, 300)
        self.assertGreaterEqual(x, work.left)
        self.assertLessEqual(x + 600, work.right)
        self.assertGreaterEqual(y, work.top)
        self.assertLessEqual(y + 300, work.bottom)

    def test_hover_dwell_show_leave_hide_and_context_menu_suppression(self):
        self.popup.icon = Mock(menu_open=False, suppress_until=0, visible=True)
        pointer = [15, 15]
        def cursor(ptr):
            ptr._obj.x, ptr._obj.y = pointer
            return True
        def show(rect):
            self.popup.visible = True
        with patch.object(self.popup.locator, "hit", side_effect=lambda **kw: pointer == [15, 15]), \
                patch("tray_popup.USER.GetCursorPos", side_effect=cursor), \
                patch("tray_popup.mouse_button_down", return_value=False), \
                patch("tray_popup.time.monotonic", side_effect=[0, .4, .5, .8, 1.2]), \
                patch.object(self.root, "after", return_value=None), \
                patch.object(self.popup, "show", side_effect=show) as showing, \
                patch.object(self.popup, "hide", wraps=self.popup.hide) as hiding:
            self.popup._poll()
            showing.assert_not_called()
            self.popup._poll()
            showing.assert_called_once()
            pointer[:] = [-5000, -5000]
            self.popup._poll()
            self.popup._poll()
            self.assertFalse(self.popup.visible)
            self.popup.icon.menu_open = True
            self.popup._poll()
            self.assertGreaterEqual(hiding.call_count, 2)

    def test_overflow_hits_only_the_named_tile_without_mouse_notifications(self):
        locator = self.popup.locator
        host = 111
        pointer = [-20, 40]
        def cursor(target):
            target._obj.x, target._obj.y = pointer
            return True
        def window_class(hwnd, target, length):
            target.value = "TopLevelWindowForOverflowXamlIsland" if hwnd == 111 else "Shell_TrayWnd"
            return len(target.value)
        with patch("tray_locator.USER.GetPhysicalCursorPos", side_effect=cursor), \
                patch("tray_locator.USER.WindowFromPhysicalPoint", return_value=123), \
                patch("tray_locator.USER.GetAncestor", side_effect=lambda *args: host), \
                patch("tray_locator.USER.GetClassNameW", side_effect=window_class):
            self.assertFalse(locator.hit())
            locator.result = (host, wintypes.RECT(-30, 30, -10, 50))
            self.assertTrue(locator.hit())
            pointer[:] = [-5, 40]  # Adjacent icon in the open panel.
            self.assertFalse(locator.hit())
            host = 222  # Panel closed; its previous rectangle is stale.
            pointer[:] = [-20, 40]
            self.assertFalse(locator.hit())

    def test_confirmed_hover_survives_obstruction_and_panel_dismissal_until_departure(self):
        locator = self.popup.locator
        tile = wintypes.RECT(10, 10, 30, 30)
        locator.host = 111
        locator.result = (111, tile)
        point = [20, 20]
        def cursor(target):
            target._obj.x, target._obj.y = point
            return True
        def window_class(hwnd, target, length):
            target.value = "TopLevelWindowForOverflowXamlIsland" if hwnd == 111 else "tooltips_class32"
            return len(target.value)
        with patch("tray_locator.USER.GetPhysicalCursorPos", side_effect=cursor), \
                patch("tray_locator.USER.WindowFromPhysicalPoint", return_value=111), \
                patch("tray_locator.USER.GetAncestor", return_value=111) as host, \
                patch("tray_locator.USER.GetClassNameW", side_effect=window_class):
            self.assertTrue(locator.hit())
            host.return_value = 222
            locator.result = (111, None)
            self.assertTrue(locator.hit(keep_visible=True))
            self.assertTrue(locator.hit(keep_visible=True))
            locator.result = (111, wintypes.RECT(0, 0, 0, 0))
            self.assertTrue(locator.hit(keep_visible=True))
            point[:] = [50, 50]
            self.assertFalse(locator.hit(keep_visible=True))
            point[:] = [20, 20]
            self.assertFalse(locator.hit())  # An overlay cannot start a hover.
            host.return_value = 111
            self.assertFalse(locator.hit())
            locator.result = (111, tile)
            self.assertTrue(locator.hit())
            locator.result = (111, wintypes.RECT(30, 10, 50, 30))
            self.assertTrue(locator.hit(keep_visible=True))  # Panel animates; cursor stays put.
            point[:] = [9, 20]
            self.assertFalse(locator.hit(keep_visible=True))

    def test_right_click_suppresses_popup_before_submenu_opens(self):
        with patch("tray_popup.pystray.Icon.__init__", return_value=None):
            icon = HoverIcon("test", None, "", None)
        icon._running = False
        icon._icon_handle = None
        with patch("tray_popup.pystray.Icon._on_notify", return_value=None), \
                patch("tray_popup.time.monotonic", side_effect=[10.0, 10.2]):
            icon._on_notify(0, 0x0204)
            self.assertTrue(icon.menu_open)
            icon._on_notify(0, 0x0205)
        self.assertFalse(icon.menu_open)
        self.assertGreaterEqual(icon.suppress_until, 11.0)

    def test_held_mouse_button_cannot_flash_popup_during_menu_race(self):
        self.popup.icon = Mock(menu_open=False, suppress_until=0, visible=True)
        with patch.object(self.popup.locator, "hit", return_value=True), \
                patch("tray_popup.USER.GetCursorPos"), \
                patch("tray_popup.mouse_button_down", return_value=True), \
                patch("tray_popup.time.monotonic", return_value=1.0), \
                patch.object(self.root, "after", return_value=None), \
                patch.object(self.popup, "show") as showing:
            self.popup._poll()
        showing.assert_not_called()
        self.assertFalse(self.popup.visible)

    def test_stale_icon_rect_cannot_start_popup_when_icon_is_not_visible(self):
        self.popup.icon = Mock(menu_open=False, suppress_until=0, visible=False)
        with patch.object(self.popup.locator, "hit", return_value=True) as lookup, \
                patch("tray_popup.USER.GetCursorPos", return_value=True), \
                patch("tray_popup.mouse_button_down", return_value=False), \
                patch("tray_popup.time.monotonic", return_value=1.0), \
                patch.object(self.root, "after", return_value=None), \
                patch.object(self.popup, "show") as showing:
            self.popup._poll()
        showing.assert_not_called()
        lookup.assert_not_called()

    def test_menu_close_blocks_rehover_until_pointer_leaves_icon(self):
        self.popup.icon = Mock(menu_open=False, suppress_until=0, hover_blocked=True, visible=True)
        pointer = [15, 15]
        def cursor(target):
            target._obj.x, target._obj.y = pointer
            return True
        with patch.object(self.popup.locator, "hit", side_effect=lambda **kw: pointer == [15, 15]), \
                patch("tray_popup.USER.GetCursorPos", side_effect=cursor), \
                patch("tray_popup.mouse_button_down", return_value=False), \
                patch("tray_popup.time.monotonic", return_value=1.0), \
                patch.object(self.root, "after", return_value=None), \
                patch.object(self.popup, "show") as showing:
            self.popup._poll()
            showing.assert_not_called()
            pointer[:] = [-5000, -5000]
            self.popup._poll()
        self.assertFalse(self.popup.icon.hover_blocked)


if __name__ == "__main__":
    unittest.main()
