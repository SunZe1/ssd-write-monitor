# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置
# 使用方法: conda run -n query pyinstaller ssd_monitor.spec

block_cipher = None

a = Analysis(
    ['disk_monitor.py'],
    pathex=[],
    binaries=[],
    datas=[
        # 如果需要打包 smartctl，取消注释下面这行：
        # ('C:\\Program Files\\smartmontools\\bin\\smartctl.exe', '.'),
    ],
    hiddenimports=['pystray._win32', 'psutil'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SSDMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',        # 托盘图标（需要提供 icon.ico 文件）
)
