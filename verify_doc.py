f = open(r"C:\Users\SunZe\Desktop\codex-p\disk-monitor\docs\development-log.md", "r", encoding="utf-8")
lines = f.readlines()
f.close()
out = open(r"C:\Users\SunZe\Desktop\codex-p\disk-monitor\docs\verify.txt", "w", encoding="utf-8")
out.write(str(len(lines)) + " lines\n")
for line in lines:
    if line.startswith("## 2026"):
        out.write(line)
out.close()
