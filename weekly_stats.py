"""近七个自然日的已确认写入总量，合并已导出记录和最新状态。"""
from datetime import timedelta

GIB = 1024 ** 3


def weekly_totals(history, batches, states, today):
    cutoff, end = (today - timedelta(days=6)).isoformat(), today.isoformat()
    records = {}

    def accept(row):
        day = row["date"]
        if not cutoff <= day <= end:
            return
        key = (row["disk_id"], day)
        previous = records.get(key)
        if previous is None or row.get("sampled_at", "") >= previous.get("sampled_at", ""):
            records[key] = row

    for row in history:
        accept(dict(row, written_bytes=round(row["written_gib"] * GIB)))
    for batch in batches:
        for row in batch.get("days", []):
            accept(row)
    for disk_id, state in states.items():
        if state.get("last_date"):
            accept(dict(disk_id=disk_id, date=state["last_date"],
                        sampled_at=state["last_sample_at"], written_bytes=state["today_bytes"]))
    totals = {}
    for (disk_id, day), row in records.items():
        total = totals.setdefault(disk_id, {"bytes": 0, "days": 0})
        total["bytes"] += row["written_bytes"]
        total["days"] += 1
    return totals
