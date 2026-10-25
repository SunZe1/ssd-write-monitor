"""Windows 托盘悬停卡片：每盘一列，每个指标一行。"""
import ctypes
from ctypes import wintypes
import time
import tkinter as tk
from tkinter import ttk

import pystray

GIB, TIB = 1024 ** 3, 1024 ** 4
LABELS = ("今日写入：", "近七天写入：", "健康度：", "总写入：")


class IconIdentifier(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("guidItem", ctypes.c_byte * 16)]


class MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


USER = ctypes.WinDLL("user32", use_last_error=True)
SHELL = ctypes.WinDLL("shell32", use_last_error=True)
SHELL.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(IconIdentifier), ctypes.POINTER(wintypes.RECT)]
SHELL.Shell_NotifyIconGetRect.restype = ctypes.c_long
USER.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
USER.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
USER.MonitorFromRect.restype = wintypes.HANDLE
USER.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
USER.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
USER.GetAncestor.restype = wintypes.HWND
USER.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
USER.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
USER.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, wintypes.UINT]
USER.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]


def icon_rect(icon):
    hwnd = getattr(icon, "_hwnd", None)
    if not hwnd or not icon.visible:
        return None
    # pystray 0.19 的 hID 参数未填入 uID，实际注册 ID 为 0；也兼容填入对象 ID 的后端。
    for uid in (0, id(icon) & 0xffffffff):
        identifier = IconIdentifier(cbSize=ctypes.sizeof(IconIdentifier), hWnd=hwnd, uID=uid)
        rect = wintypes.RECT()
        if SHELL.Shell_NotifyIconGetRect(ctypes.byref(identifier), ctypes.byref(rect)) == 0:
            return rect
    return None


def contains(rect, x, y):
    return bool(rect and rect.left <= x < rect.right and rect.top <= y < rect.bottom)


def work_area(rect):
    monitor = USER.MonitorFromRect(ctypes.byref(rect), 2)
    info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
    if not USER.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return info.rcWork


def popup_position(anchor, work, width, height):
    x = max(work.left + 4, min(anchor.right - width, work.right - width - 4))
    y = anchor.top - height - 8
    if y < work.top:
        y = anchor.bottom + 8
    y = max(work.top + 4, min(y, work.bottom - height - 4))
    return x, y


def disk_values(disk):
    week = disk.get("week_bytes")
    total = disk.get("state", {}).get("last_total_bytes", disk.get("total_bytes"))
    health = disk.get("health_percent")
    health_text = f"{health}%" if health is not None else "—"
    if disk.get("critical_warning"):
        health_text += " · 告警"
    status = disk.get("status")
    if status in ("offline", "error", "pending"):
        health_text = {"offline": "离线", "error": "读取失败", "pending": "检测中"}[status]
    return (
        f"{disk.get('today_bytes', 0) / GIB:.2f} GiB",
        f"{week / GIB:.2f} GiB" if week is not None else "—",
        health_text,
        f"{total / TIB:.2f} TiB" if total is not None else "—",
    )


class HoverIcon(pystray.Icon):
    """在系统菜单打开时抑制悬停卡片，保留 pystray 的点击行为。"""
    menu_open = False
    suppress_until = 0

    def _on_notify(self, wparam, lparam):
        if lparam in (0x201, 0x202, 0x204, 0x205):
            self.suppress_until = time.monotonic() + 0.8
        if lparam != 0x205:
            return super()._on_notify(wparam, lparam)
        self.menu_open = True
        try:
            return super()._on_notify(wparam, lparam)
        finally:
            if self.menu_open:
                self.suppress_until = time.monotonic() + 0.8
            self.menu_open = False


class TrayPopup:
    def __init__(self, root, open_window):
        self.root, self.open_window = root, open_window
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-toolwindow", True)
        self.window.configure(background="#cbd5e1")
        self.window.bind("<Double-Button-1>", lambda event: self._open())
        self.window.bind("<Escape>", lambda event: self.hide())
        self.frame = tk.Frame(self.window, background="#f8fafc", padx=14, pady=12)
        self.frame.pack(fill="both", expand=True, padx=1, pady=1)
        self.heading = tk.Label(self.frame, text="SSD 写入监控", background="#f8fafc",
                                foreground="#0f172a", font=("Microsoft YaHei UI", 11, "bold"))
        self.heading.pack(anchor="w", pady=(0, 8))
        body = tk.Frame(self.frame, background="#f8fafc")
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, background="#f8fafc", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.cards = tk.Frame(self.canvas, background="#f8fafc")
        self.canvas.create_window(0, 0, window=self.cards, anchor="nw")
        self.window.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120), "units"))
        self.footer = tk.Label(self.frame, text="", background="#f8fafc", foreground="#64748b",
                               font=("Microsoft YaHei UI", 9), justify="left")
        self.footer.pack(anchor="w", pady=(8, 0))
        self.snapshot = {"disks": []}
        self.paused = False
        self.visible = False
        self.icon = None
        self.timer = None
        self.hover_since = None
        self.leave_since = None
        self.anchor = None
        self.signature = None
        self.value_labels = []
        self.window.update_idletasks()
        self.hwnd = USER.GetAncestor(self.window.winfo_id(), 2)
        style = USER.GetWindowLongW(self.hwnd, -20)
        USER.SetWindowLongW(self.hwnd, -20, style | 0x08000000 | 0x80)  # NOACTIVATE / TOOLWINDOW

    def update(self, snapshot, paused=False):
        self.snapshot, self.paused = snapshot, paused
        if self.visible:
            self.show(self.anchor)

    def render(self, work):
        disks = [disk for disk in self.snapshot["disks"] if disk.get("enabled")]
        columns = max(1, min(len(disks), 4, (work.right - work.left - 64) // 280))
        signature = (repr(disks), self.paused, self.snapshot.get("history_error"),
                     self.snapshot.get("error"), self.snapshot.get("pending"), columns)
        if signature == self.signature:
            return
        self.signature = signature
        for child in self.cards.winfo_children():
            child.destroy()
        self.value_labels = []
        self.heading.configure(text="SSD 写入监控" + (" · 已暂停" if self.paused else ""))
        for index, disk in enumerate(disks):
            card = tk.Frame(self.cards, background="white", padx=12, pady=10,
                            highlightbackground="#e2e8f0", highlightthickness=1)
            card.grid(row=index // columns, column=index % columns, sticky="nsew", padx=4, pady=4)
            name = disk.get("alias") or disk.get("model") or "SSD"
            tk.Label(card, text=name, background="white", foreground="#0f172a",
                     font=("Microsoft YaHei UI", 10, "bold"), wraplength=245,
                     anchor="w", justify="left", height=2).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
            labels = []
            for row, (label, value) in enumerate(zip(LABELS, disk_values(disk)), 1):
                tk.Label(card, text=label, background="white", foreground="#475569",
                         font=("Microsoft YaHei UI", 10)).grid(row=row, column=0, sticky="w", pady=4)
                item = tk.Label(card, text=value, background="white", foreground="#0f172a",
                                font=("Microsoft YaHei UI", 10, "bold"))
                item.grid(row=row, column=1, sticky="e", padx=(14, 0), pady=4)
                labels.append(item)
            self.value_labels.append(labels)
            coverage = f"近七天已有 {disk.get('week_days', 0)} 天记录" if disk.get("week_bytes") is not None else "近七天记录读取中或暂不可用"
            tk.Label(card, text=coverage, background="white", foreground="#64748b",
                     font=("Microsoft YaHei UI", 9)).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        if not disks:
            tk.Label(self.cards, text="未启用可监控硬盘，双击打开管理窗口。",
                     background="#f8fafc", padx=8, pady=20).grid()
        notes = ["近七天含今天，仅累计已确认写入。双击打开管理。"]
        if self.snapshot.get("history_error"):
            notes.append(self.snapshot["history_error"])
        if self.snapshot.get("error"):
            notes.append("采集异常，请打开管理窗口查看。")
        if self.snapshot.get("pending"):
            notes.append(f"{self.snapshot['pending']} 批记录等待写入 Excel。")
        self.footer.configure(text="\n".join(notes), wraplength=max(280, columns * 280))
        self.window.update_idletasks()
        width, height = self.cards.winfo_reqwidth(), self.cards.winfo_reqheight()
        max_height = max(100, work.bottom - work.top - 160)
        self.canvas.configure(width=width, height=min(height, max_height), scrollregion=(0, 0, width, height))
        if height > max_height:
            self.scrollbar.pack(side="right", fill="y")
        else:
            self.scrollbar.pack_forget()
        self.window.update_idletasks()

    def show(self, anchor):
        self.anchor = anchor
        work = work_area(anchor)
        self.render(work)
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x, y = popup_position(anchor, work, width, height)
        self.window.geometry(f"{width}x{height}")
        # NOACTIVATE 防止鼠标悬停时抢走其他程序的输入焦点。
        self.window.deiconify()
        USER.SetWindowPos(self.hwnd, wintypes.HWND(-1), x, y, width, height, 0x10 | 0x40)
        self.visible = True

    def hide(self):
        self.window.withdraw()
        self.visible = False
        self.hover_since = self.leave_since = None

    def _open(self):
        self.hide()
        self.open_window()

    def start(self, icon):
        self.icon = icon
        self._poll()

    def _poll(self):
        self.timer = None
        now = time.monotonic()
        rect = icon_rect(self.icon)
        point = wintypes.POINT()
        USER.GetCursorPos(ctypes.byref(point))
        popup = wintypes.RECT()
        if self.visible:
            USER.GetWindowRect(self.hwnd, ctypes.byref(popup))
        if self.icon.menu_open or now < self.icon.suppress_until:
            self.hide()
        elif contains(rect, point.x, point.y):
            self.leave_since = None
            if self.hover_since is None:
                self.hover_since = now
            if now - self.hover_since >= 0.3 and not self.visible:
                self.show(rect)
        elif self.visible and contains(popup, point.x, point.y):
            self.leave_since = None
        else:
            self.hover_since = None
            if self.visible:
                if self.leave_since is None:
                    self.leave_since = now
                elif now - self.leave_since >= 0.25:
                    self.hide()
        self.timer = self.root.after(100, self._poll)

    def close(self):
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer = None
        self.window.destroy()
