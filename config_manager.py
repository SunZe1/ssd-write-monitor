"""配置、每盘状态和待写日志一起原子保存。"""
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
CONFIG_LOCK = threading.RLock()
DEFAULT_CONFIG = {
    "schema_version": 2,
    "interval_minutes": 10,
    "auto_start": False,
    "track_processes": True,
    "disks": {},
    "_state": {"disks": {}, "pending_batches": []},
}


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)


def _atomic_write_text(filepath, text):
    """保留先写临时文件、fsync、再替换的保护；每次使用独立临时文件。"""
    directory = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, filepath)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _backup_legacy():
    """任一备份失败就中止升级，原始文件不做改写。"""
    folder = Path(DATA_DIR) / "backups" / datetime.now().strftime("legacy-%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    for name in ("config.json", "write_log.xlsx", "daily_summary.xlsx", "trend_summary.xlsx"):
        source = Path(DATA_DIR) / name
        if source.exists():
            shutil.copy2(source, folder / name)
    return str(folder)


def load_config():
    with CONFIG_LOCK:
        ensure_dirs()
        if not os.path.exists(CONFIG_FILE):
            return copy.deepcopy(DEFAULT_CONFIG)
        try:
            with open(CONFIG_FILE, encoding="utf-8-sig") as stream:
                cfg = json.load(stream)
            if not isinstance(cfg, dict):
                raise ValueError("配置必须为 JSON 对象")
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            backup = CONFIG_FILE + datetime.now().strftime(".%Y%m%d-%H%M%S-%f.bad")
            shutil.copy2(CONFIG_FILE, backup)
            cfg = copy.deepcopy(DEFAULT_CONFIG)
            cfg["recovery_notice"] = f"配置损坏，原文件保存在 {backup}"
            save_config(cfg)
        version = cfg.get("schema_version", 1)
        if version not in (1, 2):
            raise ValueError(f"不支持的配置版本：{version}")
        if version == 1:
            backup = _backup_legacy()
            cfg["legacy"] = {
                "backup_directory": backup,
                "disk_label": cfg.pop("disk", "未知"),
                "state": cfg.pop("_state", None),
                "note": "旧版未记录硬盘唯一身份，历史记录保留且未绑定到新盘。",
            }
            cfg["schema_version"] = 2
        for key, value in DEFAULT_CONFIG.items():
            cfg.setdefault(key, copy.deepcopy(value))
        if not isinstance(cfg["disks"], dict) or not isinstance(cfg["_state"], dict):
            raise ValueError("硬盘配置或运行状态格式错误，请检查配置备份")
        state = cfg["_state"]
        state.setdefault("disks", {})
        state.setdefault("pending_batches", [])
        if not isinstance(state["disks"], dict) or not isinstance(state["pending_batches"], list):
            raise ValueError("运行状态格式错误，未覆盖原配置")
        if cfg.get("interval_minutes") not in (5, 10, 30, 60):
            cfg["interval_minutes"] = 10
        if version == 1:
            save_config(cfg)
        return cfg


def save_config(cfg):
    with CONFIG_LOCK:
        ensure_dirs()
        _atomic_write_text(CONFIG_FILE, json.dumps(cfg, indent=2, ensure_ascii=False, allow_nan=False))


def load_state(cfg):
    return cfg["_state"]


def save_state(cfg, state):
    """磁盘写入成功后才更新调用者的内存状态。"""
    with CONFIG_LOCK:
        updated = dict(cfg, _state=state)
        save_config(updated)
        cfg["_state"] = state
