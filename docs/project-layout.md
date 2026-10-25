# 项目文件与归档

当前版本：2.1.0。日常启动入口是项目根目录的 `SSDMonitor.exe`。

## 当前目录

| 位置 | 用途 |
| --- | --- |
| `SSDMonitor.exe` | 当前 Windows 便携程序，双击启动 |
| `使用说明.txt`、`README.md` | 操作说明与项目介绍 |
| 根目录 Python 模块 | 当前源码；开发入口为 `disk_monitor.py` |
| `build.ps1`、`ssd_monitor.spec` | 打包脚本与配置 |
| `requirements*.txt` | 运行与构建依赖 |
| `assets/` | 程序图标、版本信息 |
| `tests/` | 统计、恢复和悬停浮窗测试 |
| `docs/` | 当前目录说明与界面示例 |
| `third_party/` | 随包第三方许可证和源码 |
| `data/config.json` | 当前配置、每盘状态、待写队列 |
| `data/v2/` | 当前多硬盘 Excel 日志 |
| `data/backups/` | 配置迁移自动备份；保留配置中引用的位置 |
| `build/` | 当前构建缓存和验证报告，本地保留 |
| `dist/` | 当前 exe 和便携压缩包，本地生成 |
| `old_versions/` | 历史版本及旧文件归档，仅在本地保留 |

当前源码保留在根目录，运行入口、相对导入和打包路径保持一致。
`__pycache__/` 是 Python 自动生成的缓存，已由 Git 忽略。

## 本地旧版归档

- `old_versions/v1/source-586a386.zip`：旧版完整 Git 源码快照。
- `old_versions/v1/source-backups/`：早期 Python 备份。
- `old_versions/v1/scripts/`：旧 `启动.vbs`、`merge_docs.py`、`verify_doc.py`。
- `old_versions/v1/docs/`：早期设计文档、开发记录、文档检查输出。
- `old_versions/v1/data/`：旧版单盘 Excel 日志与损坏配置原件。
- `old_versions/2.0.0-before-tooltip-fix/`：2.0.0 源码、exe、便携包与旧自检报告。
- `old_versions/2.0.1-before-column-popup/`：2.0.1 源码、程序、便携包与旧原生提示模块。
- `old_versions/archive-manifest-2026-10-25.json`：本次旧文件移动清单及 SHA-256。

归档文件不会提交到 GitHub，也不会被打进当前便携包。需要查看历史时直接进入相应目录。
当前监控使用 `data/config.json` 和 `data/v2/`，旧单盘日志仅作为历史保留。
