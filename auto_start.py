import winreg
import os
import sys

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "SSDMonitor"


def get_script_path():
    """获取主脚本完整路径"""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "disk_monitor.py")


def is_auto_start_enabled():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except (FileNotFoundError, OSError):
        return False


def _find_pythonw():
    """查找 pythonw.exe，优先用当前解释器同目录"""
    # 1. 当前 python.exe 同目录下的 pythonw.exe（最可靠）
    exe_dir = os.path.dirname(sys.executable)
    pw = os.path.join(exe_dir, "pythonw.exe")
    if os.path.isfile(pw):
        return pw
    # 2. 当前解释器的 conda 环境根目录
    #    sys.executable 可能在 envs/xxx/Scripts/python.exe 或 envs/xxx/python.exe
    env_dir = exe_dir
    for _ in range(3):  # 向上最多找 3 层
        parent = os.path.dirname(env_dir)
        if parent == env_dir:
            break
        env_dir = parent
        pw2 = os.path.join(env_dir, "pythonw.exe")
        if os.path.isfile(pw2):
            return pw2
    # 3. 常见 conda 环境路径
    conda_candidates = [
        os.path.expanduser(r"~\.conda\envs\query\pythonw.exe"),
        os.path.expanduser(r"~\anaconda3\pythonw.exe"),
        os.path.expanduser(r"~\miniconda3\pythonw.exe"),
    ]
    for candidate in conda_candidates:
        if os.path.isfile(candidate):
            return candidate
    # 4. fallback 到 PATH
    return "pythonw.exe"


def set_auto_start(enabled):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE)
        if enabled:
            script = get_script_path()
            pythonw = _find_pythonw()
            # 直接用 pythonw.exe + 完整路径，不依赖 .vbs 文件关联
            cmd = f'"{pythonw}" "{script}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except OSError as e:
        print(f"设置开机自启失败: {e}")
        return False
