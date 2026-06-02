import os
base = r"C:\Users\SunZe\Desktop\codex-p\disk-monitor\docs"
p1 = os.path.join(base, "development-log.md")
p2 = os.path.join(base, "development-log-0601.md")
with open(p1, "r", encoding="utf-8-sig") as f:
    part1 = f.read().rstrip()
with open(p2, "r", encoding="utf-8-sig") as f:
    part2 = f.read()
with open(p1, "w", encoding="utf-8") as f:
    f.write(part1 + "\n\n" + part2)
os.remove(p2)
print("Merged OK")
