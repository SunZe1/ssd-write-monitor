Dim ws
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = "C:\Users\SunZe\Desktop\codex-p\disk-monitor"
ws.Run "C:\Users\SunZe\.conda\envs\query\pythonw.exe disk_monitor.py", 0, False