# Phase 3C：剩余执行风险与选装交付评估

> 评估日期：2026-09-27。状态：**本地代码与测试审计已完成，迁移决策待各实施阶段验证；本轮不增加 Worker、不改协议或菜单。**
> 前置证据：[3B 收尾记录](PHASE3B-STABILITY-CLOSEOUT.md)（全量环境门及人工细项仍待通过）。
> 唯一功能归属表：[功能交付总表](../plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md)；阶段顺序：[总路线](../plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md)。

## 1. 目标、方法与结论

目标不是再拆几个进程，而是找出阻塞、取消、退出、凭据和依赖所有权风险，决定哪些需要隔离、哪些应先做交付拆包。
本次实际阅读调用入口、执行体、停止路径及测试；不把关键词命中当结论。静态发现表示风险或测试缺口，**不等于已经复现产品故障**。
未请求真实账户、视觉/聊天模型或语音服务，未截取用户屏幕，未做长期运行测试。本文的线程数、超时值和缓存上限来自代码，不是性能实测。

**结论：**

1. AI 对话与文件理解是下一项主要拆包目标；先审计流式执行和会话边界，不强制 Chat QWidget 跨进程。
2. 屏幕理解仍有聊天模型/HTTP/配置依赖，必须交给 Phase 4A；懒加载和独立进程都不等于独立安装。
3. 音乐采集、歌词网络、语音合成分别评估，不默认合并成一个大 Worker。
4. 余额默认保留后台线程；Harness/bridge 先解决已有进程与外部配置的所有权；低风险提醒/台词保留受控进程内实现。
5. 本次评估及 Phase 4A 只读拆包审计可以并行；**不能要求所有功能 Worker 化后才做可拔除样板**。3B 未关闭的验收门也不能靠新迁移来替代。

## 2. 候选决策总表

| 对象 / 归属领域 | 当前运行位置与入口 | 判断及不立即迁移的理由 | 移交阶段 / 回滚接缝 |
|---|---|---|---|
| AI 对话、文件理解 / AI 功能包 | ChatService 的 QThread；文件解释调用同一服务 | 优先准备 Provider 执行隔离；UI 留主进程但随包交付。不能把一次性识屏 response 当流协议 | Phase 4A 可并行审计；样板稳定后 Phase 5B 主要实施。服务 facade 保留旧实现作为已安装功能的回滚路径 |
| 屏幕理解与聊天共享依赖 / 屏幕理解包 | 主进程配置解析 + 识屏 Worker | 必须拆清 DTO/HTTP/凭据解析归属，不新建 Worker 来掩盖打包耦合 | Phase 4A 必解；本地内置实现作对照基线，不用隐藏 fallback 绕过卸载 |
| 播放器采集与歌词 / 音乐包 | 平台采样线程 + 取词线程/线程池 + GUI 展示 | 两种执行风险分开审计；先补取消、队列和占用证据，再决定是否隔离 | Phase 5B 音乐批次；保持采样、取词、展示各自端口 |
| 合成、播报策略 / 语音与报时包 | Python 线程 + async 网络合成 + Core 音频呈现 | 合成是 Worker 候选，但不为每次播报创建常驻进程；播放端口不能被整包绑架 | Phase 5B 语音批次；保留合成接口和通用播放端口 |
| 余额、账户用量 / 账户包 | AppShell 触发后台线程，缓存复用 | 暂不新增常驻 Worker；先保证超时、请求代数、停止后副作用和凭据边界 | Phase 5B 账户批次；复用请求接口，无需让 Agent 状态依赖余额 |
| Harness、bridge、外部启动 / Agent 或快捷启动包 | 已有外部进程、hooks 和配置操作 | 先核验进程与配置修改所有权；额外套 Worker 不自动解决误停问题 | Phase 4A 形成所有权合同，Phase 5B 相应包实现；显式同意后恢复本包改动 |
| 提醒、日常台词等 / 各低风险官方包 | GUI 线程、受限调度器/事件/展示端口 | 不需要天然进程隔离；需要撤销贡献、配置和定时器所有权 | Phase 4B 验证贡献模型，Phase 5B 推广；停用官方 factory，不热卸载 Python 模块 |

以上是有依据的默认决策，不是尚未执行的迁移已经完成。合包数量、Worker 常驻数量、最终包 ID 不在本轮冻结。

## 3. AI 对话与文件理解：第一优先审计

### 3.1 当前依据与所有者

- [chat/service.py](../../pet/chat/service.py)：`_Worker(QThread)` 执行 Provider；`ChatService` 用 queued Qt 信号接收增量，通过 request ID 排除旧结果。取消设置 Event 并关闭该 worker 持有的 response；`_workers` 保留尚未结束的旧请求。
- [chat/providers.py](../../pet/chat/providers.py)：标准库 HTTP/SSE，分块读取后产生文本增量；连接/读取使用请求超时，finally 关闭响应，取消路径不应误关其他会话的 response。
- [file_interpret.py](../../pet/file_interpret.py)：`FileInterpretController` 取得用户确认后，用既有 ChatService、PromptBuilder、SessionStore 解释选定文本/代码文件；读取上限 200,000 字符，不是任意 PDF/二进制解析器，不改变源文件。
- [chat/models.py](../../pet/chat/models.py) 的 Provider 数据、[chat/session_store.py](../../pet/chat/session_store.py) 的会话存储随 AI 领域评估；UI、会话选择、角色/实例隔离与文件确认由功能包的主进程部分负责。keyring 访问必须经过 Core 授权边界，Worker 不接收完整配置。

当前这些职责尚未物理拆包。后续 UI、文件路径、会话 ID、附件、缓存、设置/快捷键均要有包所有者；Core 只保留通用宿主与授权/呈现服务，不永久托管整个 Chat 实现。

### 3.2 具体风险，不夸大为已复现缺陷

- `ChatService.shutdown()` 对每个未结束 worker 调用 `wait(1500)`，无法及时结束时保留引用并返回失败。多个挂起请求可能累加退出等待；应测量上界，不能只测一次成功请求。
- 请求获得 response 之前的连接阶段与 response 关闭取消不是同一条件；需要确定性替身模拟阻塞连接、阻塞流及取消竞态。
- UI 丢弃旧 request ID 能阻止展示，但不等于网络已停止、历史回调已撤销或所有内存已释放。
- 文件解释复用聊天服务是正确复用点；不能另建第二套 Provider/Key/流式服务。卸载要取消文件确认与进度回调，同时默认保留会话。

### 3.3 未来流协议的设计验收（本轮不扩展协议）

| 项目 | 必须保持的语义 |
|---|---|
| 增量输出 | 请求 ID、来源窗口/实例/会话和 generation 明确；增量有顺序，拼接不能跨会话 |
| 终态 | 完成、取消、错误分别定义；每请求至多一个终态，终态之后不再写 UI/会话 |
| 背压 | 帧长、单请求缓冲、总队列和累计内容上限；合并增量不能丢最终文本 |
| 取消与退出 | 连接前、流中、完成交错、窗口关闭与进程退出都可验证；不可中断时明确超时与进程兜底 |
| 凭据与数据 | 单次授权、日志脱敏、附件/会话仅发送所需部分；崩溃日志不包含 Key/完整请求 |
| 回滚 | 保留 ChatService 公共 facade；仅已安装启用时允许旧执行实现，不因卸载自动恢复隐藏聊天 |

现有依据：[test_chat_service.py](../../tests/test_chat_service.py) 覆盖并发发送、worker 集合清理、不可中断退出、response 归属和阻塞流取消；[test_file_interpret.py](../../tests/test_file_interpret.py) 覆盖大小上限、禁用/拒绝/忙碌/缺 Key、会话写回及错误复位。
缺口是**未来真实进程的流式背压、断流重启、跨实例路由与卸载持久化竞态**，现有线程测试不能冒充这些门已经通过。

## 4. 屏幕理解：Phase 4A 必解依赖

真实调用链：

- [proactive.py](../../pet/proactive.py) `_resolve_vision_provider()` 读取聊天配置并解析视觉 Provider；
- [proactive_screen_worker.py](../../pet/workers/proactive_screen_worker.py) `_provider_from_arguments()` 仍使用 `pet.chat.models.ProviderConfig`；
- [vision.py](../../pet/vision.py) 复用聊天 Provider 的 HTTP 辅助；
- [proactive_screen_adapter.py](../../pet/workers/proactive_screen_adapter.py) 只发送所选 Provider 参数，单次 Key 不进入 `config_push`；
- [chat/__init__.py](../../pet/chat/__init__.py) 懒导出解决 Worker 的 GUI import 隔离，**不解决 no-chat 构建排除 pet.chat/keyring 后的功能交付依赖**。

Phase 4A 要形成文件级 import/构建归属清单：哪些是最小服务 DTO/传输库，哪些归屏幕理解包、AI 包或显式共享依赖；不能为了共用 HTTP 将整套 AI 移回 Core。

硬验收：**不装聊天也能配置视觉服务并使用屏幕理解；不装屏幕理解时 Core 不含其专属代码、设置与后台执行。**同步聊天只通过可选端口，缺包不报 import 错误。现有自动/手动、白名单、quota、临时凭据、共享 Worker 和 fallback 语义仍需保留。

依据：[proactive Worker adapter/source/integration 测试](../../tests/test_proactive_worker_adapter.py)、[watcher 接入测试](../../tests/test_proactive_watcher_worker.py)、[shared 测试](../../tests/test_single_process_shared.py)。这些证明运行边界，不证明文件剥离或卸载完成。

## 5. 音乐：采集和取词不要混成一个大执行器

[音乐控制器](../../pet/music_lyric_controller.py) 由 Qt 定时器调度后台采样线程，用 Event 唤醒/停止，并有取词线程；[music_detect.py](../../pet/music_detect.py) 含平台音频会话检测，[now_playing.py](../../pet/now_playing.py) 负责播放器信息路径。

[取词执行](../../pet/music_lyric.py) `fetch_lyrics()` 同时查询三个来源、带截止时间与优先级宽限、命中缓存则复用；线程池 `shutdown(wait=False)` 不等于正在请求的 HTTP 被取消。控制器停止/换曲后丢弃旧 key 的结果，但被平台调用或网络阻塞的线程仍需等返回。

- 包拥有歌曲状态、歌词策略/UI、取词缓存及入口；Core 提供通用展示/音频/调度端口。播放器本身是外部程序，不由卸载音乐包自动关闭。
- [网络/代理约定](../NETWORK-PROXY-AND-VPN-2026-09-22.md) 是兼容约束：歌词的 `ProxyHandler({})` 直连不得因共用 Worker HTTP 层被改成跟随系统代理；其他服务也不能被一刀切直连。
- 补证据：快速换曲的总并发上界、停止后的缓存写入、平台采样卡住时退出时间、缓存离线命中。先用确定性平台/HTTP 替身，不访问真实音乐服务。
- [test_music_lyric.py](../../tests/test_music_lyric.py)、[test_music_detect.py](../../tests/test_music_detect.py)、[test_music_player_cache.py](../../tests/test_music_player_cache.py)、[test_music_sing_timer.py](../../tests/test_music_sing_timer.py) 提供现有解析、缓存、检测与调度回归。未来卸载门尚未实现。

若平台调用的不可中断风险或线程累积有实证，再单独决定采样或取词 Worker；当前不承诺常驻两进程。

## 6. 语音与报时：合成执行与通用播放分开

[voice_chime_service.py](../../pet/voice_chime_service.py) 的 `_TTSWorker` 是 daemon Python 线程，含音色表获取、在线合成、重试、MP3 缓存；Qt 接收结果并按策略播放。`stop()` 清队列、停定时器并置 stopped，迟到结果被忽略，但不等于已经中断合成网络线程。

- 语音包拥有 TTS Provider/音色、报时和朗读策略、合成缓存、设置；Core 保留可供文字提醒/其他包使用的通用音频呈现端口。
- 当前缓存目录及策略集中在服务中；拆包要确定缓存 owner、原子写入、部分文件清理、取消时是否继续写、数据保留策略。
- 进程隔离仅作为合成执行候选；应先对比合成频率、阻塞风险、单进程复用成本，不为每句播报增加常驻 Worker。
- [test_voice_chime_service.py](../../tests/test_voice_chime_service.py) 覆盖队列只保留最新待播、退出停止、服务启停及迟到处理；[test_voice_chime.py](../../tests/test_voice_chime.py) 保护纯调度。缺口为合成正在写缓存时卸载/重装、网络不返回时有界退出和未来进程取消。
- 没安装语音包，生活提醒仍展示文字；语音包不能成为提醒/桌宠基础运行的必需依赖。

## 7. 余额与账户：不默认增加常驻 Worker

[balance.py](../../pet/balance.py) `fetch_balance()` 使用带 10 秒默认超时的 GET，处理 HTTP/连接/JSON 错误；HTTP headers 辅助也来自聊天代码，需列入可选包依赖审计。
[app.py](../../pet/app.py) 的刷新入口使用 `_balance_busy` 防重入、后台线程、Qt 桥接、30 秒内存缓存与按 Provider 分开的文件缓存；文件替换是事务的一部分，不等于可卸载生命周期已成立。

- 账户包拥有余额/用量策略、缓存、刷新时钟、展示和菜单设置；Core 负责授权，不把 Key 明文迁移到普通配置。
- 主要风险是停止、切换 Provider 或卸载后的迟到写入/提示、busy 复位和重试频率，不是现有一次请求天然需要独立进程。
- [test_balance.py](../../tests/test_balance.py) 保护解析/错误、刷新与线程启动失败复位。后续补在途卸载、Provider generation、账户不安装时 Agent 基础状态仍可用。
- 先保留线程适配器；只有阻塞、扩展到长轮询或与现有共享执行器复用有明确收益时，再评估进程方案。不得借此恢复“Agent 必须装余额”的硬依赖。

## 8. Harness / bridge：先确认谁拥有进程与外部配置

[harness_launcher.py](../../pet/harness_launcher.py) `_spawn()` 记录自己启动的 Popen；`launch()` 也会复用已监听的服务。`stop_harness()` 先核验所有监听 PID 的命令行，拒绝未知/外来进程，然后执行停止并等待端口关闭。
**“命令行像本项目服务”不等于“这个服务由本次安装的功能包启动”。**现有 GUI 显式停止/重启可要求用户确认，不能直接挪作静默卸载钩子。

- Agent 包负责 bridge/hooks 的安装意图、配置备份/撤销及启动的外部进程标识；用户启动的服务默认不归包退出/卸载清理。
- 3A 日志采集 Worker 不拥有 bridge 安装、端口上的全部服务或外部工具配置。
- 新增隔离层不能替代进程 owner、启动代数和 PID 重用检查；不以全机器名称匹配清理子进程。
- [test_harness_lifecycle.py](../../tests/test_harness_lifecycle.py) 覆盖外来/未知监听者拒绝、全量属主先核验、停止后端口仍开、用户拒绝以及进程组边界；[test_agent_link_dep_specs.py](../../tests/test_agent_link_dep_specs.py) 保留联动声明回归。
- 未来补“用户已运行服务 + 包启用/停用/卸载不终止服务”、本包启动子树退出、外部配置被用户并发修改后不覆盖恢复。默认不再套一层常驻 Worker。

## 9. 低风险官方功能：保留受控 in-process

[官方节日插件](../../pet/plugins/) 已验证 Context、配置命名空间、事件、调度和命令 owner。提醒、日常台词、文字通知并不因成为可选包就必须进程化。

所有者应扩展到菜单/托盘、设置页、搜索项、快捷键、配置和资源 provider；功能 UI 可在 Core 进程但代码随包交付。停用不再执行，卸载撤销贡献，文件占用时明确待重启；不要求 Python 模块热卸载。
依据：[test_plugin_runtime.py](../../tests/test_plugin_runtime.py)、[test_festival.py](../../tests/test_festival.py)。现有生命周期通过不等于贡献注册/真实可拔除已完成。

## 10. 后续验收与执行顺序

| 项目 | 行为门与当前缺口 | 当前回归入口 / 下一步 |
|---|---|---|
| 3B 稳定性 | 人工细项与本次前台环境导致的全量失败未关闭 | 见 [收尾报告](PHASE3B-STABILITY-CLOSEOUT.md)，先确认桌面环境再复验，不靠增加 Worker 代替 |
| 4A 屏幕理解审计 | 专属代码/依赖/UI/fallback 都有 owner，最小 Core 不含功能；配置迁移有备份/恢复 | 原识屏、共享窗口、资源安装测试作基线；新增 import/build 清单、候选包形态与可信加载决策 |
| AI 流式审计 | 数据/会话/附件/Key、协议终态/背压/取消明确；独立文件解释服务禁止重复 | `python -m pytest -q tests/test_chat_service.py tests/test_file_interpret.py`；确定性流/连接替身再升级真实进程测试 |
| 音乐/语音/账户 | 总并发、迟到副作用、退出、卸载有界；无真实网络前提 | 上述各测试族；选装批次前补缺口，实测后决定进程数 |
| 外部服务 | 创建者与使用者分离，用户确认，卸载不误停外部服务 | `python -m pytest -q tests/test_harness_launcher.py tests/test_harness_lifecycle.py tests/test_agent_link_dep_specs.py` |
| 低风险贡献 | 安装出现入口、停用不执行、卸载撤销、重装保留偏好 | `python -m pytest -q tests/test_plugin_runtime.py tests/test_festival.py`；Phase 4B 先做样板 |

本轮全量已执行（结果不是全绿，见收尾报告）；没有额外运行真实 AI、播放器账户或 TTS 探针来凑评估数字。文档门由链接、PR 纪律与 diff 检查负责；迁移实施门不能借文档通过而关闭。

下一步推荐：关闭 3B 待验收 → Phase 4A 冻结屏幕理解文件/依赖/配置/可信加载/独立构建方案 → 4B 本地安装卸载和贡献样板 → 5A Setup/ZIP/便携 → 5B AI 对话与文件理解。3C 的补充审计可以穿插，不同时重写多个业务系统。

每次迁移保留受限 facade 和明确回滚点；尚未安装或已卸载时绝不调用内置旧实现。第三方 SDK/Workshop 仍是条件项，官方功能选装不是。

## 11. 给使用者的最终效果

本轮只增加“该隔离什么、为什么、先后怎么做”的依据，**不会增加后台进程、重新安排菜单或改变识屏操作**。

现在文件仍在主程序的 `pet/`（包括 `pet/workers/`），独立 Worker 用 QProcess + 本机 stdin/stdout JSONL 与 Core 连接；不是要你手动复制/删除的 DLC。

先把屏幕理解变成真正可装可卸载的官方包，再重点做 AI 对话与文件理解。届时未装功能不出现专属菜单、设置和后台任务；装好后注册入口，应用内卸载默认保留偏好。包目录、文件格式、便携标记和安装器如何选装由 Phase 4A/5A 验证后确定，本轮不虚构已经可用的路径或安装命令。
