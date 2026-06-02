# SSD Monitor 开发记录

> 记录开发过程中遇到的难题、Bug 及解决方案，沉淀开发经验。

---

## 2026-05-31：核心功能搭建

### 一、项目技术选型

| 模块 | 方案 | 理由 |
|------|------|------|
| SMART 数据 | smartctl (smartmontools) | 与 CrystalDiskInfo 同源，NVMe/SATA 均支持 |
| 托盘界面 | pystray + Pillow | Python 原生托盘库，轻量 |
| 数据存储 | 最终选择 xlsx (openpyxl) | Excel 直接打开，自动列宽，无需手动调整 |
| 开机自启 | Windows 注册表 Run 键 | 标准方案，无需管理员权限 |
| 打包 | PyInstaller | 单 exe 分发，用户无需装 Python |

---

### 二、遇到的 Bug 及解决方案

#### Bug 1：bat 启动后托盘图标不可见

**现象：** 双击 bat 文件后，任务栏看不到托盘图标。

**根因：** Windows 11 会将新托盘图标默认收入"隐藏的图标"区域（^ 箭头）。

**解决：** 这不是代码 Bug，是 Windows 11 行为。在代码中添加启动通知气泡引导用户查看隐藏区域。

**经验：** Windows 11 的托盘图标管理与 Win10 不同，新应用图标默认不显示在任务栏，需要用户手动拖出或通过设置固定。

---

#### Bug 2：bat 启动后关闭窗口导致进程被杀

**现象：** 用户关闭 cmd 窗口，Python 进程一起被终止。

**根因：** `python disk_monitor.py` 直接在 cmd 子进程中运行，关闭父窗口会终止所有子进程。

**解决：** 改用 `start "" "pythonw.exe" disk_monitor.py` 让 Python 脱离 bat 窗口独立运行。最终改用 VBScript (`wscript.exe`) 启动，`Run cmd, 0, False` 的参数 `0` 表示隐藏窗口。

**经验：**
- bat 中用 `start` 命令分离子进程
- pythonw.exe 比 python.exe 更适合后台应用（无控制台窗口）
- VBScript 的 `ws.Run cmd, 0, False` 是最干净的无窗口启动方式

---

#### Bug 3：PowerShell 写文件导致 UTF-8 BOM 问题

**现象：** Python 报错 `JSONDecodeError: Unexpected UTF-8 BOM`。

**根因：** PowerShell 的 `Set-Content -Encoding utf8` 默认会写入 BOM (Byte Order Mark)，Python 的 `json.load()` 不接受。

**解决：** 使用 .NET 方法写文件，显式指定无 BOM：
```csharp
[System.IO.File]::WriteAllText("config.json", $json, (New-Object System.Text.UTF8Encoding $false))
```

**经验：**
- PowerShell 的 UTF-8 编码默认带 BOM，Python 生态普遍不兼容 BOM
- 涉及跨语言文件读写时，始终用无 BOM 的 UTF-8
- 或在 Python 端用 `utf-8-sig` 编码读取（自动处理 BOM）

---

#### Bug 4：conda run 不支持多行 Python 脚本

**现象：** `conda run -n query python -c "多行代码"` 报错 `NotImplementedError: Support for scripts where arguments contain newlines not implemented`。

**根因：** `conda run` 的 `-c` 参数不支持换行符，这是 conda 的已知限制。

**解决：** 将测试代码写入 `.py` 文件，再用 `conda run -n query python test.py` 执行。

**经验：** 测试代码超过一行时，永远写入文件再执行，不要用 `-c` 内联。

---

#### Bug 5：xlsx 文件被 Excel 打开时写入失败

**现象：** 用户用 Excel 打开 xlsx 文件时，程序报 PermissionError。

**根因：** Excel 打开文件时会独占锁定，openpyxl 的 `wb.save()` 无法写入。

**解决：** 封装 `_safe_save()` 函数，遇到 PermissionError 自动重试 3 次，每次间隔 2 秒，最终失败时打印警告但不崩溃。

**经验：**
- 对用户可能打开的文件，必须处理文件锁定
- 重试机制比直接失败更友好
- 考虑写入临时文件再原子替换的方案（更高级）

---

#### Bug 6：托盘菜单弹窗无法关闭

**现象：** 右键托盘"查看今日统计"弹出的 `ctypes.windll.user32.MessageBoxW` 窗口，点击确定和×都无法关闭。

**根因：** `MessageBoxW` 在 pystray 的回调线程中调用，该线程没有 Windows 消息循环（message loop），导致消息框无法处理按钮事件。

**尝试过的方案：**

| 方案 | 结果 |
|------|------|
| `ctypes.windll.user32.MessageBoxW` | ❌ 阻塞，无法关闭 |
| `tkinter.messagebox.showinfo` | ❌ tkinter 在非主线程也会卡死 |
| PowerShell `System.Windows.Forms.MessageBox` | ✅ 独立进程，不阻塞 |

**最终方案：** 写入临时 `.ps1` 文件，用 `subprocess.Popen` 启动 PowerShell 进程显示 Windows Forms MessageBox。

**经验：**
- pystray 的回调运行在非主线程，不能直接创建 GUI 窗口
- tkinter 不是线程安全的，只能在主线程使用
- 跨线程弹窗需要用独立进程（subprocess）实现
- `creationflags=0x08000000` (`CREATE_NO_WINDOW`) 隐藏 PowerShell 控制台窗口

---

#### Bug 7：VBS 启动脚本路径引号导致解析失败

**现象：** VBS 脚本双击后无任何反应，Python 进程未启动。

**根因：** VBS 中的 `ws.Run """C:\path\to\pythonw.exe"" disk_monitor.py", 0, False` 路径嵌套了多层引号，VBScript 解析器无法正确处理。

**最终方案：** 简化 VBS，用硬编码路径避免引号嵌套：
```vbs
ws.CurrentDirectory = "C:\Users\...\disk-monitor"
ws.Run "C:\...\pythonw.exe disk_monitor.py", 0, False
```

**经验：**
- VBScript 的字符串引号规则与 Python/C 完全不同，用 `""` 转义
- 涉及路径拼接时，优先设置 `CurrentDirectory` 而不是拼绝对路径
- 简单直接比灵活通用更可靠

---

### 三、架构决策记录

#### CSV vs xlsx 存储

最初同时维护 CSV 和 xlsx，后来改为只保留 xlsx：

| 维度 | CSV | xlsx |
|------|-----|------|
| 通用性 | ✅ 任何工具都能打开 | ❌ 需要 Excel 或兼容软件 |
| 列宽 | ❌ Excel 打开需要手动拉宽 | ✅ 自动适配 |
| 数据类型 | ❌ 全是字符串 | ✅ 数字/日期自动识别 |
| 编码问题 | ❌ UTF-8 BOM 常出问题 | ✅ 无编码问题 |
| 追加写入 | ✅ 直接 append | ❌ 需要 load_workbook |

**结论：** 用户场景是 Excel 查看，xlsx 是更好的选择。用 openpyxl 每次 load → append → save，文件量小时性能可接受。

---

#### 日期变更检测 vs 固定时间统计

**方案对比：**

| 方案 | 优点 | 缺点 |
|------|------|------|
| 固定 00:00 统计 | 简单 | 关机时丢失当天统计 |
| **日期变更检测** | 关机/休眠不丢数据 | 逻辑稍复杂 |

**采用日期变更检测：** 每次采集对比当前日期和上次日期，发现跨日时自动生成昨天的汇总。无论何时关机、开机、休眠，数据都不会丢失。

---

#### 进程互斥锁方案

| 方案 | 结果 |
|------|------|
| Windows Mutex (`CreateMutexW`) | ❌ pythonw 进程间 mutex 不生效 |
| `Global\` 前缀 mutex | ❌ 可能需要管理员权限 |
| **锁文件 + PID 检测** | ✅ 简单可靠 |

**最终方案：** `data/ssd_monitor.lock` 文件记录 PID，启动时检查 PID 是否存活，存活则提示"已在运行中"并退出。

---

### 四、开发经验总结

#### 1. Windows 后台应用的隐形坑

- pythonw.exe 是后台应用的正确选择，但要注意它没有任何输出通道（stdout/stderr 被丢弃）
- 调试阶段用 python.exe + `pause` 的 bat，确认无误后切回 pythonw.exe
- `CREATE_NO_WINDOW` (0x08000000) 标志对 subprocess 启动的子进程同样重要

#### 2. 跨语言文件交换的编码陷阱

- PowerShell 写 UTF-8 默认带 BOM
- Python 读 UTF-8 默认不接受 BOM
- 统一用 `[System.IO.File]::WriteAllText(path, content, (New-Object System.Text.UTF8Encoding $false))` 或 Python 端用 `utf-8-sig`

#### 3. GUI 框架的线程安全

- pystray 回调在非主线程 → 不能用 tkinter、不能用 MessageBoxW
- tkinter 只能在主线程运行
- 跨线程弹窗必须用独立进程

#### 4. 文件锁定的防御性编程

- 用户随时可能用 Excel 打开日志文件
- 所有写入操作都需要 try/except PermissionError
- 重试机制 + 优雅降级（跳过本次写入，下次继续）

#### 5. 进程管理的可靠性

- 锁文件比 mutex 更可靠（跨语言、跨权限级别）
- 锁文件必须配合 PID 检测（防止残留锁文件）
- 正常退出和异常退出都要清理锁文件（try/finally）

#### 6. VBScript 路径处理

- 避免多层引号嵌套
- 用 `CurrentDirectory` 设置工作目录比拼接相对路径更可靠
- 简单硬编码比动态拼接更不容易出错

---

## 2026-06-01：进程写入量追踪 + 开机自启动修复

### 一、新功能：进程写入来源追踪

#### 需求

在每 10 分钟的 SMART 采集周期内，记录哪些进程写了最多数据，直接写入 `write_log.xlsx` 最右侧 3 列（从左到右递减）。

#### 方案选型

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| **psutil 周期采样** | 轻量、纯 Python、无额外权限 | 短命进程会漏掉 | ✅ 采用 |
| ETW 实时追踪 | 100% 精确 | 需管理员权限、实现复杂、库不稳定 | ❌ 过重 |
| handle.exe 快照 | 能看文件路径 | 外部依赖重、解析脆弱 | ❌ 过重 |

#### 实现要点

- `psutil.process_iter()` 采集所有进程的 `write_bytes` 快照
- 两次快照做差得到区间增量，排序取 Top 3
- 格式：`进程名(写入量)`，MB/GB 自动切换
- 通过托盘菜单 `✅ 记录写入进程` 控制开关

#### 注意事项

- 第一次采集无数据（没有上次快照做对比），第二次开始正常
- `psutil.io_counters()` 对部分系统进程会抛 `AccessDenied`，需 try/except 静默跳过

---

### 二、Bug 修复：开机自启动弹出 VS Code

#### 现象

开启开机自启后，重启电脑时 VS Code 弹出并显示 `启动.vbs` 的代码内容，应用并未自启动。

#### 根因分析

**问题链路：**

```
注册表 Run 键
  → wscript.exe //B "启动.vbs"
    → Windows 查询 .vbs 文件关联
      → VS Code 是默认打开程序（而非 wscript.exe）
        → VS Code 以编辑模式打开文件
          → 应用未启动
```

核心原因：**`.vbs` 文件的默认打开方式被 VS Code 劫持了。** Windows 在执行 `wscript.exe //B "启动.vbs"` 时，某些情况下会走文件关联而非直接调用 wscript，导致 VS Code 拦截。

#### 解决方案

**彻底绕过 VBS，直接用 pythonw.exe + 完整路径写入注册表：**

```python
# 改前（依赖 .vbs 文件关联）
cmd = f'wscript.exe //B "{vbs_path}"'

# 改后（直接调用 pythonw.exe，零中间环节）
cmd = f'"{pythonw_path}" "{script_path}"'
```

#### 具体改动

| 文件 | 改动 |
|------|------|
| `auto_start.py` | 移除 `get_vbs_path()`，新增 `get_script_path()` 直接返回 `disk_monitor.py` 路径 |
| `auto_start.py` | 新增 `_find_pythonw()` 多级查找 pythonw.exe（当前环境 → conda 环境 → PATH） |
| `auto_start.py` | `set_auto_start()` 改为直接写入 pythonw.exe 命令 |
| `disk_monitor.py` | `main()` 启动时新增自启动同步：配置标记 `auto_start: true` 但注册表缺失时自动修复 |

#### `_find_pythonw()` 查找优先级

```
1. sys.executable 同目录下的 pythonw.exe（最可靠）
2. 向上查找 conda 环境根目录的 pythonw.exe
3. 常见 conda 环境路径（~/.conda/envs/query/ 等）
4. fallback 到 PATH 中的 pythonw.exe
```

---

### 三、开发经验总结

#### 1. Windows 文件关联是隐形炸弹

> **教训：** 不要依赖 `.vbs`、`.py`、`.bat` 等扩展名的文件关联来执行关键逻辑。用户装了 VS Code、Notepad++ 等编辑器后，文件关联随时可能被篡改。

**正确做法：** 直接调用可执行文件的完整路径，跳过文件关联：

```python
# ❌ 依赖文件关联
'wscript.exe //B "script.vbs"'

# ✅ 直接调用
'"C:\path\to\pythonw.exe" "C:\path\to\script.py"'
```

#### 2. 自启动需要自愈机制

> **教训：** 注册表 Run 键可能被杀毒软件、Windows 更新、用户误操作清除。单次写入不可靠。

**正确做法：** 每次应用启动时检查配置与注册表的一致性：

```python
if config.get("auto_start") and not is_auto_start_enabled():
    set_auto_start(True)  # 自动修复
```

#### 3. pythonw.exe 查找要考虑多环境共存

> **教训：** 用户机器上可能同时有 conda、msys64、系统 Python 等多个环境。`sys.executable` 取决于用哪个 Python 启动的脚本，不一定是目标环境。

**正确做法：** 多级 fallback 查找，优先用当前解释器同目录，再查已知环境路径。

#### 4. psutil 采样方案的取舍

> **教训：** `psutil.io_counters()` 只能追踪采样期间存活的进程。短命进程（如编译器子进程）会漏掉，但对于"哪个软件写盘最多"这个需求，覆盖常驻进程已经足够。

**适用场景：** 监控长期运行的写入大户（浏览器、IDE、下载工具、云同步等）。
**不适用场景：** 需要精确追踪所有短命进程的写入行为（应选 ETW）。

#### 5. 托盘应用的窗口管理

> **教训：** `pythonw.exe` 启动脚本、subprocess 用 `CREATE_NO_WINDOW`、`STARTUPINFO` 隐藏窗口——三者缺一不可，否则会在采集周期内闪现控制台窗口。

---

### 四、改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `process_io_tracker.py` | 新增 | psutil 进程写入量采样与差值计算 |
| `auto_start.py` | 修改 | 绕过 VBS，直接写入 pythonw.exe 命令 |
| `disk_monitor.py` | 修改 | 集成进程追踪 + 自启动同步 + 托盘菜单 |
| `csv_logger.py` | 修改 | `log_write()` 末尾增加 3 列进程来源 |
| `requirements.txt` | 修改 | 新增 `psutil>=5.9.0` |
| `ssd_monitor.spec` | 修改 | hiddenimports 加入 `psutil` |
| `启动.vbs` | 保留 | 仍可用于手动启动，但自启动不再依赖它 |
