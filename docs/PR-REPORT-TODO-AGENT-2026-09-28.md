# PR 报告：LLM生成待办 Agent

> **基线**：`2786c156dc61774a4195d6930806bb0471b59884`（origin/main，Merge PR #190）
> **分支**：`codex/todo-agent-entry-packaging`　**日期**：2026-09-28
> **首轮范围**：11 个文件（实现/工具 5、测试 4、文档 2）
> **第二轮累计**：当前 PR 相对 `origin/main` 共 13 个文件（实现/工具 6、测试 5、文档 2）；本轮逐文件差异见文末。
> **关联**：[`ONEDIR_PACKAGING.md`](ONEDIR_PACKAGING.md)

## 一、核心特性

待办提醒面板保留原有「新建待办」手动表单，并新增「LLM生成待办」按钮，点击后打开独立窗口。用户可粘贴任意包含事情和时间的消息，Agent 抽取未来事项并加入现有待办列表，也可从窗口切到手动表单。窗口标出当前内置 AI 对话的服务商、模型、Chat Completions 接口和共用的 API Key 聊天额度。只有实际新增待办后才清空输入；空结果、重复项和失败均保留文本。

同时补上 onedir 构建依赖预检和启动就绪检查，避免缺失 `lunar_python` 的包进入交付阶段，也避免只看见进程就误判启动成功。

**不变量**：Agent 网络请求在后台线程；模型结果经 Qt queued signal 回 GUI 线程落盘。现有待办先装载、再去重追加，不覆盖已有数据。Agent 不显示或记录 API Key，不新增配置项、迁移或独立聊天历史。

## 修改文件说明

下面的增删行数来自 `git diff --numstat`；新文件按新增行统计，没有删除文件。

### 实现与工具

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `pet/app.py` | +83 / −1 | 接收 DSH 真人消息；在 GUI 线程校验、去重、追加和刷新待办；退出时关闭 Agent。 |
| `pet/todo_agent.py` | +222 / −0 | 新增 JSON 响应解析、日期/时间校验、输入与队列上限，以及串行后台模型调用。 |
| `pet/todo_panel.py` | +200 / −7 | 保留原有「新建待办」手动表单，增加蓝色强调的生成入口（当前文案为「LLM生成待办」）和独立窗口；展示模型/额度，成功新增后清空文本。 |
| `scripts/build_onedir.ps1` | +53 / −5 | 预检 `lunar_python`；启动包后同时等主窗口和事件循环就绪日志。 |
| `scripts/benchmark_todo_agent.py` | +127 / −0 | 提供不访问网络的提交耗时、空闲线程 CPU 和队列内存复测脚本。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---:|---|
| `tests/test_desktop_pet_features.py` | +2 / −0 | 将 PR 报告排除在产品文案品牌扫描之外；报告记录分支与验证工具，不属于产品文案。 |
| `tests/test_requested_regressions.py` | +16 / −0 | 锁定打包依赖预检与应用就绪检查。 |
| `tests/test_session_end_ffmpeg_guard.py` | +6 / −0 | 用事件同步对照组的 reader 与后台元数据探测，避免主线程立即断言造成 Windows 竞态失败。 |
| `tests/test_todo_agent.py` | +228 / −0 | 覆盖原有手动入口、与新建按钮同色的文本生成入口、独立弹窗、模型提示刷新、提交状态、清空条件与结果写入。 |

### 文档

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `docs/INDEX.md` | +1 / −0 | 登记本报告。 |
| `docs/PR-REPORT-TODO-AGENT-2026-09-28.md` | +89 / −0 | 本文件；新增本次 PR 的三项交付证据。 |

## 三、实现要点

- Agent 使用 `Config.chat_settings().active_config`，解析同一 Provider 的 API Key，并通过 `OpenAICompatibleProvider.stream` 发送一条 system prompt 和一条 user prompt。请求带本地时间，要求模型只返回待办 JSON；解析器仅接受最多 5 个有效未来事项。
- 输入上限为 8,000 字符、排队上限为 32 条、响应 `max_tokens` 上限为 700、网络超时上限为 20 秒。无可用配置或队列满时拒绝请求并保留输入。
- 手动入口弹窗打开时不请求模型；点击「手动填写」关闭弹窗并展开既有编辑表单。重复、空结果、失败不会清空文本。
- AppShell 负责给候选项去重并限制待办总量，成功后通过既有 `TodoReminderService` 保存；模型原始响应和对话上下文不落盘。

## 性能分析

**方法**：`python -m scripts.benchmark_todo_agent --samples 1000 --idle-seconds 5`；PowerShell；Windows 10 `10.0.26200`、CPython 3.10.15（conda）。样本 1,000 次。脚本把 `OpenAICompatibleProvider` 替换为进程内空响应探针，未连接网络。

| 指标 | 实测 | 归属 |
|---|---:|---|
| `TodoAgent.submit` 排队接纳，中位数 / P95 / 最大值 | 0.0163 / 0.0325 / 1.2663 ms | 新增路径，计时止于入队，不含网络 |
| 单个空闲 worker，5 秒 CPU 时间 | 0.0156 s | 首次请求后稳态；worker 每次 `Queue.get` 最多等待 0.2 秒 |
| 满队列的文本与 Python 堆增量 | 32 条 × 8,000 字符；256,000 字符；276,690 bytes | 新增路径，`tracemalloc` 实测 |

**结论**：应用启动和面板打开不创建 Agent 线程；首个请求后创建 1 个 daemon worker，应用退出时停止。空闲 5 秒实测 CPU 为 15.6 ms。每条被接纳的手动输入或 DSH 真人消息触发 1 次聊天接口请求；打开/关闭弹窗本身不发请求。此请求与内置 AI 对话共用当前 API Key 和服务商额度。一次真实请求的网络延迟、token 数和计费未测，因为会消耗用户额度；代码将其响应限制为最多 700 tokens、超时不超过 20 秒。没有新增常驻轮询、缓存或历史文件；只有成功新增待办时复用既有待办存储写盘。队列最多保留 32 条、每条最多 8,000 字符，满队列测得 Python 堆增量为 276,690 bytes。

## 实机运行记录

**根因现场**：用户此前启动旧包时日志为 `ModuleNotFoundError: No module named 'lunar_python'`，导入链落在 `pet/festival_calendar.py`。构建机预检输出 `lunar-python OK`；本次 `Analysis-00.toc` 同时包含 `lunar_python`、`pet.todo_agent` 与 `pet.todo_panel`。

**本机打包与启动**：命令 `.\scripts\build_onedir.ps1 -Variant webm-chat`；默认临时目录因祖先含 `node_modules` 导致首轮桥接隔离检查失败，随后将 `TEMP/TMP/TMPDIR` 指向 W: 上无该依赖的临时目录后通过。Windows 10 `10.0.26200-SP0`、Python 3.10.15、PyInstaller 6.22.2。真实输出：`[smoke] exe window and app-ready log appeared after 3.5s`、`[smoke] --settings window appeared after 2.9s`、`[encoding-check] PASS`、ZIP `Done testing`。便携包为 362,397,241 bytes（约 345.6 MiB）。

**弹窗与边界**：本机用实际 `TodoPanelDialog` 和真实 Qt 事件循环做交互探针，输出 `manual_form_after_add=True generate_enabled=False`、`popup_after_generate=True`；未提交文本，因此没有网络请求或读取聊天凭据。离屏截图在 440×460 主面板及生成窗口默认尺寸下确认入口和文案可见；本轮复核两个入口的 `accent=True`，截图中均渲染为 `#0a84ff`。相关 offscreen 回归测试验证原有新建按钮展开手动表单、独立文本生成按钮打开弹窗、手动填写回退、空结果和 Agent 拒绝时保留输入，以及新增成功后清空。

**原生桌面 UI 的验证边界**：本次桌面自动化探针返回 `apps=[]`，且 `cua.listApps` 不可用；因此无法对新构建的 native window 做屏幕点击和截图确认。已用本机 Qt 弹窗对象可见性与布局探针确认点击路由，不能把 offscreen 结果冒充为用户桌面实机截图。真实模型接口未探测，原因是一次请求会消耗用户当前配置的聊天额度；上面的 benchmark 明确输出 `network_calls=0`。

## 测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 定向 | `python -m pytest -q tests/test_todo_agent.py tests/test_requested_regressions.py::test_onedir_build_preflights_lunar_python_and_waits_for_app_readiness`；本次色彩回归先去掉 `accent` 标记确认失败，再恢复后运行 `python -m pytest -q tests/test_todo_agent.py` | 原验证 6 passed；本次 5 passed |
| 静态 | `python -m ruff check pet tests scripts` | All checks passed |
| 基准 | `python -m scripts.benchmark_todo_agent --samples 1000 --idle-seconds 5` | 1,000 accepted；无网络；数值见性能表 |
| 全量（PR 文件范围） | CI 同款 pytest 命令（排除四个隔离时序测试）；本地额外忽略一项未跟踪测试文件 | 2907 passed, 11 skipped, 3 warnings（200.01 s）；本次仅改变孤立按钮样式属性，接口、持久化、生命周期和平台分发均未变，因此没有重跑全量 |
| 本地额外忽略 | `tests/test_dsh_control_cross_language.py` | 该文件在当前工作区未跟踪、不属于 PR；复测按 PR 提交文件范围运行，CI 使用实际提交文件集合 |
| 打包 | `scripts/build_onedir.ps1 -Variant webm-chat`；`python -m zipfile -t dist-onedir/dsh-pet-standalone-webm-chat-portable.zip` | 启动、设置窗口、DLL、编码检查与 ZIP CRC 均通过 |

## 已知限制与回滚

- 没有对真实服务商发出请求；模型名和额度来源由当前本地配置动态显示，网络成功路径未消耗真实额度验证。
- 回滚本 PR 不涉及配置迁移或新建持久键；已由用户成功创建的待办继续由既有存储管理。

## 第二轮改进（2026-09-28，历史记录）：自动补排期、会议提醒与弹窗规范

本轮保留上文首轮实现与证据，补上“没有写具体时间”的本地待办排期，并按 Shared UX Contract 整理文本生成子窗口。此轮当时曾加入会议专属提前量；该行为已由下方第三轮需求修正撤销，当前所有待办统一使用全局提醒偏好。

### 本轮修改文件说明

下表为相对上一 PR 提交的 `git diff --numstat HEAD`，新文件/删除文件均单独列出；本轮没有新增或删除文件。

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `pet/app.py` | +18 / −2 | 在 GUI 线程获取当前启用待办时间快照并传给 Agent；创建待办时保留模型给出的单条提醒提前量。 |
| `pet/todo_agent.py` | +178 / −33 | 更新抽取提示词和 JSON 字段；在用户指定日期或未来 7 天内按“60 分钟内冲突数、当天待办数、日期和时刻”依次排序 09:00–17:00 的候选时段，每条新结果占用其已选时段。若无完全空档，选冲突最少的候选，并保留调度器选定的日期。 |
| `pet/todo_panel.py` | +62 / −35 | 让子窗口只阻塞所属待办面板；移除内部二级卡片，补输入标签关联和关闭后焦点恢复，显示会议提前量。 |
| `pet/todo_reminder.py` | +35 / −5 | 持久化可选的单条提前量，在提醒触发时覆盖全局提前量；系统通知与气泡一致显示提前分钟数。 |
| `scripts/benchmark_todo_agent.py` | +47 / −4 | 基准加入 100 条当前待办快照，并实测无时间事项的本地排期与提示上下文长度。 |
| `tests/test_todo_agent.py` | +206 / −7 | 覆盖会议 30 分钟提醒、待办空档查询、多条新事项错峰、自动选择日期、弹窗窗口模态/辅助标签/焦点和 DSH 快照传递。 |
| `tests/test_todo_reminder.py` | +53 / −0 | 覆盖单条提前量清洗、文件往返保存、覆盖全局偏好以及系统通知文案。 |
| `docs/INDEX.md` | +1 / −1 | 更新报告摘要和适用范围，登记自动排期与会议提醒内容。 |
| `docs/PR-REPORT-TODO-AGENT-2026-09-28.md` | +63 / −1 | 追加本轮差异、实测和验证记录，保留首轮证据。 |

### 本轮实现与行为边界

- 待办面板将 `TodoReminderService.items()` 的当前副本在 GUI 线程交给 Agent；队列只携带 `kind/date/time`，不带已有待办标题。提示词收到时间安排上下文；解析器对缺少时刻的事项使用相同快照，依次最小化 60 分钟内冲突数、当天待办数，再按日期和时刻择早；如果没有完全空档，则退到冲突最少的候选。未指定日期时搜索今天到第 7 天，用户指定日期时只在该日找空档；搜索范围为每小时 09:00 至 17:00。它只查询本地待办，不代表外部日历空闲。
- 同一模型响应含多条缺少时刻的事项时，解析器会把已分配时段加入临时快照，避免同批事项选到同一空档。历史版本曾根据会议、面试或预约标记保存 `reminder_lead_minutes=30`；当前实现已删除这项单条覆盖，详见第三轮修正。
- 文本生成窗口改为所属面板的 window-modal 子窗口；保留独立窗口层级，不再嵌入额外二级卡片。窗口采用现有浅/深色令牌、20 px 标题、12 px 提示、7 px 输入圆角与蓝色焦点态；最小 440×440、默认 560×480。输入标签通过 buddy 关系关联文本框，Ctrl+Enter 可提交，关闭时焦点返回“文本生成”入口，手动填写仍切回原表单。

### 本轮性能分析

**方法**：`python -m scripts.benchmark_todo_agent --samples 1000 --idle-seconds 5`；Windows 10 `10.0.26200-SP0`、CPython 3.10.15（conda）；进程内 Provider 探针不访问网络。每次提交和解析使用 100 条启用的待办时间快照，队列压力按 32 条上限、每条 8,000 字符测量。

| 指标 | 实测 | 归属 |
|---|---:|---|
| `TodoAgent.submit` 1,000 次，中位数 / P95 / 最大值 | 0.3466 / 0.4454 / 2.5219 ms | 新增路径；包含 100 条时间快照归一化与入队，不含网络 |
| 缺少时间解析 1,000 次，中位数 / P95 / 最大值 | 0.9204 / 1.1813 / 3.2771 ms | 新增本地路径；含 100 条排期扫描和空档选择 |
| 100 条待办提示上下文长度 | 1,899 字符 | 新增模型输入；不产生额外请求，实际 token 数随模型分词器变化，未调用服务测量 |
| 单个空闲 worker，5 秒 CPU 增量 | 0.0000 s（计时输出精度 0.0001 s） | 首次请求后稳态；探针无响应流量 |
| 32 条满队列的堆增量 | 839,615 bytes；含 256,000 个输入字符和每条 100 个排期时间 | 新增路径；`tracemalloc` 实测 |

**结论**：应用仍只在首次请求后创建 1 个后台 worker；读当前待办是 GUI 线程内的内存副本，空档搜索在已有 worker 返回结果后于本地执行。网络请求频率仍为每条被接纳的 Agent 输入最多 1 次，没有新增外部服务请求；模型输入新增最多 100 条已启用待办时间，100 条样本为 1,899 字符，真实 token 用量未测。没有新增定时器或常驻线程；待办数据沿用原有 JSON 文件，会议只增加每项可选整数，触发后仍由现有调度器写回。队列达到上限时，100 条快照加文本的 Python 堆增量实测 839,615 bytes。

### 本轮实机运行记录

- **实际用户可见窗口与尺寸探针**：Windows 本机 CPython 3.10.15，使用真实 `TodoPanelDialog`/`QDialog`、Qt 事件循环和其实际 QSS，未替换 UI 控件。`QT_QPA_PLATFORM=offscreen` 下抓取 440×440、720×560、1100×680 三种窗口尺寸，并分别设置浅色和深色调色板；六张截图均可见标题、模型额度说明、输入区、状态和动作按钮，空输入时主操作按钮保持禁用且可见。紧凑布局输入框实测 396×213，按钮均在窗口内。离屏环境没有 CJK 字体回退，因此探针仅给截图进程加载 `C:\Windows\Fonts\simsun.ttc`；产品未改字体。截图保存在 `%TEMP%\todo-agent-ui-qa-20260928\`。
- **变更前复现**：首轮 Agent 提示词把未标钟点事项定为 09:00，解析器也没有已存待办快照参数；实现前的行为回归因缺少 `find_available_todo_slot` 入口失败。更晚发现的日期折回缺陷用 `$env:QT_QPA_PLATFORM='offscreen'; python -m pytest -q tests/test_todo_agent.py::test_missing_date_and_time_keep_the_scheduled_open_day` 重现，红输出为 `1 failed`，实际结果日期 `2026-09-04`、期望 `2026-09-05`；修复后同一用例通过。提醒回归在实现前也观察到清洗流程会丢弃单条提前量字段。
- **行为确认**：定向 Qt 用例通过真实窗口控件验证：点击“文本生成”打开子窗口并聚焦文本框；窗口只对待办面板 window-modal；输入标签关联文本框；关闭后焦点回到入口；“手动填写”仍打开原表单。服务回归覆盖会议条目在 09:30 触发 10:00 会议前的系统通知 `项目会议（10:00，提前30分钟提醒）`，模型网络未调用。
- **自动化边界**：桌面自动化探针先前返回 `apps=[]` 且 `cua.listApps` 不可用，本轮截图因此明确是本机 Qt offscreen 渲染，不冒充原生桌面截屏。没有请求真实聊天服务，因为会消耗当前配置的额度；脚本输出 `network_calls=0`。桌面原生窗口与服务商真实响应仍需在应用运行环境中手动确认。

### 本轮测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 定向 | `python -m pytest -q tests/test_todo_agent.py tests/test_todo_reminder.py`（`QT_QPA_PLATFORM=offscreen`） | 54 passed, 2.26 s |
| 静态 | `python -m ruff check pet tests scripts` | All checks passed |
| 基准 | `python -m scripts.benchmark_todo_agent --samples 1000 --idle-seconds 5` | 1,000 次入队与 1,000 次本地空档解析；0 网络请求，结果见性能表 |
| 全量主套件 | PowerShell：`$env:QT_QPA_PLATFORM='offscreen'; python -m pytest -q --ignore=tests/test_webm_reader_lifecycle.py --ignore=tests/test_webm_clip_lifecycle.py --ignore=tests/test_webm_first_frame_lock.py --ignore=tests/test_low_priority_warm_interaction_yield.py --ignore=tests/test_dsh_control_cross_language.py` | 2918 passed, 11 skipped, 3 warnings（191.78 s）；额外忽略项是工作区未跟踪文件，不属于 PR |
| 差异检查 | `git diff --check` | 通过；仅 Git 提示工作区文件的 LF/CRLF 转换警告 |

上文首轮测试表保留当时结果；本表是本轮代码修改后的最新全量主套件结果。

**本轮回滚**：移除本轮的单条提醒覆盖后，旧待办和全局提醒偏好语义保持不变；移除本轮排期快照传递后，无时间事项恢复由原有模型输出时间的行为。没有配置迁移，也没有需要清理的专属缓存或后台服务。

## 第三轮需求修正（2026-09-28）：统一沿用全局提醒提前量

产品确认待办功能已有全局“提前提醒”设置，因此文本 Agent 不再识别会议、面试或预约，也不再给某类事项单独设置 30 分钟。每条待办都由现有 `todo_reminder_lead_minutes` 设置决定提醒提前量。解析器忽略模型可能返回的旧 `is_meeting` 标记；待办标准化时丢弃历史试验版的 `reminder_lead_minutes` 字段，避免旧字段覆盖当前偏好。提醒设置本身和手动新建流程未改。

### 本轮修改文件说明

下表增删行数为本轮工作区相对当前分支 `HEAD` 的 `git diff --numstat`；没有新增或删除文件。

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `pet/app.py` | +0 / −1 | 创建 Agent 结果时不再传递模型给出的单条提前量。 |
| `pet/todo_agent.py` | +3 / −7 | 删除会议识别和 30 分钟规则，统一要求模型沿用全局提醒设置。 |
| `pet/todo_panel.py` | +10 / −13 | 界面说明改为沿用桌宠提醒设置，移除单项提前量徽标，并将入口与窗口统一命名为「LLM生成待办」。 |
| `pet/todo_reminder.py` | +3 / −29 | 调度只读取全局提前量，标准化时清除过往单项覆盖字段。 |
| `tests/test_todo_agent.py` | +6 / −8 | 更新提示、解析和 App 接收行为的回归断言，确保会议标记不产生专属提前量，并同步弹窗无障碍名称。 |
| `tests/test_todo_reminder.py` | +19 / −17 | 覆盖旧字段被清除且不能覆盖全局提醒设置。 |
| `docs/INDEX.md` | +1 / −1 | 将索引摘要改为准确描述全局提醒行为。 |
| `docs/PR-REPORT-TODO-AGENT-2026-09-28.md` | +40 / −6 | 记录本轮需求修正、LLM生成待办入口文案、性能分析和重新打包结果。 |

### 本轮实现与行为边界

- 提示词不要求识别会议类别；解析结果只包含待办标题、类型、日期和时间。即使旧模型响应带有 `is_meeting`，解析器也忽略它。
- 手动创建与 Agent 创建的待办均由 `TodoReminderService` 读取同一全局提前量。旧待办的 `reminder_lead_minutes` 不再生效，并在标准化/保存时移除；无配置迁移、新设置或新增聊天请求。入口、窗口标题和无障碍名称统一使用「LLM生成待办」。

### 本轮性能分析

**方法与实测**：PowerShell 内联 Python 探针调用 `advance_todo_state`；Windows 10 `10.0.26200-SP0`、CPython 3.10.15，1,000 轮 × 每轮 100 条（共扫描 100,000 条）。总用时 1.387801 秒，平均每轮 1.388 ms、每条 13.878 μs。该值覆盖完整提醒扫描，未与旧版本做性能对比。改动路径的稳态成本没有新增分支或数据结构；每条待办仍每 30 秒 tick 扫描一次，启动时仍立即扫描一次。网络请求频率不变（Agent 每条输入最多 1 次），没有新增系统调用、线程或持久磁盘写入；仍只在提醒状态变化时保存待办 JSON。内存没有新增常驻字段或缓存，历史单项字段在清洗时被移除。

### 本轮实机运行记录

**重新打包**：提醒规则更新时先将原目录和 ZIP 备份至 `dist-onedir/backup-webm-chat-20260928-131146-106`（1,220 个文件；旧 ZIP 362,397,241 bytes，备份哈希一致）。改成“LLM生成待办”后，又将前一版目录和 ZIP 备份至 `dist-onedir/backup-webm-chat-20260928-133036-743`（1,220 个文件；ZIP 355,843,433 bytes，SHA-256 为 `E8964F2C61CFCE67708EF1D44794FEF847A066E4665A6510B26FD7C1D4FEE23B`）。命令 `powershell -ExecutionPolicy Bypass -File scripts\build_onedir.ps1 -Variant webm-chat`；为通过桥接隔离探针，仅在构建进程中把 `TEMP/TMP/TMPDIR` 指向 `W:\dsh-pet-package-temp-20260928-1331`。Windows 10 `10.0.26200-SP0`、CPython 3.10.15、PyInstaller 6.22.2。最新真实输出：`lunar-python OK`、桥接零依赖冒烟通过、Qt Runtime validation OK、瘦身移除 118 个文件 / 44.92 MB、中文编码检查 PASS、DLL 链检查 ALL OK；应用窗口 3.7 秒就绪，`--settings` 窗口 2.4 秒就绪。新 onedir 目录 1,220 个文件、866,173,795 bytes；portable ZIP 355,844,657 bytes（339.4 MiB），`python -m zipfile -t dist-onedir\dsh-pet-standalone-webm-chat-portable.zip` 输出 `Done testing`，SHA-256 为 `1B3BA18765D03FD97CBB39A19FCB05E52F8DF3461EE7470122FEAA9059435E47`。

**本次需求修正的产品测试套件未运行**；既有测试断言已随文案更新。`python -m ruff check pet tests scripts` 输出 `All checks passed!`；构建脚本的依赖、桥接、Qt DLL、应用启动、设置窗口、编码及 ZIP 检查均已实际通过，`git diff --check` 通过。新包保留原有提醒设置和人工新建流程，Agent 界面说明统一显示沿用桌宠提醒设置。

## 第四轮（2026-09-29）：待办提醒确认与推迟

为可见桌宠的待办提醒增加可选确认模式。开启后，提醒一直显示，用户可点「确定」关闭提醒，或点「推迟」将当前事项安排到本地待办中相对空闲的下一时段。一次性待办直接移动日期和时间；每日待办只为当前触发保存一次性推迟，不改变每天的固定时间。桌宠隐藏时仍沿用系统通知。默认关闭，旧配置和旧待办均可直接读取。

### 本轮修改文件说明

增删行数来自本轮工作区相对 `bd87d3a` 的 `git diff --numstat`；本轮没有新增或删除文件。

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `pet/config.py` | +4 / −0 | 加入默认关闭的 `todo_reminder_require_ack` 配置，并登记到 reload 白名单和布尔值清洗。 |
| `pet/modern_settings_dialog.py` | +12 / −1 | 在唯一归属的「待办提醒」设置组中展示并保存确认选项。 |
| `pet/settings_pet_controls.py` | +4 / −0 | 创建设置页开关并从配置恢复当前状态。 |
| `pet/todo_agent.py` | +13 / −0 | 把每日待办的一次性推迟时段加入本地排期快照，避免其他事项误占该时段。 |
| `pet/todo_panel.py` | +12 / −1 | 列表行显示每日待办的推迟日期；用户编辑条目时清除旧推迟状态。 |
| `pet/todo_reminder.py` | +197 / −30 | 加入推迟字段清洗、单次/每日触发、下一空档选择、原条目标记、存储及面板刷新；相同事项的提前/准点提醒复用弹窗 ID，避免重复排队。 |
| `pet/window_alerts.py` | +1 / −0 | 允许待办确认提醒在设置窗口抑制期间继续入队。 |
| `tests/test_alert_queue.py` | +14 / −0 | 验证待办的确认/推迟提醒在设置抑制期间保留。 |
| `tests/test_architecture.py` | +2 / −1 | 按设置页新增控件的实际行数校准行数预算，并记录日期和原因。 |
| `tests/test_config_schema.py` | +1 / −0 | 将新配置键登记进 reload 白名单显式快照。 |
| `tests/test_todo_agent.py` | +18 / −1 | 验证每日待办推迟时段进入空档选择的日程快照。 |
| `tests/test_todo_reminder.py` | +162 / −0 | 验证字段清洗、每日仅推迟一次、一次性待办改期、提醒按钮、旧弹窗防覆盖、设置往返与列表显示。 |
| `docs/INDEX.md` | +1 / −1 | 更新本报告索引，包含确认和推迟行为。 |
| `docs/PR-REPORT-TODO-AGENT-2026-09-28.md` | +62 / −0 | 追加本轮文件、性能、实机与验证记录，保留前三轮历史。 |

### 本轮实现要点

- 「提醒需点击确定」默认关闭。开启后，桌宠可见时走现有交互气泡队列，提供「确定」和「推迟」；确认仅关闭当前提醒，不标记待办完成。桌宠隐藏时保持既有系统通知分支。
- 推迟按钮调用现有本地空档选择函数，先排除当前待办，再从当前时刻与原定时刻中较晚的一方开始找空位。选择依据只看已启用的本地待办；日程扫描 09:00–17:00、搜索未来 7 天，返回相对空闲的整点时段，不查询外部日历。
- 一次性待办直接更新自身日期/时间并重新武装触发戳。每日待办保留固定时间，单独存一个日期/时间推迟覆盖；触发并过宽限后清除覆盖。原日期的 lead/due 都盖戳，因此提前提醒推迟后不会又在原准点重复提醒。
- 提醒 ID 按待办、触发日期和时间稳定生成，lead 与 due 会更新同一条提醒；条目编辑后旧提醒的推迟回调不会覆盖新时间。存盘失败或找不到候选时不关闭提醒；成功存盘后刷新已打开的待办面板。
- 待办 JSON 格式仍为版本 1；新增字段是可选项，旧文件清洗后自动补空值。没有新配置迁移、网络请求、后台线程或定时器。

### 本轮性能分析

**环境与方法**：Windows 10 `10.0.26200-SP0`、CPython 3.10.15（conda）。用内联 Python 调用真实 `find_available_todo_slot` 和 `TodoReminderService.snooze_fire`，不替换排期或文件写入逻辑；临时 TodoStore 位于系统临时目录，网络请求为 0。

| 指标 | 实测 | 触发路径 |
|---|---:|---|
| 99 条已启用日程的空档选择，1,000 次，中位数 / P95 / 最大值 | 0.9043 / 1.0346 / 1.5659 ms | 每次用户点击「推迟」调用一次 |
| 含 100 条待办与原子 JSON 保存的完整推迟，500 次，中位数 / P95 / 最大值 | 7.2126 / 8.3051 / 34.7836 ms | 成功点击一次写盘一次；临时磁盘测试 |
| 每条待办 Python 字典大小，变更前后 | 360 → 640 bytes，增加 280 bytes；100 条上限约增加 28,000 bytes | 四个可选推迟状态键，无缓存 |

**结论**：既有 30 秒提醒扫描和 GUI 线程模型不变；只有每日条目存在推迟覆盖时，多处理一组 lead/due 触发档。推迟路径只在用户点击时运行，单次最多扫描 100 项、写入一次既有待办 JSON 并刷新打开的列表。没有新增网络调用、系统通知种类、线程、计时器或常驻缓存。测得的 34.7836 ms 最大值来自 500 次临时文件写入样本，不代表任意磁盘设备的硬上限。

### 本轮实机运行记录

- **实现前回归**：新增的三个聚焦回归先运行时为 `3 failed`：提醒只有「确定」按钮、待办清洗丢弃推迟字段、触发引擎没有一次性每日推迟档。实现后这些用例转绿。
- **本机 Qt 呈现**：Windows 本机 Python/Qt 事件循环创建真实 `PetSpeechBubble`（无假按钮控件），调用正式 `show_text` 渲染「确定 / 推迟」。离屏实测气泡 `268×98`，两个按钮都可见、各宽 48 px；截图在 `%TEMP%\todo-snooze-alert-20260929.png`。测试通过实际按钮回调验证一次性待办改期、每日待办保持原时间、弹窗关闭和旧弹窗不覆盖已编辑条目。
- **最终便携包**：命令 `powershell -ExecutionPolicy Bypass -File scripts\build_onedir.ps1 -Variant webm-chat`；构建期间将 `TEMP/TMP/TMPDIR` 指向 `W:\dsh-pet-package-temp-20260929-todo-confirm`。Windows 10 `10.0.26200-SP0`、CPython 3.10.15、PyInstaller 6.22.2。输出含 `lunar-python OK`、桥接零依赖导入通过、Qt runtime 验证通过、编码检查 PASS、Shiboken/Qt DLL 链 ALL OK；真实打包程序主窗口 3.5 秒就绪，`--settings` 窗口 2.6 秒就绪。onedir 共 1,219 个文件 / 866,173,452 bytes；便携 ZIP 为 355,844,420 bytes（339.4 MiB），SHA-256 `E63CED5765C1E4296FD11835646C643328AC3A9C2EB9CE6A1622937DB9FE6468`。
- **边界**：UI 截图通过本机 Qt offscreen 渲染，不声称是桌面截屏；打包冒烟实际启动了本机新构建的主程序和设置窗。推迟逻辑不访问模型或外部日历；500 次性能探针输出 `network_calls=0 disk_saves=500`。该路径的按钮动作、持久化和日期逻辑由本机 Qt 回归验证，真实屏幕上的人工点击未单独执行。

### 本轮测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 聚焦回归 | `python -m pytest -q tests/test_todo_reminder.py tests/test_todo_agent.py tests/test_alert_queue.py tests/test_config_schema.py tests/test_architecture.py::test_modern_settings_dialog_py_line_budget`（`QT_QPA_PLATFORM=offscreen`） | 100 passed, 7.74 s |
| 全量 | `$env:QT_QPA_PLATFORM='offscreen'; python -m pytest -q` | 2,984 passed, 11 skipped, 3 warnings（244.69 s） |
| 静态 | `python -m ruff check pet tests scripts` | All checks passed |
| 补丁空白 | `git diff --check` | 通过 |
| 打包与 CRC | `scripts/build_onedir.ps1 -Variant webm-chat`；`python -m zipfile -t dist-onedir\dsh-pet-standalone-webm-chat-portable.zip` | 启动冒烟通过；`Done testing` |
