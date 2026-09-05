# Phase 3：独立进程 / 插件化容器调研与设计

> 状态：调研/设计稿（不进入本轮代码实现）
> 范围：评估把 AI 聊天、Agent 联动、主动识屏等“可关功能”从主桌宠进程拆到
> 独立进程或插件容器的成本与收益。

---

## 1. 要解决的问题

经过 Phase 1 / Phase 2，当前 main 已经具备：

- 可选服务按配置懒创建/懒启动（碰撞 IPC、待办、Agent 联动、主动识屏等）；
- 纯桌宠构建不打包 `pet.chat` / keyring；
- 动画预热可关闭，空闲内存可明显下降。

但“关闭即不加载”仍有两个天花板：

1. **Python 模块一旦 import，进程内很难真正卸载**
   已经进入主进程的模块代码、Qt 类型和单例对象会一直占内存。
2. **主进程仍承担所有功能的稳定性风险**
   某个 Agent 监视器、主动识屏截图链路或聊天 UI 的崩溃/死锁，都会拖垮桌宠本体。

Phase 3 想回答：是否值得把“重/可选/高风险”功能拆成独立进程或插件容器，
让桌宠主进程保持小而稳。

---

## 2. 现状盘点

### 2.1 内存构成（Phase 2 后粗略）

| 项目 | 说明 |
|---|---|
| PySide6 / Qt 基础 | 主进程 ~60MB 起点，很难再降 |
| WebM 播放/解码/帧缓存 | 主要可变内存，动画预热关闭后可明显下降 |
| 可选服务对象 | Phase 1 后关闭即不创建，收益已收 |
| 聊天 / keyring | 纯桌宠变体已从构建层排除；全功能版按需 import |

结论：**对纯桌宠形态，Phase 3 的进程拆分收益有限**；
对“全功能聊天版”，如果用户长期不打开聊天但安装的是全功能包，
主进程仍然保留 chat 相关代码路径和部分可 import 依赖，这才是进程拆分可能
有价值的地方。

### 2.2 已有“跨进程”经验

- **碰撞 IPC**：`pet/collision_ipc.py` 使用 `QLocalServer` / `QLocalSocket` +
  文件锁做多实例协调，已有可复用的本地 IPC 模式。
- **ffmpeg 解码**：`WebMClip` 本来就拉起独立 ffmpeg 子进程，崩溃隔离已有实践。
- **DSH 桥接**：`integrations/dsh-pet-bridge` 是桌宠与外部 CLI/插件生态的
  跨进程桥，说明项目已有“主程序 ↔ 外部能力”的协议经验。
- **PyInstaller 变体**：`webm` / `webm-chat` / `gif` 已实现“构建期隔离”。

### 2.3 尚未具备

- 没有通用的“功能服务进程”生命周期管理；
- 没有把 Qt 窗口（聊天窗、设置窗）放到另一个进程的成熟路径；
- PyInstaller 冻结环境下的 Python 插件热加载没有现成机制；
- 多进程后配置/密钥/会话文件的一致性需要重新设计。

---

## 3. 候选方案

### A. 维持单进程 + 继续懒加载（现状路线）

- 优点：改动小、调试简单、与 Qt 对象线程模型天然一致。
- 缺点：无法真正卸载已 import 的模块；主进程仍是稳定性单点。

### B. 构建期多变体（现状的延伸）

- 例如新增 `webm-min`、`webm-chat-lite` 等。
- 优点：简单可靠，纯桌宠内存/体积最优。
- 缺点：用户换功能要换安装包，不能运行时动态“打开 AI”。

### C. 非 UI 功能独立 worker 进程（推荐优先评估）

把**不需要在主进程内显示窗口**的服务移到子进程：

- Agent 联动监视器（DSH / Claude / Cursor / OpenCode / 自定义）
- 主动识屏截图、dHash、视觉请求
- 余额/网络请求长连接类任务（可选）

主进程只保留轻量 facade + 本地 IPC 事件通道。

- 优点：
  - 这些功能通常是后台轮询/线程密集，适合独立进程；
  - 崩溃不会拖垮桌宠；
  - 功能关闭 = 不启动子进程，真正实现“运行时未加载”。
- 缺点：
  - 需要管理子进程生命周期（启动/退出/心跳/崩溃重启）；
  - Qt 对象不能跨进程直接传递，需定义事件协议；
  - 截图/视觉等功能在独立进程内仍要初始化 Python/PIL/模型客户端，
    内存从主进程移到子进程；若用户一直开启，总内存未必下降。

### D. AI 聊天 UI 独立进程（高成本）

聊天窗口是 Qt Widgets UI，不能简单“后台线程化”。若拆到独立进程，可做成
“独立聊天程序”通过本地 IPC 与桌宠通信（角色切换、气泡回复、会话同步等）。

- 优点：聊天/API Key/会话存储完全隔离；聊天卡死/崩溃不影响桌宠。
- 缺点：
  - UI 跨进程通信量很大（历史、流式输出、设置联动、主题同步）；
  - 当前双 UI（modern/legacy）与角色热切换深度耦合；
  - 需要处理两个窗口的层级、置顶、跟随桌宠等细节；
  - 短期收益不明确：聊天未打开时聊天代码已基本不加载。

### E. Qt / Python 插件容器

- Qt `QPluginLoader` 适合 C++ 原生插件，对 PySide6 中纯 Python 功能模块
  帮助有限，且 PyInstaller 冻结环境下动态加载第三方 .py 插件需额外设计。
- Python `importlib` 插件容器可做“启用时才 import”，但模块一旦 import
  仍不能卸载；若要求卸载，需要把插件放进独立进程。
- 结论：**插件容器应建立在“独立进程 worker”之上**，而不是替代它。

---

## 4. 推荐架构：功能进程 + 本地 IPC

```
┌────────────────────────────────────────────┐
│ 主进程：PetApp + PetWindow + MovieLibrary  │
│  - 桌宠动画/交互/托盘/设置                 │
│  - FeatureGate：按配置启停 worker           │
└───────┬────────────────────────────────────┘
        │ QLocalSocket / JSON lines
        │ (复用 collision_ipc 的本地 IPC 经验)
┌───────▼─────────────┐   ┌──────────────────┐
│ Agent Link Worker   │   │ Proactive Worker │
│ - 监视外部 Agent     │   │ - 白名单/截图    │
│ - 事件→主进程气泡    │   │ - dHash/视觉请求 │
└─────────────────────┘   └──────────────────┘
```

### 4.1 进程边界判断

建议按以下条件决定是否拆：

1. **无主窗口 UI 依赖**：不需要与主窗口共享 QWidget 层级。
2. **可关性明确**：功能有独立开关，关闭时不启动 worker。
3. **崩溃隔离收益 > 通信成本**：后台监听/轮询/网络长任务优先。
4. **不共享可变 Qt 对象**：跨进程只传 JSON/bytes/文件路径。

符合：Agent 联动、主动识屏、余额/网络轮询（可选）。
暂不符合：聊天 UI、设置 UI、碰撞 IPC（已跨实例且属于核心多开）。

### 4.2 生命周期

每个 worker 一个 `QProcess`（或 `multiprocessing`），由主进程持有：

```
FeatureGate.enabled = true
  → ensure_worker()
  → start QProcess
  → handshake: hello / version / config_digest
  → worker emits events
  → heartbeats every N seconds
  → feature disabled / app quit
  → stop worker (graceful) → kill timeout
```

关键点：

- 主进程崩溃时 worker 必须能检测父进程退出并自杀；
- worker 崩溃时主进程按策略重启（带退避/次数上限）或提示；
- 配置变更通过 IPC 推送，不直接读同一文件（避免写坏/并发）；
- 密钥/API Key 只在 worker 内使用，或经一次内存握手传递，不落盘。

### 4.3 需要新增的模块草案

```
pet/workers/
  __init__.py
  base.py            # WorkerProcess + WorkerClient + 心跳/重启
  agent_link_worker.py
  proactive_worker.py
  protocol.py        # 消息信封、事件类型、错误码
```

主进程侧保持现有 `AgentLinkManager` / `ProactiveScreenWatcher` 外观不变，
内部改为“本地 worker 客户端 + 事件重放”。

### 4.4 与 Phase 1 的关系

Phase 1 已经提供 `FeatureGate` 雏形（`_sync_feature_services` /
`WindowFeatureGateMixin`）。Phase 3 可把“服务对象懒创建”升级为
“worker 进程懒创建”：

- 功能关：不 import 对应 worker 模块、不启动 QProcess；
- 功能开：启动 worker，主进程只 import 轻量 client；
- 功能关回：stop worker 并释放 client。

---

## 5. 收益 / 成本预估

| 维度 | 预估 |
|---|---|
| 纯桌宠内存 | 收益小（核心是 Qt/媒体） |
| 全功能版“不开聊天”内存 | 中：若把 Agent/主动识屏移走，可减少主进程常驻 Python/PIL/网络模块 |
| 稳定性 | 中高：后台功能崩溃不再拖垮桌宠 |
| 启动速度 | 小：worker 异步启动，不阻塞主 UI |
| 开发成本 | 高：IPC、生命周期、打包多入口、测试矩阵 |
| 调试成本 | 中高：需要跨进程日志/事件追踪 |
| 维护成本 | 中：协议版本化、配置同步、升级兼容 |

### 5.1 不建议现在做的部分

- **聊天 UI 拆进程**：除非出现“聊天打开导致桌宠明显卡顿/崩溃”的真实证据，
  否则成本远大于收益。
- **通用 Python 插件热卸载**：PyInstaller + Python 模块卸载限制多；
  若需要，应设计成“子进程插件”而不是“进程内热卸载”。

---

## 6. 落地路线（若继续）

### Phase 3A：Worker 基础设施（最小可用）

1. 新增 `pet/workers/base.py`：
   - `QProcess` 启动/停止/心跳/崩溃重启；
   - JSON lines 协议 + `hello` / `config_push` / `event` / `shutdown`。
2. 选 1 个试点：**Agent Link worker**
   - 把现有 `AgentLinkManager` 的监视器线程移到 worker；
   - 主进程保留气泡/动作呈现层；
   - 用现有 agent 事件语义定义 IPC 事件。
3. 打包：
   - 新增 `packaging/pet_worker_entry.py`；
   - PyInstaller 多入口或在主包内以 `--worker` 参数启动同一 frozen exe。
4. 验收：
   - 关闭 Agent 联动时主进程没有 agent_link worker 进程；
   - 开启后事件能驱动桌宠气泡/动作；
   - kill worker 后主进程不崩，可按策略重启或提示。

### Phase 3B：Proactive Worker

把主动识屏的截图/视觉请求移入 worker，主进程只接收“可展示事件”
（先兆气泡、结果同步等）。PIL / 视觉 provider 依赖可只进 worker 包。

### Phase 3C：长期可选

- 余额轮询、系统通知投递、Todo 提醒等是否拆，按实测内存/稳定性决定；
- 若后续出现“第三方扩展”需求，再基于 Phase 3A 的 worker 协议做插件 SDK。

---

## 7. 成熟方案对照

| 方向 | 参考 |
|---|---|
| 浏览器/桌面多进程 | Chromium / Electron：渲染进程与扩展宿主分离 |
| 编辑器扩展隔离 | VS Code Extension Host：扩展跑独立进程，主 UI 不崩 |
| Qt 原生插件 | `QPluginLoader`：适合 C++ 插件，不适合纯 Python 热卸载 |
| Python 子进程服务 | `multiprocessing` / `QProcess` + 本地 IPC |
| 本项目已有 | `collision_ipc.py` 的 QLocal IPC、DSH bridge、ffmpeg 子进程 |

---

## 8. 结论

- Phase 3 的**正确形态不是“把一切插件化”**，而是：
  **把无 UI 依赖、后台型、可关闭的功能拆成独立 worker 进程。**
- 当前最值得试点的是 **Agent Link worker**，其次是 **Proactive worker**。
- AI 聊天 UI 拆分应继续搁置，直到有真实性能/稳定性证据支持。
- 纯桌宠用户已从 Phase 1/2 获得主要收益；Phase 3 主要服务“全功能版 +
  长期稳定性/可扩展性”。

> 本文档为设计评估，不包含本轮代码改动。
