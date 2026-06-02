"""进程写入量追踪模块

通过 psutil 采样所有进程的 write_bytes，两次快照做差得到区间写入增量，
排序取 Top N 输出。
"""

import psutil


def snapshot():
    """对所有进程做一次 write_bytes 快照，返回 {pid: {"name": str, "write_bytes": int}}"""
    snap = {}
    for p in psutil.process_iter(["pid", "name"]):
        try:
            io = p.io_counters()
            snap[p.info["pid"]] = {
                "name": p.info["name"],
                "write_bytes": io.write_bytes,
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError, OSError):
            # 系统进程或已退出的进程，跳过
            pass
    return snap


def compute_delta(prev_snap, curr_snap, top_n=5):
    """对比两次快照，返回写入增量最大的 top_n 个进程。

    返回格式: [{"name": str, "pid": int, "delta_bytes": int}, ...]
    """
    deltas = []
    for pid, info in curr_snap.items():
        if pid in prev_snap:
            prev_bytes = prev_snap[pid]["write_bytes"]
            delta = info["write_bytes"] - prev_bytes
            if delta > 0:
                deltas.append({
                    "name": info["name"],
                    "pid": pid,
                    "delta_bytes": delta,
                })

    deltas.sort(key=lambda x: x["delta_bytes"], reverse=True)
    return deltas[:top_n]
