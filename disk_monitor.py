import os
import sys
import time
import threading
from datetime import datetime, date
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem as Item

from config_manager import load_config, save_config, load_state, save_state, DATA_DIR
from smart_reader import read_smart, scan_disks, find_smartctl
from csv_logger import log_write, log_daily_summary, log_trend, get_today_stats, get_recent_trend
from auto_start import is_auto_start_enabled, set_auto_start
from process_io_tracker import snapshot as io_snapshot, compute_delta as io_compute_delta


# ─── 全局状态 ───
icon = None  # 托盘图标
monitor_thread = None
running = True
paused = False
config = None
interval_seconds = 600  # 默认 10 分钟
last_io_snapshot = None  # 上次进程写入量快照


def bytes_display(b):
    """字节转 GB/TB 显示"""
    gb = b / (1024 ** 3)
    tb = b / (1024 ** 4)
    if tb >= 1:
        return f"{tb:.2f} TB ({gb:.1f} GB)"
    return f"{gb:.2f} GB"


# ─── 采集逻辑 ───
def collect_once():
    """执行一次采集"""
    global config, last_io_snapshot

    # ─── 进程写入量快照（采集前）───
    if config.get("track_processes", True):
        curr_snap = io_snapshot()
    else:
        curr_snap = None

    smart_data = read_smart()
    if smart_data is None:
        print(f"[{datetime.now()}] SMART 读取失败")
        return

    total_bytes = smart_data["total_bytes"]
    temp = smart_data.get("temperature")
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    state = load_state(config)

    if state is None:
        # 首次运行
        state = {
            "last_total_bytes": total_bytes,
            "last_date": today_str,
            "day_start_bytes": total_bytes,
            "today_bytes": 0,
            "today_gb": 0.0,
            "readings": 0,
        }
        save_state(config, state)
        # 记录第一次采集
        log_write(now, config["disk"], total_bytes, 0, 0, temp)
        log_trend(0, total_bytes, 1)
        return

    # 计算增量
    delta = max(0, total_bytes - state["last_total_bytes"])

    # 检测日期变化
    if today_str != state["last_date"]:
        # 生成昨天的 daily_summary
        yesterday_written = state["today_bytes"]
        if yesterday_written > 0:
            day_start = state["day_start_bytes"]
            day_end = state["last_total_bytes"]
            log_daily_summary(config["disk"], yesterday_written, day_start, day_end)

        # 重置今天
        state["day_start_bytes"] = state["last_total_bytes"]
        state["today_bytes"] = delta
        state["today_gb"] = delta / (1024 ** 3)
        state["readings"] = 1
    else:
        # 同一天，累加
        state["today_bytes"] += delta
        state["today_gb"] = state["today_bytes"] / (1024 ** 3)
        state["readings"] += 1

    # 更新状态
    state["last_total_bytes"] = total_bytes
    state["last_date"] = today_str
    save_state(config, state)

    # ─── 计算进程写入来源 ───
    sources = None
    if curr_snap is not None and last_io_snapshot is not None:
        sources = io_compute_delta(last_io_snapshot, curr_snap, top_n=3)
        if sources:
            top_str = ", ".join(f"{s['name']}({s['delta_bytes']/(1024**2):.1f}MB)" for s in sources)
            print(f"  写入来源: {top_str}")

    # 保存本次快照供下次对比
    if curr_snap is not None:
        last_io_snapshot = curr_snap

    # 写入日志（含进程来源）
    log_write(now, config["disk"], total_bytes, delta, state["today_bytes"], temp, sources=sources)
    log_trend(state["today_bytes"], total_bytes, state["readings"])

    print(f"[{now}] 采集完成 | 今日: {state['today_gb']:.2f} GB | 增量: {delta/(1024**3):.2f} GB | 温度: {temp}°C")


def monitor_loop():
    """后台采集线程"""
    while running:
        if not paused:
            try:
                collect_once()
            except Exception as e:
                print(f"采集异常: {e}")
        # 等待下一个周期
        for _ in range(int(interval_seconds)):
            if not running:
                return
            time.sleep(1)


# ─── 托盘图标绘制 ───
def create_icon_image():
    """生成托盘图标"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 简单的磁盘图标
    draw.ellipse([8, 8, 56, 56], fill=(52, 152, 219), outline=(41, 128, 185), width=3)
    draw.text((18, 16), "SSD", fill="white")
    return img


def update_tooltip():
    """更新托盘悬停提示"""
    if icon is None:
        return
    stats = get_today_stats()
    state = load_state(config)

    lines = ["SSD 写入监控"]
    if paused:
        lines.append("⏸ 已暂停")
    if stats:
        lines.append(f"今日写入: {stats['written_gb']:.2f} GB ({stats['written_tb']:.3f} TB)")
        lines.append(f"近7天日均: {stats['avg_7d_gb']:.2f} GB")
        lines.append(f"累计写入: {stats['cumulative_tb']:.3f} TB")
    elif state:
        lines.append(f"今日写入: {state['today_gb']:.2f} GB")
    icon.title = "\n".join(lines)


# ─── 菜单事件 ───
def on_show_today(icon_obj, item):
    """显示今日统计"""
    import subprocess, tempfile
    stats = get_today_stats()
    state = load_state(config)
    if stats:
        msg = (
            f"日期: {stats['date']}\r\n"
            f"今日写入: {stats['written_gb']:.2f} GB ({stats['written_tb']:.3f} TB)\r\n"
            f"累计写入: {stats['cumulative_tb']:.3f} TB\r\n"
            f"近7天日均: {stats['avg_7d_gb']:.2f} GB\r\n"
            f"今日采集次数: {stats['readings']}"
        )
    elif state:
        msg = f"今日写入: {state['today_gb']:.2f} GB\r\n今日采集次数: {state['readings']}"
    else:
        msg = "暂无数据，请等待首次采集完成。"
    # 写入临时 ps1 文件并执行
    ps1 = tempfile.mktemp(suffix=".ps1")
    with open(ps1, "w", encoding="utf-8-sig") as f:
        f.write("Add-Type -AssemblyName System.Windows.Forms\n")
        f.write(f"[System.Windows.Forms.MessageBox]::Show(@\"\n{msg}\n\"@, '今日统计')")
    subprocess.Popen(
        ["powershell", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", ps1],
        creationflags=0x08000000
    )

def on_open_log_dir(icon_obj, item):
    """打开日志目录"""
    os.startfile(DATA_DIR)


def on_pause(icon_obj, item):
    """暂停/恢复监控"""
    global paused
    paused = not paused


def pause_text(item):
    """菜单文本：暂停/恢复"""
    return "▶️ 恢复监控" if paused else "⏸️ 暂停监控"


def on_set_interval(icon_obj, item):
    """设置采集间隔"""
    global interval_seconds, config
    interval_map = {
        "5 分钟": 300,
        "10 分钟": 600,
        "30 分钟": 1800,
        "60 分钟": 3600,
    }
    seconds = interval_map.get(item.text.replace("✅ ", "").strip(), 600)
    interval_seconds = seconds
    config["interval_minutes"] = seconds // 60
    save_config(config)


def interval_text(minutes):
    """返回间隔菜单项的勾选状态文本"""
    def checker(item):
        current = interval_seconds // 60
        if item.text.replace("✅ ", "").strip() == f"{minutes} 分钟":
            return True
        return False
    return checker


def interval_label(minutes):
    """生成间隔菜单项文本"""
    def get_label(item):
        current = interval_seconds // 60
        base = f"{minutes} 分钟"
        return f"✅ {base}" if current == minutes else base
    return get_label


def on_toggle_track_processes(icon_obj, item):
    """切换进程写入量追踪"""
    current = config.get("track_processes", True)
    config["track_processes"] = not current
    save_config(config)


def track_processes_text(item):
    """进程追踪菜单文本"""
    return "✅ 记录写入进程" if config.get("track_processes", True) else "☐ 记录写入进程"


def on_toggle_autostart(icon_obj, item):
    """切换开机自启"""
    current = is_auto_start_enabled()
    set_auto_start(not current)
    config["auto_start"] = not current
    save_config(config)


def autostart_text(item):
    """开机自启菜单文本"""
    return "✅ 开机自启" if is_auto_start_enabled() else "☐ 开机自启"


def on_exit(icon_obj, item):
    """退出程序"""
    global running
    running = False
    icon_obj.stop()


# ─── 主入口 ───
def main():
    global icon, config, interval_seconds, monitor_thread

    # ─── 进程互斥检测（锁文件）───
    import tempfile
    lock_file = os.path.join(DATA_DIR, "ssd_monitor.lock")
    if os.path.exists(lock_file):
        try:
            with open(lock_file, "r") as f:
                pid = int(f.read().strip())
            # 检查进程是否存在
            import subprocess
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True,
                startupinfo=si, creationflags=0x08000000
            )
            if str(pid) in result.stdout:
                sys.exit(0)
        except (ValueError, Exception):
            pass
    # 写入当前 PID
    with open(lock_file, "w") as f:
        f.write(str(os.getpid()))

    # 检查 smartctl
    if find_smartctl() is None:
        print("[SSD Monitor] 未找到 smartctl，请安装 smartmontools。", file=sys.stderr)
        sys.exit(1)

    # 加载配置
    config = load_config()
    interval_seconds = config.get("interval_minutes", 10) * 60

    # 同步自启动：配置标记启用但注册表缺失时，自动修复
    if config.get("auto_start") and not is_auto_start_enabled():
        set_auto_start(True)

    # 启动采集线程
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()

    # 创建托盘图标
    image = create_icon_image()

    # 间隔子菜单
    interval_menu = pystray.Menu(
        Item(lambda item: "✅ 5 分钟" if interval_seconds == 300 else "5 分钟", on_set_interval),
        Item(lambda item: "✅ 10 分钟" if interval_seconds == 600 else "10 分钟", on_set_interval),
        Item(lambda item: "✅ 30 分钟" if interval_seconds == 1800 else "30 分钟", on_set_interval),
        Item(lambda item: "✅ 60 分钟" if interval_seconds == 3600 else "60 分钟", on_set_interval),
    )

    menu = pystray.Menu(
        Item("📊 查看今日统计", on_show_today),
        Item("📂 打开日志目录", on_open_log_dir),
        Item(pause_text, on_pause),
        Item("⏱️ 采集间隔", interval_menu),
        Item(track_processes_text, on_toggle_track_processes),
        Item(autostart_text, on_toggle_autostart),
        Item("❌ 退出", on_exit),
    )

    icon = pystray.Icon("SSDMonitor", image, "SSD 写入监控", menu)

    # 定时更新 tooltip
    def update_loop():
        while running:
            update_tooltip()
            time.sleep(30)

    threading.Thread(target=update_loop, daemon=True).start()


    try:
        icon.run()
    finally:
        try:
            os.remove(lock_file)
        except Exception:
            pass


if __name__ == "__main__":
    main()

