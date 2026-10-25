"""Windows 原生托盘提示最多容纳 127 个 UTF-16 代码单元。"""
GIB = 1024 ** 3
TIB = 1024 ** 4


def utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


def shorten(text, limit):
    if utf16_length(text) <= limit:
        return text
    return text.encode("utf-16-le")[:(limit - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def _disk_block(disk, detailed):
    name = disk.get("alias") or disk.get("model") or "SSD"
    # 保留名称尾部的序列号片段，便于区分同型号硬盘。
    if utf16_length(name) > 12:
        name = shorten(name, 7) + name[-4:]
    written = disk.get("today_bytes", 0) / GIB
    status = disk.get("status")
    if status in ("offline", "error", "pending"):
        label = {"offline": "离线", "error": "读取失败", "pending": "待检测"}[status]
        return f"{name}\n状态：{label}\n已记：{written:.2f}GiB"
    health = disk.get("health_percent")
    temperature = disk.get("temperature")
    health_text = f"{health}%" if health is not None else "—"
    if disk.get("critical_warning"):
        health_text = "告警 " + health_text
    temp = f"{temperature}℃" if temperature is not None else "—"
    lines = [name, f"今日：{written:.2f}GiB"]
    total = disk.get("state", {}).get("last_total_bytes")
    if detailed and total is not None:
        lines.append(f"累计：{total / TIB:.2f}TiB")
    lines.extend((f"健康：{health_text}", f"温度：{temp}"))
    return "\n".join(lines)


def build_tooltip(snapshot, paused=False, limit=127):
    disks = [disk for disk in snapshot["disks"] if disk.get("enabled")]
    total = sum(disk.get("today_bytes", 0) for disk in disks) / GIB
    mode = "暂停" if paused else "今日"
    header = f"SSD {mode}合计 {total:.2f}GiB"
    if snapshot.get("error"):
        header += "\n采集异常"
    if snapshot.get("pending"):
        header += f"\n待写：{snapshot['pending']}"
    if not disks:
        return shorten(header + "\n未启用可监控硬盘，双击查看", limit)
    detailed = header + "\n\n" + "\n\n".join(_disk_block(disk, True) for disk in disks)
    if utf16_length(detailed) <= limit:
        return detailed
    blocks = [header]
    for index, disk in enumerate(disks):
        candidate = blocks + [_disk_block(disk, False)]
        remaining = len(disks) - index - 1
        footer = f"\n\n另{remaining}盘，双击查看" if remaining else ""
        if utf16_length("\n\n".join(candidate) + footer) > limit:
            blocks.append(f"另{len(disks) - index}盘，双击查看")
            break
        blocks = candidate
    return shorten("\n\n".join(blocks), limit)
