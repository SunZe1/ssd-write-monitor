# -*- mode: python ; coding: utf-8 -*-
"""Windows x64 单文件版，内含 smartctl；数据始终保存在 exe 旁的 data 中。"""
from pathlib import Path
import os
import shutil
import sys
from comtypes.client import GetModule

root = Path(SPECPATH)
smart_path = os.environ.get("SMARTCTL_PATH") or shutil.which("smartctl")
if not smart_path:
    smart_path = r"C:\Program Files\smartmontools\bin\smartctl.exe"
smart = Path(smart_path)
if not smart.is_file():
    raise SystemExit("未找到 smartctl.exe，请安装 smartmontools 或设置 SMARTCTL_PATH")
binaries = [(str(smart), ".")]
# conda 的运行时 DLL 位于 Library/bin，必须随 exe 打包，不能依赖用户环境。
runtime_dir = Path(sys.prefix) / "Library" / "bin"
if runtime_dir.is_dir():
    os.environ["PATH"] = str(runtime_dir) + os.pathsep + os.environ.get("PATH", "")
    for name in ("ffi.dll", "libexpat.dll", "liblzma.dll", "libbz2.dll", "tcl86t.dll", "tk86t.dll"):
        candidate = runtime_dir / name
        if candidate.is_file():
            binaries.append((str(candidate), "."))
datas = [
    (str(root / "third_party" / "smartmontools" / "COPYING.txt"), "licenses/smartmontools"),
    (str(root / "使用说明.txt"), "."),
]
database = smart.with_name("drivedb.h")
if database.is_file():
    datas.append((str(database), "."))

uia_module = GetModule("UIAutomationCore.dll")
a = Analysis(
    [str(root / "disk_monitor.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["pystray._win32", uia_module.__name__],
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "pandas", "matplotlib", "scipy", "pytest", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="SSDMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(root / "assets" / "ssd_monitor.ico"),
    version=str(root / "assets" / "version_info.txt"),
    uac_admin=False,
)
