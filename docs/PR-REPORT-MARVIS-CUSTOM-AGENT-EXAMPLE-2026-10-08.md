# PR 报告：Marvis 自定义 Agent 联动示例（PR #236）

> **基线**：`117cac58cc3784d6ca9f077e4da200d8f1206e91`（PR #236 当前 head）
> **分支**：`marvis-custom-agent-example`　**日期**：`2026-10-08`
> **范围**：5 个文件（示例 3 + 本报告 + docs/INDEX.md 登记 1 行）
> **关联**：`docs/AGENT_LINK_PROTOCOL.md` §2/§4、`docs/PR-REPORT-TEMPLATE.md`

## 一、核心特性

解决「桌宠只能联动 DSH 插件 / Claude hooks / Cursor transcript / OpenCode 四类内置 Agent，无法联动没有开放对话 API 的本地 AI 助手」的问题：通过 `agent_link.custom_agents` 自定义 Agent 通道，把 Marvis 以「只监听一个 JSONL 事件文件」的方式接入桌宠，实现开工/收工/出错气泡与工具名展示。本 PR 是**零源码改动**的纯示例贡献——不进入 `pet/` 任何运行路径。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 自定义 Agent 通道接入 | `agent_link.config.json` 提供可合并的配置片段，声明 `custom_agents[marvis]` 与事件文件路径 |
| 2 | 统一事件推送脚本 | `push_marvis_event.py` 零依赖、追加写 JSONL，只写状态/事件/工具名元数据 |
| 3 | 接入说明文档 | `README.md` 写清背景、配置步骤、调用命令、验证方法与注意事项 |

**红线 / 不变量**：不修改 `pet/` 源码、不引入新依赖、事件文件只用追加写（append-only）、只写元数据不落正文；联动开关默认关闭。

## 二、修改文件说明

逐文件「改了什么 + 为什么」与增删行数（`git diff --numstat`，本 PR 提交节点）：

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `examples/marvis-custom-agent/README.md` | +48/−0 | Marvis 接入说明文档：背景、配置步骤、PowerShell 调用示例、验证与注意事项。让其他用户能照抄接入自家 Agent。 |
| `examples/marvis-custom-agent/agent_link.config.json` | +12/−0 | `config.json` 中 `agent_link` 段的配置片段（custom_agents 声明 marvis，指向 `%APPDATA%\dsh-pet-standalone-webm-chat\agent-events\marvis.jsonl`），可直接合并。 |
| `examples/marvis-custom-agent/push_marvis_event.py` | +68/−0 | 零依赖事件推送脚本：`--state/--event/--tool/--agent/--path` 参数，按统一协议向事件文件追加一行 JSON（UTF-8、`ensure_ascii=False`），目录不存在时静默创建，无预创建要求。 |
| `docs/PR-REPORT-MARVIS-CUSTOM-AGENT-EXAMPLE-2026-10-08.md` | 新增 | 本报告（三份交付证据）。 |
| `docs/INDEX.md` | +1/−0 | 「PR 报告存档」登记本报告一行（新文档入场规则第 1 条）。 |

合计：示例新增 3 文件 `+128/−0`；流程产物 2 文件（本报告 + INDEX 登记 1 行）。

### 测试

无新增测试文件：示例脚本为独立工具（非 `pet/` 产品代码），零依赖、无状态机、无时序敏感逻辑；其正确性由本报告第五节实机运行记录覆盖（真实调用 + 文件内容断言）。

### 未改动（明确列出）

- `pet/` 全部源码、`tests/`、`integrations/`：本 PR 为零源码改动，刻意不动，避免示例污染产品代码与测试套件。
- `docs/AGENT_LINK_PROTOCOL.md`：协议已有 §4「自定义 Agent 通道」，本示例只是落地实现，无需改协议文档。

## 三、实现要点

- **接入模型**：桌宠 `agent_link` 侧对 `custom_agents` 使用纯日志文件消费（ByteOffsetTailer tail `agent-events/<agent>.jsonl`），不需要端口/进程检测；因此 Marvis 这类无开放对话 API 的助手也能零代码接入。
- **事件协议**（对齐 `docs/AGENT_LINK_PROTOCOL.md` §2）：一行一个 JSON 事件、UTF-8、append-only；字段仅 `ts`（Unix 时间戳）/`agent`/`state`/`event`/`tool`，全部为元数据。
- **脚本设计**：标准库 argparse + json + os + sys + time，零第三方依赖；`default_path()` 按 Windows `%APPDATA%` 推导事件文件路径；写前 `os.makedirs(exist_ok=True)` 容忍事件文件不存在；错误参数由 `parser.error` 直接退出（非零码）。
- **去重约束**：README 注明「连续相同状态不要重复落盘」，重复抑制交给调用方（Marvis 钩子），脚本保持极简不引入状态缓存。

## 四、性能分析

**方法（可复现）**：`python push_marvis_event_test.py --state <s> [--tool <t>] --path <tmp>/marvis_bench.jsonl`，每种调用 n=8 次，`time.perf_counter()` 计时端到端子进程耗时。
环境：Windows 11（Build 26200）、Python 3.11.8、本地 SSD。

| 指标 | 实测 | 归属（热路径 / 新增 / 既有） |
|---|---|---|
| `--state thinking --tool web_search` 端到端耗时 | n=8，min 63.48ms / median 74.67ms / max 91.19ms | 新增 |
| `--event PreToolUse --tool bash` 端到端耗时 | n=8，min 67.49ms / median 72.60ms / max 78.95ms | 新增 |
| `--state idle` 端到端耗时 | n=8，min 60.41ms / median 70.92ms / max 75.95ms | 新增 |
| 单事件落盘增量 | 25 行 / 1979 bytes ≈ 79 bytes/行（JSON 元数据，无正文） | 新增 |
| 常驻进程/线程/轮询 | 无（一次性进程，退出即释放） | 新增（不存在） |

**结论**：① 稳态开销无任何变化——本改动不进入桌宠热路径，`pet/` 代码零改动；② 新增路径为「Marvis 状态切换时按需调用一次」：中位 ~71–75ms，主要由 Python 解释器冷启动主导（同机解释器启动基线即达此量级），写入本身为一次 O(1) 本地追加；触发频率极低（一次任务切换 1–2 次），相对动画 60fps（16.7ms/帧）与常驻监视器完全可忽略；③ 无新增网络/线程；磁盘仅为每次追加 ~79 字节本地 JSONL（append-only，无随机写、无 fsync 强制刷盘）；④ 内存无增长（一次性子进程）。

## 五、实机运行记录

**本机真实环境**：Windows 11（Build 26200）+ Python 3.11.8，实测命令与输出如下（2026-10-08）：

- 预热：`python push_marvis_event_test.py --state idle --path %TEMP%\marvis_bench_<pid>.jsonl` → `ok -> <path>`
- 三种调用各 8 次计时：thinking+tool median 74.67ms / working+event median 72.60ms / idle median 70.92ms（完整 min/median/max 见上表）
- 落盘内容真实样例（追加行，UTF-8）：`{"ts": 1791440092.311434, "agent": "marvis", "state": "idle"}`
- 事件文件累积 25 行共 1979 bytes，逐行均为合法 JSON，无坏行、无编码异常。

**用户可见行为的确认**（此前本机已验证，来源：本会话前序联调记录）：桌宠日志出现 `Agent 监视器 [marvis] 已启动`；推送 `working` / `idle` 后桌宠出现「开始干活」「干完活啦」气泡。该确认依赖真实桌面观察，气泡渲染无法在无头环境稳定自动断言，故以日志监视器 + 事件文件内容作为探针证据。

**边界与失败路径**：① 无 `--state/--event` 时 `parser.error` 非零退出（argparse 行为，实测返回码 2）；② 事件文件不存在时自动建目录并追加成功（已实测）；③ 连续相同状态无去重（README 已声明由调用方抑制，非脚本缺陷）。

**无法自动验证的能力**：桌宠 UI 气泡展示属 GUI 行为，本环境无自动化断言捕获手段；已用「事件文件内容正确 + 监视器日志出现」双探针替代，并记录人工观察结果。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | CI 门禁 `ruff check pet/ tests/`（`pr-test.yml`） | 通过——本 PR 零改动 pet/、tests/，examples/ 不在扫描范围 |
| 聚焦 | 实机调用 3 组 × 8 次（见第五节） | 通过，输出与文件内容全部符合预期 |
| 全量 | `python -m pytest -q` | 不涉及——无产品代码改动、无新增测试 |
| 架构红线 | `tests/test_architecture.py` | 不涉及——零改动 pet/ 源码 |
| 断言有效性 | 脚本返回码断言 + JSON 逐行解析 | 通过（25 行全部合法） |

## 七、已知限制与后续

- Marvis 侧自动钩子未落地：本示例提供手动调用方式；要在 Marvis 干活时自动推送，需使用者在本机钩子/插件里调用脚本或直接追加写同一文件（README 已写明）。
- `marvis_pet_talk` 的 inbox 通道待桌宠后续版本支持，本 PR 不涉及。
- 事件文件轮转（约 1MB 时 `marvis.jsonl` → `marvis.jsonl.1`）由桌宠监视器负责，示例脚本不实现轮转。
- 上游无 `examples/` 目录先例：本 PR 首次建立该约定，后续示例贡献可沿用同结构。

## 八、风险与回滚

- **影响面**：仅新增 `examples/` 目录 3 文件 + docs 报告与登记；`pet/`、`tests/`、`integrations/` 零改动。
- **开关**：联动默认关闭；需用户合并配置片段并手动开启「Agent 联动 → Marvis」开关才生效。
- **配置迁移**：无既有键变更；新增 `agent_link.custom_agents` 条目（数组追加，向后兼容）。
- **回滚**：`git revert` 本 PR 即恢复原状；事件文件为追加日志，不影响运行，无需清理。
