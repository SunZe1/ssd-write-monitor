import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

DEFAULT_CONFIG = {
    "interval_minutes": 10,
    "disk": r"\\.\PHYSICALDRIVE0",
    "auto_start": False,
    "track_processes": True,
    "_state": None,
}


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_config():
    ensure_dirs()
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # 补全缺失字段
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    ensure_dirs()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def load_state(cfg):
    """从 config 的 _state 字段加载运行状态"""
    return cfg.get("_state")


def save_state(cfg, state):
    """将运行状态保存到 config 的 _state 字段并立即写磁盘"""
    cfg["_state"] = state
    save_config(cfg)
