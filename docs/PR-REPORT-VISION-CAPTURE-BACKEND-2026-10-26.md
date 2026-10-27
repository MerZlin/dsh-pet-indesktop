# PR 报告：可配置的外部截图后端（vision_capture_command）

> **基线**：`main`（`codeload.github.com/MerZlin/dsh-pet-indesktop/zip/refs/heads/main`，2026-10-26 拉取）
> **分支**：`feat/vision-capture-backend`　**日期**：`2026-10-26`
> **范围**：4 个实现/测试文件改动 + 1 个新测试文件（实现 3、测试 2）
> **关联**：无上游 issue；起因是「让桌宠的『看看屏幕』复用外部抓屏实现」

## 一、核心特性

「看看屏幕」和「主动识屏」原来各自直接 `PIL.ImageGrab` 抓屏。本改动加了一个**可选**的
外部截图后端：配置 `vision_capture_command`（argv 模板）后，两个入口先跑这条外部命令、
读它写出的 PNG，失败再逐调用点回落到内置 `ImageGrab`。默认配置为空，所以**不配置就与
改动前完全一致**。

用途：一类环境里内置抓屏拿不到想要的结果（HDR 屏、特定多屏/DPI 组合、需要复用别的
工具已经算好的取景），用户可以把抓屏换成自己的实现，而不用改代码。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 外部抓屏后端 | `vision_capture_command`：argv 数组，占位符 `{mode}` / `{rect}` / `{out}` |
| 2 | 逐调用点回落 | 命令不存在/超时/非零退出/没写出 PNG → 该次抓屏退回内置 `ImageGrab` |
| 3 | 启用可见 | 启动日志一行 `外部截图后端已启用: …`（或 `未配置，使用内置 ImageGrab`） |

**红线 / 不变量**：

- 默认（键为空）行为**逐字不变**：两个入口仍走原来的 `ImageGrab` 分支，没有任何额外
  子进程、额外磁盘文件或额外判断之外的代价。
- 配置值**只接受字符串数组**，不接受整条命令行字符串——这条路径不经过 shell，
  配置不构成命令注入面。
- 外部命令失败绝不向上抛：截图能力不依赖外部命令是否可用。

## 二、修改文件说明

行数为与上游 `main` 的逐行差异（无 git 环境，用 `difflib` 对原始 zip 计算，等价 `--numstat`）。

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/vision.py` | +120 / −5 | 新增 `set_capture_command()` / `capture_command()` / `_external_capture_png()` / `_external_capture_image()`；`capture_window_rect()` 与 `capture_screen_bytes()` 改为「先外部、失败回落」；模块 docstring 补充临时 PNG 中转的事实 |
| `pet/config.py` | +9 / −0 | 默认值新增 `vision_capture_command: []`、`vision_capture_timeout_seconds: 20.0`；reload 白名单加入这两个键（否则手动改 `config.json` 不生效） |
| `pet/app.py` | +8 / −0 | `main()` 在 `_setup_logging()` 之后、创建 `AppShell` 之前调用 `set_capture_command()`，让启用日志进得了 `pet.log` |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_vision_capture_command.py` | +108（新） | 空/字符串/非字符串拒绝；argv 保留；region 与 full 的 `{mode}` / `{rect}` 传参；命令失败回落内置并确实执行过；timeout 参数校验 |
| `tests/test_config_schema.py` | +3 / −0 | 两个新键进 reload 白名单快照（不更新会红） |

### 未改动

- `pet/proactive/__init__.py`、`pet/overlay_shell.py`、`pet/window.py` 的调用点：入参不变，
  由 `vision` 模块级配置决定走哪条路，调用方零改动。
- 设置页：这是开发者向高级键，与 `pnpm_bin` 同级别，不进 UI。

## 三、实现要点

- **模块级配置而非穿透参数**：`_CAPTURE_COMMAND` 是 `tuple[str, ...]`，由
  `set_capture_command()` 在启动时写入一次。调用点有三个（全屏两处、区域一处），其中
  `_worker_capture()` 只拿到 `eff` 而不是全局配置，穿参会让三处签名都变形；模块级单点
  与既有的 `set_configured_pnpm_bin()` 同款。
- **占位符契约**：`{mode}` ∈ `full`/`region`；`{rect}` 是 `x,y,w,h`，`full` 时传
  `0,0,0,0`（这样模板不用为两种模式写两份）；`{out}` 是本次要写入的 PNG 路径。
- **临时文件**：`mkstemp` 建空文件交给外部命令写，读回字节后 `unlink`；即使命令失败也
  在 `finally` 里删。
- **子进程**：`subprocess.run(argv, …)`，Windows 加 `CREATE_NO_WINDOW`（与仓库里
  `ocr.py` / `agent_link` 的隐藏窗口约定一致），默认 20s 超时。
- **只接受 argv**：字符串一律忽略。想用 shell 形态的用户应自己写包装脚本并以
  `["cmd","/c","my.cmd",…]` 显式声明——由用户显式选择，而不是配置里隐含。

## 四、性能分析

**方法**：`python bench_capture.py`（5 次取平均，先各跑一次预热导入）
**环境**：Windows 10 x64、Python 3.11.9、Pillow 13.0.0、1920×1080 单屏

| 指标 | 实测 | 归属 |
|---|---|---|
| 内置 `ImageGrab` 全屏 | **87 ms/次** | 既有热路径（未配置时不变） |
| 外部命令全屏（`capture-screen.ps1`） | **658 ms/次** | 新增路径 |
| 外部命令区域 400×200 | 约 0.5 s/次 | 新增路径 |

**结论**：

1. **稳态开销**：`vision_capture_command` 为空时，每次截图只多一次
   `if not _CAPTURE_COMMAND: return None`，低于测量噪声；内置路径 87 ms 与改动前一致。
2. **新增路径成本**：每次抓屏 +约 0.57 s，来源是「新建一个 PowerShell 进程（约
   0.3–1 s 冷启动）+ 写读删一个 PNG」。触发频率等于用户点击「看看屏幕」或主动识屏的
   一次采样，不是轮询；相对随后的模型请求（秒级）占比可接受。
3. **新增系统调用 / IO / 线程**：每次抓屏 1 次进程创建、1 次写文件、1 次读文件、
   1 次删除；**没有**新增线程，也没有新增网络。
4. **内存 / 缓存**：PNG 字节读入后立刻解码成 `PIL.Image`，临时文件删除；不新增常驻
   缓存。模块级只多一个 argv 元组。

## 五、实机运行记录

**本机真实环境**（源码运行，`python -m pet`，非 CI）：

```
2026-10-26 23:39:47,592 INFO 外部截图后端已启用: powershell -NoProfile（超时 20s）
2026-10-26 23:39:47,592 INFO dsh-pet-standalone 启动 (slot: 0, instance: )
2026-10-26 23:39:47,627 INFO Agent 监视器 [dsh] 已启动
2026-10-26 23:39:47,745 INFO 素材加载完成：shenshen 106 段动画
```

同一份配置走真实抓屏（直接调用两个公开入口）：

```
$ python -c "...set_capture_command(cfg.get(...)); vision.capture_screen_bytes()"
command loaded: ('powershell', '-NoProfile') ...
full screen jpeg: 46105 magic: ffd8
region size: (400, 200)
```

**边界与失败路径**：

- 配置为空 → 启动日志 `外部截图后端未配置，使用内置 ImageGrab`，截图仍成功（46105 字节
  JPEG，magic `ffd8`）。
- 外部命令非零退出 → 回落到内置路径仍出图（`tests/test_vision_capture_command.py::
  test_failed_command_falls_back_to_builtin` 用真实子进程 `SystemExit(3)` + monkeypatch
  的 `ImageGrab` 验证，并断言命令确实被执行过）。
- 配置成整条命令行字符串 → 被忽略，走内置路径（同文件 `..._string_is_refused`）。

**无法自动验证的能力**：GUI 里点「看看屏幕」的**完整用户路径**（菜单点击 → worker →
模型请求）没有自动化——需要一个已配置的聊天 provider 与真实点击，而本机 provider 未
配置，点击后会在请求阶段失败、无法区分是抓屏还是模型环节的问题。为此把验证切在
`capture_screen_bytes()` 这个公开 seam 上（GUI 的两处调用点入参未变），并保留启动日志
作为「配置已被读取」的现场证据。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests` | 通过（All checks passed） |
| 聚焦 | `pytest -q tests/test_vision_capture_command.py tests/test_vision.py tests/test_config_schema.py` | 92 passed |
| 受影响时序族满载 3 遍 | 不适用 | 本改动不碰线程/计时器/Qt 事件循环；新增的只有一次同步子进程调用 |
| 全量 | `python -m pytest -q`（`QT_QPA_PLATFORM=offscreen`） | **9065 passed / 5 failed / 18 skipped，3124 s（52 分钟）** |
| 架构红线 | `tests/test_architecture.py` | 含在全量内，通过 |

**那 5 条失败与本改动无关**（已用对照实验证明）：

```
本改动树 ：5 failed, 9065 passed, 18 skipped
上游原始树：5 failed,  64 passed（同样这 5 条，同样的堆栈）
```

失败原因全是同一类：这些用例用 `subprocess(..., text=True)` 读子进程输出，父进程按
本机 **GBK** 本地编码解码，而子进程写的是 UTF-8 中文 ⇒ 读线程抛 `UnicodeDecodeError`
⇒ `completed.stdout` 为 `None` ⇒ 断言里 `'x' in None` / `None.splitlines()`。设置
`PYTHONUTF8=1` 后同一批用例在**上游原始树**上 **69 passed**，无需改任何代码。

所以这 5 条是本机区域设置（非 UTF-8 代码页）的已知环境问题，不是产品缺陷、更不是本
PR 引入的回归；在 UTF-8 环境（作者的 CI / 常规开发者机器）上不会有。

## 七、已知限制与后续

- 模板不支持条件参数：`{rect}` 在 `full` 模式下固定传 `0,0,0,0`，命令需自行忽略。
- 配置只在启动时读取；改完 `config.json` 要重启桌宠（与 `pnpm_bin` 一致）。
- 不解析 shell 语法（设计取舍，见上）。
- 外部命令必须把 PNG 写到 `{out}`；写别的路径视为失败并回落。

## 八、风险与回滚

- **影响面**：仅 `vision` 抓屏路径；默认配置下行为不变，风险集中在「用户配错」，
  而配错的最坏结果是回落内置实现。
- **配置迁移**：新增 2 个键，旧配置读入时拿默认值，无需迁移；`reload` 白名单已同步，
  否则手改 `config.json` 不生效（这正是本次实现中发现并修掉的一个坑）。
- **回滚**：`git revert` 即可，无落盘状态、无迁移残留；删掉这两个键也等价于关闭。
