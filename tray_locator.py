"""Find the named SSD icon inside the taskbar or its visible overflow panel."""
import ctypes
from ctypes import wintypes
import logging
import threading

import comtypes
from comtypes.client import CreateObject, GetModule

TRAY_TITLE = "SSDMonitor · SSD 写入监控"
TRAY_WINDOWS = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                "TopLevelWindowForOverflowXamlIsland"}
USER = ctypes.WinDLL("user32", use_last_error=True)
USER.GetPhysicalCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
USER.WindowFromPhysicalPoint.argtypes = [wintypes.POINT]
USER.WindowFromPhysicalPoint.restype = wintypes.HWND
USER.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
USER.GetAncestor.restype = wintypes.HWND
USER.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


def contains(rect, x, y):
    return bool(rect and rect.left <= x < rect.right and rect.top <= y < rect.bottom)


class TrayIconLocator:
    def __init__(self):
        self.host = None
        self.result = (None, None)
        self.confirmed = None
        self.changed = threading.Event()
        self.closed = False

    def start(self, title):
        threading.Thread(target=self._watch, args=(title,), name="tray-location", daemon=True).start()

    def close(self):
        self.closed = True
        self.changed.set()

    def hit(self, keep_visible=False):
        point = wintypes.POINT()
        host = None
        have_point = USER.GetPhysicalCursorPos(ctypes.byref(point))
        # Once the card is open, keep its trigger area fixed until the pointer
        # leaves. Explorer can animate, hide or destroy the overflow window.
        retained = keep_visible and have_point and contains(self.confirmed, point.x, point.y)
        if have_point and not retained:
            hwnd = USER.GetAncestor(USER.WindowFromPhysicalPoint(point), 2)
            name = ctypes.create_unicode_buffer(96)
            if hwnd and USER.GetClassNameW(hwnd, name, len(name)) and name.value in TRAY_WINDOWS:
                host = hwnd
        if host != self.host:
            self.host = host
            self.result = (None, None)
            self.changed.set()
        if retained:
            return True
        self.confirmed = None
        owner, rect = self.result
        if host and owner == host and contains(rect, point.x, point.y):
            self.confirmed = rect
            return True
        return False

    def _watch(self, title):
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        try:
            try:
                from comtypes.gen import UIAutomationClient as uia
            except ImportError:
                uia = GetModule("UIAutomationCore.dll")
            client = CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
            condition = client.CreatePropertyCondition(uia.UIA_NamePropertyId, title)
            while not self.closed:
                self.changed.clear()
                host = self.host
                if host:
                    rect = None
                    try:
                        item = client.ElementFromHandle(host).FindFirst(uia.TreeScope_Descendants, condition)
                        if item and not item.CurrentIsOffscreen:
                            rect = item.CurrentBoundingRectangle
                    except (comtypes.COMError, ValueError):
                        pass  # Explorer may close the panel during the lookup.
                    self.result = (host, rect)
                if not self.closed:
                    self.changed.wait(0.25 if host else None)
        except Exception:
            logging.getLogger(__name__).exception("无法定位托盘图标")
        finally:
            comtypes.CoUninitialize()
