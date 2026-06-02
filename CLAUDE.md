# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Windows 桌面 SSD 写入量监控工具。后台常驻系统托盘，定时通过 `smartctl` 读取 SMART 数据（主机写入量），记录到 xlsx 文件，支持每日统计、趋势分析和开机自启。

## 开发环境

- **Python 环境**: `conda run -n query python` (conda 环境名为 `query`)
- **依赖**: `pystray>=0.19.0`, `Pillow>=9.0.0`, `openpyxl` (requirements.txt 只列了前两个，openpyxl 是隐式依赖)
- **外部依赖**: [smartmontools](https://www.smartmontools.org/) — `smartctl.exe` 需要在 PATH 中或项目目录下

## 常用命令

```bash
# 运行程序（开发调试）
conda run -n query python disk_monitor.py

# 使用 pythonw 无窗口运行
conda run -n query pythonw disk_monitor.py

# PyInstaller 打包为单 exe（spec 文件方式）
conda run -n query pyinstaller ssd_monitor.spec
```

## 架构

```
disk_monitor.py   主程序入口 — 托盘 UI + 监控循环 + 状态管理
smart_reader.py   SMART 数据读取 — 封装 smartctl 调用，解析 NVMe/SATA 输出
csv_logger.py     xlsx 日志写入 — write_log.xlsx / daily_summary.xlsx / trend_summary.xlsx
config_manager.py 配置管理 — config.json 读写，运行状态持久化到 _state 字段
auto_start.py     开机自启 — Windows 注册表 HKCU\...\Run
启动.vbs          VBS 启动脚本 — 无窗口启动 pythonw
data/             数据目录 — config.json + xlsx 日志文件
```

### 核心流程

1. `disk_monitor.main()` → 加载配置、启动监控线程、创建托盘图标
2. `monitor_loop()` 每 N 秒调用 `collect_once()`
3. `collect_once()` → `smart_reader.read_smart()` 获取累计字节数 → 计算增量 → 调用 `csv_logger` 写入三种日志
4. 日期变更检测：跨日时用 `_state` 中的 `last_total_bytes` 补生成前一天的 daily_summary

### 关键设计决策

- **状态持久化**: 运行状态存储在 `config.json` 的 `_state` 字段，每次采集后立即写盘，断电/崩溃不丢数据
- **进程互斥**: 通过 `data/ssd_monitor.lock` 文件 + PID 检测实现单实例运行
- **日志格式**: 使用 xlsx (openpyxl) 而非 CSV，支持中文列名和自动列宽
- **SMART 解析**: NVMe 用 `Data Units Written`（每 unit = 512,000 bytes），SATA 用 F1 属性（32MiB 单位）

## 注意事项

- 所有 subprocess 调用需隐藏窗口：`STARTUPINFO` + `STARTF_USESHOWWINDOW` + `creationflags=0x08000000`
- `_state` 通过 `config_manager.save_state()` 保存，会同时更新内存 config 和磁盘文件
- 设计文档在 `docs/superpowers/specs/2026-05-31-ssd-write-monitor-design.md`
