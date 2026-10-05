# Codex companion 独立版本贡献报告

> 基线：`0227a6e`；分支：`feat/codex-companion`；日期：2026-10-06。
> 操作说明及设置契约：[CODEX-COMPANION.md](CODEX-COMPANION.md)。
> 本报告登记于 [INDEX.md](INDEX.md)，按 [PR-REPORT-TEMPLATE.md](PR-REPORT-TEMPLATE.md) 交付。

## 一、核心特性

新增明确启用的 Codex 桌宠入口，使用独立用户配置，连接已登录的 Codex app-server，提供账号聊天、额度条/提醒/趋势、多工作状态及界面外观、语言、头像和 YouTube Music 扩展。普通入口保持原有提供商与界面；没有导入用户的 API/OAuth 密钥、聊天记录、Hooks、额度历史、机器路径或修改过的二进制。

| 能力 | 可见结果 |
|---|---|
| 登录账号聊天 | 通过本机桥接使用 catalog 默认/指定模型，低推理档，临时线程无 Codex 文件/工具执行。 |
| 额度与趋势 | 点击桌宠显示短期/周额度，阈值提醒；五小时从消耗开始向后绘图，七天显示观测到的每日周额度增长。 |
| 并行工作 | 所有活动主线程独立显示；收合仍更新，多于三行滚动查看，完成与输入通知独立已读。 |
| 界面 | 简中/繁中/英语，六种独立表面主题与灵动岛原主题、实时预览/取消、字体大小和自定义头像。 |
| 音乐 | 原生 Windows 媒体会话自动发现，暂停后保留控制目标，歌词完整换行，灵动岛悬停三圆形按钮。 |

**不变量**：物理/碰撞/解码计算、进程内多宠基础层及普通入口未替换；`window.py` 与 `modern_settings_dialog.py` 未增加行数。没有恢复已删除的跨进程多宠模块。本版本目前要求 PetWindow 拓扑，overlay 明确拒绝；贡献范围和边界一起提交审阅。

## 二、修改文件说明

下面表格取 `git diff --cached --numstat`，新增文件计入；没有删除已有上游文件。新增资源只包含文本目录，不包含运行配置、聊天或媒体二进制。

<!-- FILE-TABLE-BEGIN -->
| 文件 | 增删 | 改动意图 |
|---|---|---|
| `README.md` | +2 / −0 | 登记可选入口，方便用户找到独立版本说明。 |
| `THIRD_PARTY_NOTICES.md` | +8 / −0 | 保留 OpenCC 字典来源和 Apache-2.0 归属。 |
| `docs/CODEX-COMPANION.md` | +81 / −0 | 说明启动、设置作用域、采样开销、能力和退出/回滚。 |
| `docs/INDEX.md` | +2 / −0 | 在功能目录和 PR 存档登记新文档。 |
| `docs/PR-REPORT-CODEX-COMPANION-2026-10-06.md` | +180 / −0 | 按贡献约定交付逐文件、实测性能、实机和验证证据。 |
| `packaging/pet_entry.py` | +15 / −1 | 冻结入口提前分流设置/桥接；普通启动仍调用原 app.main。 |
| `packaging/pet_entry_no_chat.py` | +3 / −0 | 在缺少 Chat 能力时明确拒绝独立版本入口。 |
| `pet/__main__.py` | +15 / −0 | 增加可选入口和设置子进程分流，普通入口保持原路径。 |
| `pet/autostart.py` | +28 / −1 | 按独立变体隔离启动项，保留 profile 并正确引用源码/冻结目录。 |
| `pet/bubble_polish.py` | +141 / −0 | 以统一圆角、描边和配色绘制气泡，并保留呼吸形态。 |
| `pet/codex_bridge/__init__.py` | +1 / −0 | 声明独立可导入桥接包。 |
| `pet/codex_bridge/__main__.py` | +127 / −0 | 模型选择、独立配置、keyring 保存、启动/停止所创建的进程。 |
| `pet/codex_bridge/hook.py` | +52 / −0 | 只保存状态/时间的权限与生命周期 Hook，移除 prompt/tool 输入。 |
| `pet/codex_bridge/hooks.py` | +65 / −0 | 生成或明确安装附加 Hook，合并/备份已有配置。 |
| `pet/codex_bridge/rpc.py` | +325 / −0 | app-server stdio 客户端和临时账号聊天，不复制 OAuth。 |
| `pet/codex_bridge/server.py` | +161 / −0 | 有认证的回环 ChatCompletions/usage/models 端点与并发边界。 |
| `pet/codex_bridge/usage.py` | +102 / −0 | 额度语义归一化、未知值和重置感知的只读缓存。 |
| `pet/codex_companion.py` | +106 / −0 | 启动前安装与签名验证，协调设置、预览和每窗控制器。 |
| `pet/codex_companion_adapters.py` | +842 / −0 | 具名源代码适配器保留现有方法，避免修改大窗口/设置模块。 |
| `pet/codex_languages/OpenCC-LICENSE.txt` | +55 / −0 | 随派生繁体字典保留完整上游许可。 |
| `pet/codex_languages/en.json` | +907 / −0 | 离线英语界面目录，避免模型翻译开销。 |
| `pet/codex_languages/traditional_chars.json` | +1 / −0 | OpenCC 派生字符回退表，支持尚无整句目录的中文 UI。 |
| `pet/codex_languages/zh_CN.json` | +26 / −0 | 新增功能的简体目录。 |
| `pet/codex_languages/zh_TW.json` | +1680 / −0 | 离线繁体界面目录。 |
| `pet/codex_quota_state.py` | +123 / −0 | 持久化三个阈值的已提醒状态并按额度重置重新计数。 |
| `pet/codex_usage.py` | +508 / −0 | 显示可点击额度条、监控开关、后台读取和提醒。 |
| `pet/codex_usage_chart.py` | +412 / −0 | 主题独立的短期曲线/七天图，百分比文案与未来留白。 |
| `pet/codex_usage_history.py` | +205 / −0 | 有界采样历史、消耗起始横轴、断线和每日观測增长。 |
| `pet/codex_work_reader.py` | +174 / −0 | 只读并行主线程状态、输入事件和独立已读 ledger；无最近24项截止。 |
| `pet/codex_work_status.py` | +414 / −0 | 灵动岛多行/圆角滚动与各线程状态计时；收合背景更新和短暂提醒。 |
| `pet/config.py` | +37 / −0 | 十个普通键的默认值、reload 与无效数据归一化；独立 profile 环境入口。 |
| `pet/custom_avatar.py` | +206 / −0 | 导入图片/GIF、独立配置副本和取消清理；已有鱼图标。 |
| `pet/independent_ui.py` | +294 / −0 | 六类外观控件、主题和保存逻辑，保留灵动岛原独立主题。 |
| `pet/island_embedded_chat.py` | +256 / −0 | 隐藏桌宠时将快速聊天嵌入灵动岛并维护显隐按钮。 |
| `pet/island_music.py` | +317 / −0 | 悬停延伸的三个圆形媒体控件，与灵动岛主题一致。 |
| `pet/language_ui.py` | +341 / −0 | 加载目录、观察/刷新 UI；排除输入、路径和聊天正文。 |
| `pet/overlay_layout.py` | +120 / −0 | 气泡与聊天避让、额度窗层级和定位。 |
| `pet/ui_polish.py` | +428 / −0 | 设置/菜单/聊天共享配色、圆角、空白与布局适配。 |
| `pet/ui_preview.py` | +373 / −0 | 预览事务、心跳与过期恢复，保存前剔除临时覆盖。 |
| `pet/ytmusic.py` | +424 / −0 | YTM/浏览器发现、固定控制会话、歌词换行与字体预览。 |
| `scripts/benchmark_codex_companion.py` | +76 / −0 | 可复现实测采样持久化、趋势计算和本机读取开销。 |
| `scripts/build_linux.sh` | +1 / −0 | 包含离线语言目录，保持原素材与 no-chat 变体构建方式。 |
| `scripts/build_macos.sh` | +1 / −0 | 包含离线语言目录，保持原素材与 no-chat 变体构建方式。 |
| `scripts/build_onedir.ps1` | +1 / −0 | 包含离线语言目录，保持原素材与 no-chat 变体构建方式。 |
| `scripts/probe_codex_companion.py` | +146 / −0 | 真实 Qt 三语言/主题/宽度及保存取消、头像和聊天探针。 |
| `scripts/probe_codex_pet.py` | +85 / −0 | 真实素材解码、PetWindow 和气泡的本机离屏探针。 |
| `scripts/probe_codex_work_status.py` | +149 / −0 | 收合更新、独立计时、七行滚动和逐线程已读的真实 Qt 探针。 |
| `scripts/stress_codex_companion.py` | +95 / −0 | 测量满载 CPU，事件同步执行三轮相关时序族并收口负载进程。 |
| `tests/test_codex_autostart.py` | +44 / −0 | 确认独立变体身份及三平台 profile 命令，普通命令不变。 |
| `tests/test_codex_bridge.py` | +119 / −0 | 真实本机 HTTP 授权拒绝、只读端点/缓存和 no-chat 拒绝。 |
| `tests/test_codex_companion.py` | +197 / −0 | 配置、额度边界、历史、提醒、语言、Hooks 和独立 Qt 进程契约。 |
| `tests/test_codex_music.py` | +35 / −0 | 模拟 OS 媒体边界，验证暂停后目标固定/失效不启动游戏回放。 |
| `tests/test_codex_work_reader.py` | +95 / −0 | 并行/长期活动不被近期通知遮蔽，独立转态/已读及 Qt 回归。 |
| `tests/test_config_schema.py` | +11 / −0 | 同步新增十个普通键的 schema 快照。 |
| `tests/test_desktop_pet_features.py` | +25 / −16 | 品牌护栏聚焦原版 Qt 文案，允许可选集成的服务说明及机器键。 |
<!-- FILE-TABLE-END -->

原窗口与设置主模块保持基线 4499/2393 行；通过独立控制器和具名适配入口连接，未向主文件压缩塞入新功能。原版品牌测试由扫描所有机器键、测试和文档，调整为检查原版 Qt 用户文案；新增服务名称只允许出现在可选扩展中，原版界面继续保持中性。

## 三、实现要点

- `codex_companion.install` 在构造窗口前准备适配器，检查所有目标及参数名/类型/默认值后一次应用；相同目标的多层扩展依序组合并保留原函数。普通启动不导入这些控制器。设置子进程仅安装设置控件适配，不导入桌宠/媒体层。
- 配置新增十个普通键，默认关闭额度和工作监控，默认简中；新增独立版本首次启动才开启监控，之后保留用户开关。默认值、reload 白名单、schema 三处同步；scope/恢复/能力在使用说明中逐键登记。
- 桥接随机密码保存到系统 keyring 与权限受限的本机配置；不会搬运 Codex OAuth。仅监听回环地址，严格 Host/Bearer 检查并拒绝 Origin。聊天回合禁止 shell/apps/hooks/multi_agent，生成额度正常记入账号；额度读取没有 `thread/start` 或 `turn/start`。
- 工作读取在一个 Qt worker 线程内，每 1.5 秒只读 SQLite 与增量事件尾部；界面使用 queued signal 更新。原逻辑的 `box.isVisible()` 使收合期间丢弃更新，已取消；原最近 24 条限制/活动回合十二小时截止改为扫描全部未归档主线程，截止仅适用于结束通知。支持 null、空和 `/root` 主路径，排除系统子代理。逐线程计时与已读签名，cursor 仅保留当前回合。
- 历史只存时间、额度百分比/重置与窗口标识；保留九天、最多 20,000 条。五小时只绘制当前周期，未来留白，读数长间隔断线。周图不是官方逐日账单，而是采样得到的额度增长。
- 外观预览具有心跳和过期保护，保存写正式配置，取消恢复。媒体控件保持暂停前目标，失去目标时停止控制，避免播放游戏回放。歌词使用上游提供的数据源，不包含硬编码歌曲内容。

## 四、性能分析

环境：Windows，Python 3.10.11，PySide6 6.11.2，16 逻辑 CPU；Codex CLI 0.160.0。基准使用真实本机数据库及临时文件，无模型回合。

```sh
python scripts/benchmark_codex_companion.py --output /tmp/companion-benchmark
```

| 指标 | 样本与实测中位数 / p95 | 频率与归属 |
|---|---|---|
| 本机工作读取 | n=100，5.377 / 5.815 ms | 开启监控后每 1.5 秒，后台线程 |
| 重复工作读取 | n=300，5.264 / 5.879 ms；CPU 5.312 ms/次 | 当前七行，300 次后 RSS −9,977,856 B；不据此声称长期零泄漏 |
| JSON 保存 | n=20，34.701 / 35.506 ms | 九天 12,960 采样、两窗口，最大每分钟一次 |
| JSON 加载 | n=20，52.351 / 58.573 ms | 启动时读取历史 |
| 五小时计算 | n=30，9.191 / 9.745 ms | 打开/更新对应趋势 |
| 七天计算 | n=30，23.205 / 24.181 ms | 打开/更新对应趋势 |
| 正常九天历史文件 | 3,163,711 B | 九天/20,000 采样硬上限，非无限增长 |
| 本机真实 Codex 模型读取 | 一次 594.27 ms，8 个模型 | 启动时，不证明每个模型均有账号使用权限 |
| 本机真实额度读取 | 一次 691.33 ms，2 个窗口；缓存命中 0.011 ms | 60 秒 GUI 轮询，桥接 30 秒缓存 |

活动状态扫描修正的前后各 n=30：中位数 5.416 → 5.193 ms，p95 5.703 → 5.780 ms。本机样本规模很小，不能外推为大规模加速收益。

稳态变化：普通入口没有新增轮询；可选版本增加工作 reader 一个 Qt 线程、桥接一个子进程与按请求产生的 HTTP/RPC 线程。每次新额度采样启动/结束 Codex app-server 子进程并读取账号额度，工作状态不发生网络请求。预览 180 ms 本机轮询、设置保存 90 ms 防抖、1 秒心跳，语言观察 750 ms；弹窗跟随只在显示时启动 40 ms 位置计时器。

磁盘变化：JSON 采用原子替换，每分钟重写当前历史；九天满采样稳态写量约 4.56 GB/天。保留此格式方便已有本地历史读取，后续可评审增量存储；没有把它描述成零磁盘开销。通知仅在状态变化或已读时写入。历史有条数和时间上限，已读 ledger 最多 200 项、事件尾部每次最多 1 MiB；本机 hook 旧 session 文件没有自动清理。内存只有短期 RSS 探针证据，没有 24 小时浸泡结果。

## 五、实机运行记录

真实代码/本机数据已运行，测试不等同于查看使用者屏幕。离屏渲染使用本机字体和真正的 Qt 控件；没有伪造产品界面截图。

```sh
QT_QPA_PLATFORM=offscreen python scripts/probe_codex_companion.py --output /tmp/companion-ui --screenshots
QT_QPA_PLATFORM=offscreen python scripts/probe_codex_work_status.py --output /tmp/companion-status
QT_QPA_PLATFORM=offscreen python scripts/probe_codex_pet.py --output /tmp/companion-pet
```

实际结果：18 组设置界面（3 语言 × 3 主题 × 720/1100 宽度）通过实时预览、保存、取消恢复、独立聊天主题、内嵌聊天与导入头像取消清理。并行状态 probe 输出：

```json
{"collapsedBackgroundUpdates":true,"independentConcurrentPhases":true,"allSevenRowsScrollable":true,"perThreadAcknowledgement":true,"threeThemes":true,"modelTurns":0,"directDesktopInspection":false}
```

动画 probe 使用真正 `PetWindow` / `MovieLibrary`，106 个本地素材，并等待真实解码帧；`realDecodedFrame=true`，文字气泡可见。截图经人工检查，状态卡首两行、滚动条及底部按钮均在窗口内；字体与气泡渲染可读。

并行回归在改产品前失败：30 个新完成项遮蔽较早活动任务时 active dict 为空；收合岛的 Qt probe 报 `Collapsed island discarded concurrent status updates`。修正后长期活动线程、不同状态同时显示、逐项已读及换状态保留通过。

已安装的本机独立版本只替换两个状态 helper，1341 个其他模块和启动器字节保留；启动后七项本机状态写入缓存，GPT-6 Luna 健康检查正常，设置字节、聊天、额度历史前缀、Hooks 与开机启动保持。真实机器上的其他 Codex 项目当前已结束，所以并行活动切换使用独立 SQLite fixture + 真实 Qt 验证；未启动别人的工作或发出额外模型请求。

失败/置灰边界：no-chat 入口明确拒绝；overlay 安装拒绝；keyring/保存失败中止启动；无数据图表保持未知；失去指定媒体会话返回空目标。真实 loopback 拒绝缺失/错误密码、外部 Host 与 Origin，读端点不会调用生成。Codex 本机 RPC 实测为 ChatGPT 登录，未输出凭证；模型 catalog 仅证明存在，未额外消耗额度验证每个模型。

Windows 前景能力探针：沙盒内 `foregroundWindowAvailable=false, visionResultAvailable=false`，沙盒外两项为 true。该环境差异通过沙盒外全量测试处理，未修改上游视觉逻辑。macOS/Linux 实机与发行构建不可在本 Windows 主机确认；原生桌面 UI 自动化禁用，因此报告没有声称直接查看桌面。YTM 的公共贡献版有媒体会话边界回归，本轮没有重新启动用户音乐验证歌词网络来源。

## 六、测试与验证

<!-- TEST-RESULTS-BEGIN -->
最终验证环境设置 `QT_QPA_PLATFORM=offscreen`、`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，PATH 包含临时 Node/npm 工具目录。Windows 全量在具有前景窗口访问能力的本机环境执行。

| 门 | 命令 | 真实结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests scripts` | All checks passed |
| 全量（含架构/设置/报告门禁） | `python -X utf8 -m pytest -q` | 4325 passed，14 skipped，15 warnings；316.80 s |
| 桥接/并行 reader/入口聚焦 | `python -X utf8 -m pytest -q tests/test_codex_bridge.py tests/test_codex_work_reader.py` | 11 passed；2.34 s |
| 文档门禁（最终报告补全后） | `python -X utf8 -m pytest -q tests/test_pr_report_discipline.py` | 67 passed；0.48 s |
| 发布前通用路径护栏/文档复核 | `python -X utf8 -m pytest -q tests/test_codex_companion.py::test_extension_has_no_binary_or_user_profile_dependencies tests/test_pr_report_discipline.py` | 68 passed；0.50 s；仅将测试中的本机名称断言改为通用路径断言，产品代码未变 |
| 媒体目标边界 | `python -X utf8 -m pytest -q tests/test_codex_music.py` | 2 passed |
| 受影响时序族满载三轮 | `python scripts/stress_codex_companion.py --output /tmp/companion-stress` | 16 负载进程，每轮测得 CPU 100%；每轮 68 passed；进程墙钟 66.44 / 66.73 / 69.05 s |
| 真实 Qt 设置/状态/动画 | 本报告第五节三个 probe 命令 | 18 组设置；七项并行滚动/已读；真实解码帧与气泡可见 |
| 真实 Codex 只读 RPC | `model/list` 与 `account/rateLimits/read` | 已登录；8 个模型、2 个额度窗口；缓存复用；0 个模型回合 |

满载脚本覆盖 `test_codex_companion`、`test_codex_work_reader`、`test_codex_bridge`、`test_settings_process_isolation`、`test_codex_autostart`、`test_autostart` 六个时序族，三轮均 exit 0，负载进程已结束。真实 Qt 的源代码探针与本机安装版探针均通过；回归在修改前能复现失败，修改后转绿。
<!-- TEST-RESULTS-END -->

初次基线 4289 passed、14 skipped，2 项环境失败：Node bundle 不含 npm/npx、Windows 子进程编码；临时工具目录补 npm 与设置 PYTHONUTF8/PYTHONIOENCODING 后，两项独立通过。贡献首轮全量 4309 passed、14 skipped，失败三项（机器字符串品牌测试、设置入口静态契约、沙盒前景视窗）；前两项已修正并聚焦通过，前景能力已在沙盒外证实。未删除或 deselect 测试以隐藏失败。

## 七、已知限制与后续

- 贡献为 opt-in PetWindow 版本；overlay 不支持，需按上游新的 sprite 服务接入点另行迁移。
- source 适配器检查签名且真实窗口已运行，但仍依赖 Qt 类的现有内部字段；未来大版本需要再次验收这些边界。维护者可在审阅中要求逐功能拆分，此提交保留用户授权的完整扩展范围。
- Codex 本机 SQLite/rollout 是版本相关读取；数据库版本变化显示不可用，未操作远程主机的工作。
- 额外模型授权、完整发行构建、macOS/Linux 实机、24 小时内存浸泡与新的现场 YTM 歌词验证未宣称完成。模型文字回复经完整结果分块发给上游 ChatCompletions 客户端，不是真实逐 token 推送。
- JSON 历史重写与旧 Hook 文件清理的成本明确列出，后续可评审增量持久化和 session 清理。

## 八、风险与回滚

关闭两个监控开关即停止相应轮询；退出独立入口并用普通 `python -m pet` 启动可回到原版。独立配置保留，不覆盖原版数据。回滚提交后可保留独立配置供再次启用，若要移除须由用户明确选择该配置目录和独立启动项。普通未知键加载遵循上游 Config，新增值有严格归一化，不触碰既有密钥和角色/slot 历史。
