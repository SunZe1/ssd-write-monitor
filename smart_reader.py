import subprocess
import re
import shutil


def find_smartctl():
    """查找 smartctl 可执行文件路径"""
    # 优先用 PATH 中的
    path = shutil.which("smartctl")
    if path:
        return path
    # 常见安装位置
    candidates = [
        r"C:\Program Files\smartmontools\bin\smartctl.exe",
        r"C:\Program Files (x86)\smartmontools\bin\smartctl.exe",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "smartctl.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None

import os


def scan_disks():
    """扫描系统中的磁盘，返回列表 [(device, type), ...]"""
    smartctl = find_smartctl()
    if not smartctl:
        return []
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        result = subprocess.run(
            [smartctl, "--scan"],
            capture_output=True, text=True, timeout=10,
            startupinfo=si, creationflags=0x08000000
        )
        disks = []
        for line in result.stdout.strip().splitlines():
            # 格式: /dev/sda -d nvme # /dev/sda, NVMe device
            parts = line.split()
            if len(parts) >= 3:
                device = parts[0]
                dtype = parts[2]
                disks.append((device, dtype))
        return disks
    except Exception:
        return []


def read_smart(device=None, dtype=None):
    """
    读取 SMART 数据，返回 (total_bytes_written, temperature_celsius) 或 None
    支持 NVMe 和 SATA (SMART attribute F1)
    """
    smartctl = find_smartctl()
    if not smartctl:
        return None

    # 如果没有指定磁盘，自动扫描
    if device is None:
        disks = scan_disks()
        if not disks:
            return None
        device, dtype = disks[0]

    if dtype is None:
        dtype = "nvme"

    try:
        cmd = [smartctl, "-A", device, "-d", dtype]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
            startupinfo=si, creationflags=0x08000000
        )
        output = result.stdout

        if dtype.lower() == "nvme":
            return _parse_nvme(output)
        else:
            return _parse_sata(output)
    except Exception as e:
        print(f"SMART 读取失败: {e}")
        return None


def _parse_nvme(output):
    """解析 NVMe SMART 输出"""
    total_bytes = None
    temp = None

    for line in output.splitlines():
        line = line.strip()
        # Data Units Written: 43,534,621 [22.2 TB]
        if line.startswith("Data Units Written:"):
            match = re.search(r"(\d[\d,]*)\s*\[([^\]]+)\]", line)
            if match:
                units = int(match.group(1).replace(",", ""))
                # NVMe: 每个 data unit = 512,000 bytes (500 KB)
                total_bytes = units * 512_000
        # Temperature: 46 Celsius
        elif line.startswith("Temperature:"):
            match = re.search(r"(\d+)\s*Celsius", line)
            if match:
                temp = int(match.group(1))

    if total_bytes is not None:
        return {"total_bytes": total_bytes, "temperature": temp}
    return None


def _parse_sata(output):
    """解析 SATA SMART 输出 (attribute F1: Host Writes)"""
    total_bytes = None
    temp = None

    for line in output.splitlines():
        line = line.strip()
        # 格式: F1 Host_Writes_32MiB ...
        if re.match(r"^F1\s+", line):
            parts = line.split()
            if len(parts) >= 10:
                raw_value = parts[9]
                try:
                    sectors = int(raw_value)
                    # SATA 通常是 32MiB 单位，或者用 sectors
                    # F1 通常是 32MiB 单位
                    total_bytes = sectors * 32 * 1024 * 1024
                except ValueError:
                    pass
        elif line.startswith("194 Temperature_Celsius") or \
             line.startswith("190 Temperature_Case"):
            parts = line.split()
            if len(parts) >= 10:
                try:
                    temp = int(parts[9])
                except ValueError:
                    pass

    if total_bytes is not None:
        return {"total_bytes": total_bytes, "temperature": temp}
    return None


def bytes_to_display(total_bytes):
    """将字节数转换为 GB 和 TB 的显示字符串"""
    gb = total_bytes / (1024 ** 3)
    tb = total_bytes / (1024 ** 4)
    return gb, tb
