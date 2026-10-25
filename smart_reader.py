"""通过 smartctl JSON 读取设备身份和主机写入量。"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys


class SmartError(RuntimeError):
    pass


def hidden_process_options():
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return {"startupinfo": si, "creationflags": 0x08000000}


def find_smartctl():
    # 打包版本优先使用随程序附带的工具，避免依赖目标机器的 PATH。
    if getattr(sys, "frozen", False):
        bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "smartctl.exe")
        if os.path.isfile(bundled):
            return bundled
    found = shutil.which("smartctl")
    if found:
        return found
    roots = [os.path.dirname(os.path.abspath(__file__)), os.path.dirname(sys.executable)]
    roots.append(getattr(sys, "_MEIPASS", roots[0]))
    candidates = [
        r"C:\Program Files\smartmontools\bin\smartctl.exe",
        r"C:\Program Files (x86)\smartmontools\bin\smartctl.exe",
        *(os.path.join(root, "smartctl.exe") for root in roots),
    ]
    return next((path for path in candidates if os.path.isfile(path)), None)


def _run_json(args):
    executable = find_smartctl()
    if not executable:
        raise SmartError("未找到 smartctl，请安装 smartmontools 或放入程序目录")
    try:
        result = subprocess.run(
            [executable, *args, "-j"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, **hidden_process_options(),
        )
    except subprocess.TimeoutExpired as exc:
        raise SmartError("读取超时（15 秒）") from exc
    except OSError as exc:
        raise SmartError(f"无法运行 smartctl：{exc}") from exc
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise SmartError("smartctl 未返回有效 JSON，请使用支持 -j 的版本") from exc
    if not isinstance(data, dict):
        raise SmartError("smartctl JSON 格式错误")
    # smartctl 返回值是位掩码；健康告警位不能被当成命令执行失败。
    status = data.get("smartctl", {}).get("exit_status", result.returncode)
    if status & 3:
        messages = data.get("smartctl", {}).get("messages", [])
        detail = "; ".join(item.get("string", "") for item in messages)
        raise SmartError(detail or "设备无法访问，请检查连接及读取权限")
    return data


def scan_disks():
    data = _run_json(["--scan"])
    return [
        {"device": item["name"], "dtype": item.get("type", "auto")}
        for item in data.get("devices", []) if item.get("name")
    ]


def _integer(value):
    if isinstance(value, bool) or isinstance(value, float):
        return None
    try:
        result = int(str(value).replace(",", "").strip())
        return result if result >= 0 else None
    except (ValueError, TypeError):
        return None


def _identity(model, serial, wwn):
    if serial and serial.lower() not in ("unknown", "none", "n/a", "00000000"):
        raw = "serial:" + model + ":" + serial
    elif wwn:
        raw = "wwn:" + wwn
    else:
        return None
    return "disk-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _sata_bytes(data):
    """只接受属性名称明确标注的单位，未知型号不猜测 F1 的倍率。"""
    for attr in data.get("ata_smart_attributes", {}).get("table", []):
        name = attr.get("name", "").lower()
        if not any(word in name for word in ("host_writes", "total_lbas_written", "total_writes")):
            continue
        raw = _integer(attr.get("raw", {}).get("value"))
        if raw is None:
            continue
        if "32mib" in name:
            return raw * 32 * 1024 ** 2
        if "gib" in name:
            return raw * 1024 ** 3
        if "mib" in name:
            return raw * 1024 ** 2
    return None


def parse_smart(data, device, dtype):
    model = str(data.get("model_name") or data.get("product") or "未知型号").strip()
    serial = str(data.get("serial_number") or "").strip()
    wwn_data = data.get("wwn")
    wwn = json.dumps(wwn_data, sort_keys=True) if wwn_data else ""
    protocol = data.get("device", {}).get("protocol", dtype).upper()
    nvme = data.get("nvme_smart_health_information_log", {})
    total = None
    if nvme:
        units = _integer(nvme.get("data_units_written_s", nvme.get("data_units_written")))
        if units is not None:
            total = units * 512_000
    elif protocol == "ATA":
        total = _sata_bytes(data)
    is_ssd = bool(nvme) or protocol == "NVME" or data.get("rotation_rate") == 0
    error = ""
    if total is None:
        error = "未提供可确认单位的主机写入计数"
    disk_id = _identity(model, serial, wwn)
    if not disk_id:
        error = "缺少可靠的序列号或 WWN，暂不自动累计"
    used = _integer(nvme.get("percentage_used"))
    warnings = _integer(nvme.get("critical_warning")) or 0
    warning_labels = [
        label for bit, label in enumerate((
            "备用空间不足", "温度越过设备阈值", "可靠性下降", "设备进入只读", "易失性缓存备份故障",
        )) if warnings & (1 << bit)
    ]
    if warnings & ~31:
        warning_labels.append(f"其他关键告警 0x{warnings:02x}")
    return {
        "disk_id": disk_id, "device": device, "dtype": dtype,
        "model": model, "serial": serial, "wwn": wwn, "protocol": protocol,
        "capacity_bytes": data.get("user_capacity", {}).get("bytes", data.get("nvme_total_capacity")),
        "is_ssd": is_ssd, "total_bytes": total,
        "temperature": data.get("temperature", {}).get("current", nvme.get("temperature")),
        "percentage_used": used,
        "health_percent": max(0, 100 - used) if used is not None else None,
        "health_warning": "；".join(warning_labels),
        "available_spare": nvme.get("available_spare"),
        "media_errors": nvme.get("media_errors"),
        "power_on_hours": data.get("power_on_time", {}).get("hours", nvme.get("power_on_hours")),
        "critical_warning": warnings,
        "supported": total is not None and disk_id is not None and is_ssd,
        "error": error if is_ssd else "当前版本仅监控固态硬盘",
    }


def read_smart(device, dtype="auto"):
    # 每次采样都读取身份，防止拔盘后同一路径指向另一块设备。
    return parse_smart(_run_json(["-i", "-A", "-d", dtype, device]), device, dtype)


def physical_disk_number(device):
    match = re.fullmatch(r"(?:/dev/)?sd([a-z]+)", device, flags=re.I)
    if match:
        number = 0
        for letter in match.group(1).lower():
            number = number * 26 + ord(letter) - ord("a") + 1
        return number - 1
    match = re.search(r"(?:PhysicalDrive|/dev/pd)(\d+)$", device, flags=re.I)
    return int(match.group(1)) if match else None


def volume_map():
    """盘符仅用于显示；设备路径必须能明确映射到 Windows 物理磁盘号。"""
    if os.name != "nt":
        return {}
    script = (
        "$ErrorActionPreference='Stop'; "
        "@(Get-Disk | ForEach-Object { "
        "$n=$_.Number; "
        "$letters=@(Get-Partition -DiskNumber $n -ErrorAction SilentlyContinue | "
        "Where-Object DriveLetter | ForEach-Object { [string]$_.DriveLetter + ':' }); "
        "[pscustomobject]@{number=$n; letters=$letters} "
        "}) | ConvertTo-Json -Compress -Depth 4"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=15, **hidden_process_options(),
        )
        if result.returncode:
            return {}
        data = json.loads(result.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
        return {item["number"]: item.get("letters", []) for item in data}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
