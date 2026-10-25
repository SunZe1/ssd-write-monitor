# AGENTS.md

## 项目概述

Windows 多 SSD 写入监控，常驻系统托盘。当前版本 2.1.0，日常启动入口为根目录 SSDMonitor.exe，开发入口为 disk_monitor.py。

## 开发环境与命令

Windows 11，PowerShell 7，Python 使用 conda query 环境。依赖包括 pystray、Pillow、psutil、openpyxl；构建依赖 PyInstaller。源码运行需要 smartctl，便携 exe 已内置。

```powershell
conda run -n query python disk_monitor.py
conda run -n query pythonw disk_monitor.py
conda run -n query python -m unittest discover -s tests -v
./build.ps1
```

## 模块

- disk_monitor.py：Tk 管理窗口、托盘、调度和单实例锁。
- tray_popup.py：按硬盘分列的悬停浮窗，每列依次显示今日、近七天、健康度、总写入。
- monitor_service.py：每盘状态、统计、待写队列、历史缓存、故障隔离。
- weekly_stats.py：七个自然日的已确认写入求和与去重。
- smart_reader.py：smartctl JSON、设备身份、健康度与盘符映射。
- csv_logger.py：多盘 xlsx 批量导出、去重、历史读取。
- config_manager.py：原子配置写入与旧版数据迁移。
- auto_start.py：Windows HKCU Run 开机自启。
- process_io_tracker.py：整机进程 I/O 参考。
- diagnostics.py：使用临时数据目录的源码与 exe 自检。

## 关键约定

- 所有 subprocess 调用需隐藏窗口：STARTUPINFO + STARTF_USESHOWWINDOW + creationflags=0x08000000。
- Tk 界面只能在主线程操作，硬盘采集和工作簿读取在后台执行；悬停仅使用缓存快照。
- 配置、每盘状态和待写队列一次原子保存，保存成功后才更新内存基线。
- 每盘使用稳定身份；跨日未知区间写入保留为未分配量，不强行计入某一天。
- 近七天含今天，仅累加已确认写入，合并每日汇总、待写队列和当前状态；按日期与硬盘去重。
- NVMe Data Units Written 每单位为 512,000 字节；SATA 仅接受明确标注单位的属性。
- 根目录 SSDMonitor.exe 与 data 相邻；当前日志在 data/v2，配置仍会引用 data/backups 中的升级备份。
- 旧版归档统一放 old_versions，已被 Git 忽略；目录说明见 docs/project-layout.md。
- tests 使用临时目录、模拟硬盘和注入的历史读取器，避免修改真实监控数据。
