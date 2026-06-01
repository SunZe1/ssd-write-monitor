# SSD Write Monitor

Windows 桌面 SSD 写入量监控工具。后台常驻系统托盘，定时通过 smartctl 读取 SMART 数据（主机写入量），记录到 xlsx 文件，支持每日统计、趋势分析和开机自启。

## 功能特性

- 🔍 **SMART 数据采集** — 支持 NVMe 和 SATA 硬盘，自动检测 smartctl 路径
- 📊 **三维度日志** — 写入日志（write_log）、每日汇总（daily_summary）、趋势分析（trend_summary）
- ⏱️ **灵活采集间隔** — 支持 5 / 10 / 30 / 60 分钟，可在托盘菜单切换
- ⏸️ **暂停/恢复** — 临时暂停监控，不影响已有数据
- 🖥️ **系统托盘常驻** — 最小化到托盘，实时显示写入量提示
- 🚀 **开机自启** — 通过 Windows 注册表实现，一键开关
- 💾 **断电不丢数据** — 运行状态持久化到 config.json，崩溃后自动恢复
- 🔄 **进程互斥** — 锁文件机制防止多实例运行

## 环境要求

- Windows 10/11
- Python 3.x（推荐使用 conda 环境）
- [smartmontools](https://www.smartmontools.org/)（smartctl.exe 需在 PATH 或项目目录中）
- 管理员权限（读取 SMART 数据需要）

## 安装

`ash
# 克隆仓库
git clone https://github.com/SunZe1/ssd-write-monitor.git
cd ssd-write-monitor

# 安装依赖
pip install -r requirements.txt

# 安装 smartmontools（如未安装）
# 下载地址：https://www.smartmontools.org/wiki/Download
# 确保 smartctl.exe 在 PATH 中，或将其放在项目目录下
`

## 使用

`ash
# 开发运行
python disk_monitor.py

# 无窗口运行（推荐后台使用）
pythonw disk_monitor.py

# 使用 conda 环境
conda run -n query python disk_monitor.py
`

### 托盘菜单功能

| 菜单项 | 说明 |
|--------|------|
| 今日统计 | 查看当天写入量 |
| 打开日志目录 | 快速访问 xlsx 日志文件 |
| 采集间隔 | 切换 5/10/30/60 分钟 |
| 进程跟踪 | 记录写入来源进程 |
| 开机自启 | 注册/取消开机启动 |
| 暂停监控 | 临时停止采集 |
| 退出 | 关闭程序 |


## 文件结构

`
├── disk_monitor.py       # 主程序：托盘 UI + 监控循环 + 状态管理
├── smart_reader.py       # SMART 读取：smartctl 调用与 NVMe/SATA 解析
├── csv_logger.py         # 日志写入：xlsx 格式的三种日志
├── config_manager.py     # 配置管理：config.json 读写与状态持久化
├── auto_start.py         # 开机自启：Windows 注册表操作
├── 启动.vbs              # VBS 启动脚本：无窗口启动 pythonw
├── requirements.txt      # Python 依赖
└── data/                 # 运行时数据目录（自动生成）
    ├── config.json       # 配置与运行状态
    ├── write_log.xlsx    # 写入详细日志
    ├── daily_summary.xlsx # 每日汇总
    └── trend_summary.xlsx # 趋势分析
`

## 日志格式

### write_log.xlsx
每次采集记录：时间戳、磁盘、累计写入量、本次增量、当日累计、温度、来源进程

### daily_summary.xlsx
每日汇总：日期、磁盘、当日写入量、起始累计、结束累计

### trend_summary.xlsx
趋势分析：日期、日均写入量、累计写入量、采集次数

## 依赖

`
pystray>=0.19.0
Pillow>=9.0.0
psutil>=5.9.0
openpyxl  # xlsx 读写（隐式依赖）
`

## License

MIT


