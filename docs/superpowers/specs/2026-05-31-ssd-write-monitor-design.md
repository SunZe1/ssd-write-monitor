# SSD 写入量监控工具 - 设计文档

日期: 2026-05-31

## 概述

Windows 桌面小程序，后台常驻 + 系统托盘，每 10 分钟定时采集 SSD 的 SMART 数据（主机写入量），记录到 CSV 文件，支持每日统计和开机自启。

## 技术选型

- **语言**: Python 3.x
- **SMART 数据**: smartmontools (`smartctl`) 便携版，与 CrystalDiskInfo 同源数据
- **托盘界面**: `pystray` + `Pillow`
- **打包**: PyInstaller（最终输出单个 exe）
- **开机自启**: Windows 注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`

## 数据来源

SSD SMART 属性中的「主机写入量」（Total Host Writes / Data Units Written），通过 `smartctl -A <disk>` 读取。这是 SSD 控制器硬件记录的累计写入字节数，与 CrystalDiskInfo 显示一致。

## 文件结构

```
disk-monitor/
├── disk_monitor.py        # 主程序入口
├── smart_reader.py        # SMART 数据读取模块
├── tray_app.py            # 系统托盘界面
├── config.json            # 用户配置
├── smartctl.exe           # smartmontools 便携版（打包时内置）
├── data/
│   ├── write_log.csv      # 每次采集的详细记录
│   └── daily_summary.csv  # 每日汇总
└── icon.ico               # 托盘图标
```

## CSV 数据格式

### write_log.csv（详细记录）

| 列名 | 说明 | 示例 |
|------|------|------|
| timestamp | 采集时间 | 2026-05-31 14:00:00 |
| disk | 物理磁盘标识 | \\.\PHYSICALDRIVE0 |
| total_written_gb | 累计写入(GB) | 1523.45 |
| total_written_tb | 累计写入(TB) | 1.49 |
| delta_gb | 较上次采集增量(GB) | 0.32 |
| today_gb | 今日累计增量(GB) | 12.50 |

### daily_summary.csv（每日汇总）

| 列名 | 说明 | 示例 |
|------|------|------|
| date | 日期 | 2026-05-31 |
| disk | 物理磁盘标识 | \\.\PHYSICALDRIVE0 |
| today_written_gb | 当日写入量(GB) | 45.23 |
| today_written_tb | 当日写入量(TB) | 0.04 |
| day_start_total_tb | 日初累计(TB) | 1.48 |
| day_end_total_tb | 日末累计(TB) | 1.53 |

## 采集逻辑

1. 程序启动 → 读取 smartctl 获取当前累计写入量（字节）
2. 与上次记录对比 → 计算增量 (delta)
3. 写入 write_log.csv
4. 每天 00:00 自动生成当天的 daily_summary 行
5. 等待 10 分钟 → 重复

**边界处理**:
- 首次运行：delta 为 0，today 从 0 开始
- 跨日：today_gb 重置，记录前一天的 daily_summary
- 磁盘未就绪/smartctl 失败：记录错误日志，下次重试
- 系统休眠/唤醒：检测时间跳跃，超过 30 分钟则重新采集

## 托盘界面

### 悬停提示（Tooltip）

```
SSD 写入监控
今日写入: 12.50 GB (0.01 TB)
本月写入: 156.32 GB (0.15 TB)
累计写入: 1.53 TB
```

### 右键菜单

| 菜单项 | 功能 |
|--------|------|
| 📊 查看今日统计 | 弹窗显示今日详细数据 |
| 📂 打开日志目录 | 资源管理器打开 data/ 目录 |
| ⏸️ 暂停监控 | 暂停采集，图标变灰 |
| ▶️ 恢复监控 | 恢复采集 |
| ✅ 开机自启 | 勾选状态，点击切换开关 |
| ❌ 退出 | 退出程序 |

### 开机自启实现

通过注册表实现：
- 路径: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- 键名: `SSDMonitor`
- 键值: exe 完整路径
- 开启：写入注册表
- 关闭：删除注册表键
- 菜单显示勾选状态反映当前设置

## 打包分发

使用 PyInstaller 打包：
- `--onefile` 单 exe
- `--windowed` 无控制台窗口
- smartctl.exe 通过 `--add-data` 打包
- icon.ico 通过 `--icon` 设置

## 非功能需求

- CPU 占用极低（大部分时间 sleep）
- 内存占用 < 50MB
- 不弹出命令行窗口
- 程序退出后 CSV 数据保留
- 支持 Windows 10/11
## 每日统计时机（日期变更检测）

不采用固定时间点（如 00:00）生成每日汇总，而是基于**日期变更检测**：

### 机制

每次采集时：
1. 获取当前日期 `today`
2. 与上次采集的日期 `last_date` 比较
3. 如果 `today != last_date`（检测到日期变化）：
   - 用上一次采集的累计值作为「昨天的日末累计」
   - 用当天第一次采集的累计值减去日初累计，得到「昨天的今日写入量」
   - 生成昨天的 daily_summary 行
   - 重置 today_gb 为 0，开始新的一天
4. 如果日期未变化：正常累加 today_gb

### 关键数据持久化

在 `config.json` 中追加运行状态字段（程序退出/崩溃后可恢复）：
```json
{
  "interval_minutes": 10,
  "disk": "\\\\.\\PHYSICALDRIVE0",
  "auto_start": false,
  "_state": {
    "last_total_bytes": 536870912000,
    "last_date": "2026-05-31",
    "day_start_bytes": 534454198272,
    "today_bytes": 2416713728,
    "today_gb": 2.25
  }
}
```

### 场景覆盖

| 场景 | 行为 |
|------|------|
| 正常运行跨日 | 00:00 后首次采集触发日期变更检测 |
| 23:30 关机 | 下次开机检测到日期变化，用 23:30 数据补生成昨天汇总 |
| 休眠后第二天唤醒 | 检测到日期变化，补生成后继续监控 |
| 多天未开机 | 只补最近一天的汇总（因为中间无数据点，无法统计） |
| 首次运行 | `_state` 为空，初始化为当前日期和累计值 |
## 采集间隔设置（托盘菜单）

托盘菜单增加「⏱ 采集间隔」子菜单：

| 选项 | 说明 |
|------|------|
| ⏱ 5 分钟 | 更精细监控 |
| ✅ 10 分钟 | 默认值，勾选状态 |
| ⏱ 30 分钟 | 省资源 |
| ⏱ 60 分钟 | 最省资源 |

选择后立即生效，保存到 `config.json` 的 `interval_minutes` 字段，下次启动沿用。

## 趋势统计文件

新增 `data/trend_summary.csv`，每天一行，清晰展示近期写入趋势：

| 列名 | 说明 | 示例 |
|------|------|------|
| date | 日期 | 2026-05-31 |
| written_gb | 当日写入量(GB) | 45.23 |
| written_tb | 当日写入量(TB) | 0.04 |
| cumulative_tb | 累计写入(TB) | 1.53 |
| avg_7d_gb | 近7天日均写入(GB) | 38.50 |
| readings | 当天采集次数 | 144 |

用户用 Excel 打开 trend_summary.csv 即可直接看到：
- 每天写入了多少
- 近 7 天日均写入量趋势
- 累计增长曲线

## 状态持久化（防断电丢数据）

`config.json` 中的 `_state` 字段在**每次采集后立即写入磁盘**，不依赖内存缓存。

启动时读取 `_state`，对比当前日期和 `last_date`：
- 日期相同 → 继续累加 today
- 日期不同 → 用 `_state` 中的 last_total_bytes 作为昨天的数据，生成 daily_summary，重置 today

**多天未开机的处理**：
- 如果 `today - last_date > 1天`：说明中间几天电脑没开，SSD 没有写入
- 只为 `last_date` 生成 daily_summary（因为只有那天有数据）
- `today` 作为新一天开始，today_gb 从 0 开始
- 中间空缺的天数不生成 daily_summary（无数据 = 无写入，不产生误导）
