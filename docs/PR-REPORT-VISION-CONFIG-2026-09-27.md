# Phase 4A 第二步：独立视觉配置、凭据与确认迁移实施报告

> 工作树基线：`b97112d2a65d1af1a8d3396875afac0cdb984337`，分支 `codex/phase3-worker`。
> 本轮保留此前尚未提交的平台查询拆分和设计文档；没有暂存、提交或推送。
> 文件名沿用本轮计划日期 2026-09-27；以下本机执行日志发生于 **2026-09-28（Asia/Shanghai）**。
> 设计入口：[Phase 4A 最小拆包设计](plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md)；[文档索引](INDEX.md)。

## 一、核心特性与验收结论

实现屏幕理解自己的自动/手动 profile、凭据作用域、显式确认迁移与设置组件。关闭或不加载聊天，不再阻止已配置的识屏请求；聊天可用时仍尝试同步分析文字。首次使用必须确认迁移或手动配置，**不静默使用旧聊天 Key**。一个模式待配置不阻塞另一个模式。

这只是配置与凭据独立，不是物理拆包。当前 Worker 仍内置，Phase 3B 的进程内 fallback 保留，但也必须使用独立配置。完整贡献注册、可信加载、独立构建、安装与卸载尚未实施。

自动识屏触发策略、白名单、dwell、额度/记忆文件不迁移；关闭自动识屏仍允许手动。没有修改 Worker 协议版本、Core 更新、公开 CLI 或打包配置。

## 二、修改文件说明

以下增删使用 `git diff --no-index --numstat` 比较：此前已改文件对本轮开始时 `.scratch/phase4a-vision-config/baseline/` 快照，其余已跟踪文件对 HEAD，新增文件对空文件。**不能将当前整个 `git diff` 都归给本轮。**新增文件全部列出，本轮没有删除/移动文件。报告自身计数是生成后的行数。

### 实现

| 文件 | 本轮增删 | 比较基线 | 改了什么、为什么 |
|---|---:|---|---|
| `pet/screen_understanding/__init__.py` | +1 / −0 | 新增 | 独立功能模块入口；不导入聊天或 GUI。 |
| `pet/screen_understanding/models.py` | +132 / −0 | 新增 | VisionProfile、VisionSettings、VisionRequestConfig 及字段校验；区分持久化配置与单次授权凭据。 |
| `pet/screen_understanding/config.py` | +157 / −0 | 新增 | 按自动/手动绑定解析独立配置；实现 revision、锁内提交、凭据事务与可恢复清理。 |
| `pet/screen_understanding/migration.py` | +151 / −0 | 新增 | 分别解析旧自动/手动有效行为，脱敏预览、确认复制、来源冲突检查；正常运行不再调用旧解析器。 |
| `pet/screen_understanding/settings.py` | +182 / −0 | 新增 | 独立屏幕理解编辑组件；显式确认迁移、配置绑定、安全保存和清理重试，不借用聊天设置页。 |
| `pet/screen_understanding/presentation.py` | +30 / −0 | 新增 | 抽出手动结果的版本验证、气泡和可选文字同步；回调失败不吞掉成功气泡，并保持 window.py 架构行数门。 |
| `pet/credentials.py` | +119 / −0 | 新增 | 按功能/实例/profile/端点/操作授权的 keyring 端口；拒绝非受支持安全后端并区分失败状态。 |
| `pet/config_transaction.py` | +73 / −0 | 新增 | 复用现有文件锁，锁内重读并原子合并；Core 的旧内存副本不能覆盖独立视觉命名空间。 |
| `pet/http_compat.py` | +52 / −0 | 新增 | 提取确实共用的端点拼接、代理/TLS 和 HTTP 基础函数；不移动 Provider 或会话业务到 Core。 |
| `pet/config.py` | +12 / −7 | HEAD | 保留 save 入口，委托窄范围合并保存，防止设置进程迁移结果被 Core 后续保存覆盖。 |
| `pet/vision.py` | +8 / −10 | 本轮前工作树快照 | 视觉执行接收独立请求模型，不查聊天 Provider/keyring；保留上一小步平台查询兼容适配。 |
| `pet/proactive.py` | +44 / −43 | HEAD | 自动识屏按独立配置授权；待配置不运行，配置变更使在途结果失效；保留策略、额度与回滚。 |
| `pet/window.py` | +15 / −16 | HEAD | 手动识屏不再要求聊天启用；待配置引导设置，Worker/进程内执行统一使用独立配置并保留冷却和忙碌提示。 |
| `pet/app.py` | +2 / −2 | HEAD | 可选识屏文字同步显式检查聊天可用与启用，独立识屏不反向启动聊天。 |
| `pet/workers/proactive_screen_adapter.py` | +3 / −18 | HEAD | 只序列化已经授权的独立单次请求，不再解析聊天 Key 或 Provider。 |
| `pet/workers/proactive_screen_worker.py` | +3 / −12 | HEAD | 无聊天依赖的请求校验与视觉执行；临时凭据仅在请求中，不进入 config_push。 |
| `pet/chat/providers.py` | +2 / −31 | HEAD | 保留旧 HTTP 辅助入口兼容导出；聊天 Provider、网络与会话语义保持。 |
| `pet/chat/ai_settings_page.py` | +22 / −22 | HEAD | 旧视觉编辑入口改为说明与跳转；兼容草稿控件由 Qt 父对象持有且不可见，避免双处编辑。 |
| `pet/modern_settings_dialog.py` | +22 / −27 | 本轮前工作树快照 | 自动化域接入独立组件、搜索与深链；无聊天构建也能编辑，保留原有非识屏功能门。 |
| `pet/settings_pet_controls.py` | +1 / −1 | HEAD | 移除旧“优先独立视觉/跟随聊天”的运行编辑控件；旧字段只留作迁移来源。 |
| `pet/context_menus/registry.py` | +1 / −1 | HEAD | 主动识屏入口不再通过聊天回调判断是否可用；保留平台限制。 |
| `pet/context_menus/shared.py` | +2 / −4 | HEAD | 手动看看屏幕入口独立于聊天开关；不重排菜单或引入完整贡献机制。 |

### 测试与开发验证

| 文件 | 本轮增删 | 比较基线 | 改了什么、为什么 |
|---|---:|---|---|
| `tests/screen_fakes.py` | +38 / −0 | 新增 | 统一临时独立配置与内存安全存储替身，不读取用户凭据。 |
| `tests/test_screen_configuration.py` | +449 / −0 | 新增 | 确认门、旧有效参数、凭据隔离、恢复/冲突/损坏配置及真实双进程保存与锁测试。 |
| `tests/test_screen_runtime.py` | +81 / −0 | 新增 | 待配置不执行、聊天关闭可运行、revision 失效与禁止聊天导入的新进程边界。 |
| `tests/test_screen_settings.py` | +180 / −0 | 新增 | 独立保存、迁移取消、存储失败、无聊天设置、浅深色与窄宽窗口/键盘检查。 |
| `tests/screen_runtime_probe.py` | +159 / −0 | 新增 | 新子进程阻断 pet.chat，真实 Qt 设置与窗口、确定性截图替身和本地 HTTP 执行链。 |
| `tests/test_vision.py` | +7 / −8 | HEAD | 原视觉网络与提示词断言转向独立请求 DTO；不再依赖隐式 Provider/keyring。 |
| `tests/test_proactive.py` | +22 / −20 | 本轮前工作树快照 | 保留既有查询回归，更新独立配置及旧解析仅供迁移的预期。 |
| `tests/test_proactive_watcher_worker.py` | +16 / −13 | HEAD | 自动识屏配置授权、Worker 连接与 fallback 回归使用独立 fixture。 |
| `tests/test_proactive_worker_source.py` | +1 / −0 | HEAD | Worker 参数校验及真实子进程禁止 Qt/chat/keyring、假截图与本地 HTTP 验证。 |
| `tests/test_proactive_worker_adapter.py` | +8 / −10 | HEAD | 独立请求序列化、临时凭据和配置快照隔离回归。 |
| `tests/test_proactive_worker_integration.py` | +13 / −13 | HEAD | 真实 QProcess、手动/自动结果、取消、冷却、错误与聊天可选同步回归。 |
| `tests/test_single_process_shared.py` | +4 / −0 | HEAD | 共享 Worker 与来源窗口路由保持，不把独立视觉配置退回聊天字段。 |
| `tests/test_feature_gating.py` | +5 / −2 | HEAD | 无聊天时识屏可用；模拟平台时不改动全局 sys.platform 以免破坏 Windows 文件锁。 |
| `tests/test_desktop_pet_features.py` | +13 / −5 | HEAD | 独立设置分组及旧开关退为迁移来源的预期，保留功能行为门禁。 |
| `scripts/benchmark_vision_config.py` | +133 / −0 | 新增 | 可复现的有界配置解析、确认迁移和 stale Core 保存基线，仅内存凭据/临时文件。 |

### 文档（本轮增量）

| 文件 | 本轮增删 | 比较基线 | 改了什么、为什么 |
|---|---:|---|---|
| `docs/plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md` | +20 / −12 | 本轮前工作树快照 | 记录第二小步真实实现、锁/凭据/设置/联动边界，以及仍待完成的交付和人工验收。 |
| `docs/plugin-phase-04-updates/README.md` | +3 / −3 | 本轮前工作树快照 | 阶段状态由查询层完成更新为查询与独立配置两小步完成；不宣称可卸载。 |
| `docs/INDEX.md` | +5 / −4 | 本轮前工作树快照 | 登记本报告并更新阶段和设置入口描述。 |
| `SPEC.md` | +5 / −4 | 本轮前工作树快照 | 明确独立配置、确认门和有限实现/验收范围。 |
| `LOG.md` | +11 / −0 | 本轮前工作树快照 | 追加本轮实现、测试、性能、失败分类及验证限制。 |
| `LOG-INDEX.md` | +1 / −0 | 本轮前工作树快照 | 追加本轮记录导航，不覆盖原平台查询记录。 |
| `docs/PR-REPORT-VISION-CONFIG-2026-09-27.md` | +215 / −0 | 新增 | 本报告：逐文件增量、实测、失败记录、验收限制与回滚。 |

### 保护范围

`pet/updater.py`、`pet/update_settings.py`、`plugin-roadmap-demo.html` 必须与本轮前摘要一致；既有平台查询实现、测试、探针及历史报告未被回退。未修改 README、打包规格、入口 CLI。`.scratch` 仅存验证和交接，产物不进入提交。

## 三、实现合同与安全边界

### 独立配置及临时凭据

- 持久化位置：每实例 `plugins.official.screen-understanding`；自动与手动分别绑定 profile。
- `VisionRequestConfig` 只表示一次执行；密钥不进入持久化 profile，且不出现在对象 repr。正常运行不调用聊天解析器；旧规则只由迁移服务使用。
- Vault 按功能、规范化配置路径所代表的实例、profile、端点和操作检查授权。复制另一实例的引用不能读取其凭据；移动数据目录后可能需要重新配置，不能把引用当成可跨机器搬运的秘密。
- 只使用受支持的原生 keyring 后端；缺失、后端不可用、写后读校验失败和读取失败分别返回状态，不回退明文/会话缓存/聊天 Key。
- Worker 不导入 keyring，只接收 `analyze_frame/manual_look` 单次授权参数；`config_push` 不含秘密。释放引用不等于 Python 内存物理擦零，本轮不作此保证。

### 迁移与并发保存

1. 预览分别计算旧自动/手动行为，包括 prefer-free、独立视觉开关、提示词、模型、端点和 Key 来源；没有确认就不复制 Key。
2. 确认前验证来源指纹；确认窗口默认取消。仅有效参数和秘密均一致时合并 profile，不删除聊天凭据。
3. 非敏感备份保留目标视觉命名空间与来源指纹，不复制整份含潜在旧明文的聊天配置。journal 只存阶段、引用和安全字段。
4. 操作锁防止两次视觉迁移互相干扰；配置锁内重读/合并/原子保存。keyring 操作不持有配置文件锁，锁竞争明确返回。
5. Core 普通 save 保留磁盘最新视觉命名空间；其他字段仍沿用原有语义，不声称解决全项目并发合并。
6. 恢复只清理本次创建或已退役、且未被当前配置引用的凭据；不覆盖后续独立编辑。配置损坏时拒绝覆盖并保持待配置。

### UI 与联动

- 设置位于“自动化与联动 → 屏幕理解”，无聊天模块时仍提供编辑。打开/搜索不截图、不请求模型、不读取秘密；预览和保存是显式用户操作。
- 旧聊天视觉入口改成说明与跳转；兼容草稿控件隐藏且由 Qt 持有，不形成第二套有效配置编辑器。
- 成功气泡先展示，再调用既有受控文字同步。无接收方、停用、繁忙或回调失败不影响气泡；不新增补发队列或额外模型请求。
- 配置 revision 改变会作废旧结果；手动仍发给来源窗口，共享自动识屏保持原有路由。

## 四、性能分析

命令：`python scripts/benchmark_vision_config.py`。
环境：本机 Windows、Python 3.11.1；临时配置文件、MemoryVault；**不使用原生 keyring，不截图，不请求外部网络**。这是本次新增路径的绝对基线，不是与旧路径等负载对比，也不是长期 RSS/CPU 测试。

| 路径 | 样本量 | 平均 ms | p50 ms | p95 ms |
|---|---:|---:|---:|---:|
| 待配置解析 | 500 | 0.1375 | 0.1244 | 0.1805 |
| 已配置解析 | 500 | 0.3614 | 0.3361 | 0.4728 |
| 迁移预览 | 500 | 0.3516 | 0.3087 | 0.5787 |
| 确认迁移 | 25 | 55.1150 | 56.6268 | 64.7695 |
| stale Core 保存 | 25 | 23.7316 | 23.2228 | 27.5200 |

- **稳态成本**：新增磁盘配置/revision 与按需安全存储读取，表内 ready 数字不含真实 keyring 延迟；没有证据宣称“无开销”或“更快”。自动检查原有 8 秒周期不变，未新增常驻轮询器。
- **新增路径及频率**：迁移是显式一次性/重试操作，保存由用户操作或原有 Core 保存流程触发。确认/保存含真实文件落盘，不能视为纯 CPU 计算。
- **系统资源**：增加配置文件读取、窄范围文件锁与原子保存，以及按需 keyring 调用；未为配置迁移新增线程、网络请求、截图或后台任务。实际视觉请求仍使用原有 Worker/回滚线程。
- **内存**：额外 500 次 ready 解析的 tracemalloc retained **2242 bytes**、peak **16565 bytes**，线程数增量 **0**。这只是该有界 Python 分配样本，不代表整个 Core/Worker RSS，也不证明长期无泄漏。

原始数字留在本地 `.scratch/phase4a-vision-config/benchmark-final.log`，复测使用已纳入工作树的脚本，不依赖忽略目录里的专有工具。

## 五、实机运行记录与未完成的人工验收

### 已执行的本机验证（注明替身边界）

本机真实 Python、文件系统、Qt 事件循环、子进程和 QProcess 均参与测试；OS 截图与凭据、外部模型被确定性替身或 loopback HTTP 替代。以下不是 CI 结果，**也不是用户真实屏幕/账户的端到端手测**。

- `python -m pytest -q tests/test_screen_configuration.py tests/test_screen_runtime.py tests/test_screen_settings.py`：覆盖真实双进程 stale-save/锁争用、新进程禁止导入聊天、真实设置与 PetWindow，以及取消/失败不激活。完整结果计入下节最终全量。
- `tests/test_proactive_worker_source.py` 中真实子进程禁止加载 `pet.chat`、keyring 和 Qt；确定性截图与本地 HTTP 返回，验证 Worker 请求链不靠聊天模块完成。
- Qt offscreen 探针：`python .scratch/phase4a-vision-config/ui_probe.py`，PySide6 6.11.1；720/1100 宽、浅/深主题、720 宽 1.3 字体倍率，共 25 张**仅合成设置组件**渲染图。检查字段、凭据、状态、保存及失败态，无横向溢出，安全存储失败不激活。
- 探针初次 font database 缺中文字形；仅在探针中只读加载系统 `msyh.ttf` 后重采样并检查可读图。没有安装字体或修改系统/产品字体设置。

### 不得冒充已通过的项目

本轮禁止读取用户真实 Key、截取实际屏幕和调用收费模型，因此没有运行真实迁移或真实视觉请求。offscreen 渲染不等于用户可见桌面操作。此前用户“手动看看屏幕正常”属于本步之前的版本，**不能自动继承为本步通过**。

| 人工/平台项目 | 状态及原因 |
|---|---|
| Windows 原生 keyring 保存、复制旧凭据、读回 | 未验收；只测试了替身的成功/失败与原生后端拒绝规则，需用户显式配置验证 |
| 本轮设置实际点击、系统缩放及屏幕阅读器 | 未验收；仅真实 Qt offscreen/键盘/布局检查 |
| 本轮手动识屏及聊天追问 | 待用户验证；不使用历史手测替代 |
| 自动识屏 | 仍未验收；用户此前未继续等待，不判定为缺陷 |
| 托盘自然退出 | 本轮未人工验证，保留既有自动化退出覆盖 |
| macOS/Linux、冻结构建、最小 Core 包 | 本轮未运行，不外推 Windows 源码结果 |

## 六、测试与验证

### 红绿回归与失败分类

新增模型/确认门/运行独立/损坏配置/并发恢复测试先失败后修复；原始过程日志留于 `.scratch/phase4a-vision-config/`。第一次全量为 **10 failed, 3214 passed, 11 skipped, 14 warnings（294.29s）**，没有删掉失败断言来声称全绿：

- **本轮引入问题**：window.py 超架构行数预算，抽出结果展示 helper 而不是抬高预算；无聊天回调保护、隐藏 Qt 控件生命周期及非识屏功能原有 gate 修复。
- **旧测试合同需随已批准行为调整**：旧聊天视觉分组、prefer-free 运行读取、Provider fixture 改为独立 profile 和迁移来源；保留其有效行为断言。
- **测试环境问题**：Darwin 模拟原先改全局 `sys.platform`，导致本机文件锁错误；改为只替换被测模块视图，不影响真实操作系统后端。
- **未复现的一次退出异常**：Phase 3A 退出探针首次出现 62097，单独复验通过（6.49s），最终全量也通过；未放宽断言或改 Worker 生产逻辑，保留为时序/环境观察而非证明根因已修复。

失败相关组合复验：**156 passed, 1 skipped, 3 warnings（24.00s）**。最终全量：

```text
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
3224 passed, 11 skipped, 14 warnings in 300.50s
```

11 个 skip 与此前套件一致；14 个 warnings 为既有 `QImage.mirrored` / `QHoverEvent` 弃用警告，没有新增静默忽略。全量发生在新增本报告之前，报告纪律按文件参数化会随报告增加，不能把之后文档测试数量冒充成重新全量运行。

### 验收矩阵

| 行为 | 证据/结论 |
|---|---|
| 自动/手动旧有效行为和 Key 选择 | 迁移参数化、缺失独立 Key 不借聊天 Key、确认后聊天改变不影响独立配置 |
| 未确认、取消、部分待配置 | 无凭据复制，无相应任务；另一已绑定模式可用 |
| 安全存储、作用域和秘密边界 | 后端不可用/读写失败/不安全后端拒绝；跨实例/profile/endpoint/operation 拒绝；配置/日志/快照无测试 Key |
| 恢复、来源变化和损坏文件 | 提交失败保留旧配置，仅清理本次未引用秘密；来源变化需刷新；不覆盖后续编辑 |
| 设置进程/Core 并发 | 真实双进程 stale save 和非阻塞锁回归通过 |
| 无聊天运行 | 新进程导入阻断、真实 Qt 与 Worker、假截图/本地 HTTP 测试通过 |
| 可选聊天文字联动 | 既有会话与共享窗口回归保持；回调缺失/失败不阻止气泡，旧 revision 不展示 |
| 生命周期与平台查询 | Worker、fallback、generation、共享单 Worker、查询层相关套件纳入最终全量 |
| UI | 独立分组和深链、无聊天保存、失败待配置、窄宽/主题/键盘确定性检查通过；人工保留 |

静态门：`python -m mypy pet/credentials.py pet/config_transaction.py pet/http_compat.py pet/screen_understanding pet/workers/proactive_screen_adapter.py pet/workers/proactive_screen_worker.py` 为 **11 个文件无错误**。最终 Ruff、文档链接、报告纪律与保护摘要在本报告生成后复验；结果追加至末节，不将未运行的门标绿。

没有重跑长期 soak、真实模型、安装包构建或推送前满载三遍；本轮不推送，不能据此声称发布门通过。

## 七、风险、回滚和下一步

- 保存/迁移失败会维持待配置并显示原因；不会改用旧聊天 Key 或明文。用户应先在“自动化与联动 → 屏幕理解”预览确认，或手动填写独立 profile。
- 迁移不删除旧聊天字段/凭据。回滚代码时须保留独立命名空间及安全存储，不直接删除用户后续数据；回退旧程序可能重新使用旧聊天关联配置，应明确告知用户。
- `in_process` 仅是当前开发回滚通道，同样经过独立配置授权；尚未交付外部功能包，不把 fallback 当成卸载后隐藏恢复能力。
- 没有本轮 Git 提交，不能运行整树 reset/revert 来回滚；后续只暂存本轮增量形成独立提交。已有平台查询与文档变更必须保留。
- 下一步仍是菜单/设置贡献合同落地，再推进 host/Worker 独立构建及无包启动；本次不提前新增安装/卸载体验。

## 八、收尾复验

2026-09-28 收尾命令实际结果（全量之后只补文档，没有继续修改运行时代码）：

| 命令/检查 | 实际结果 |
|---|---|
| `python -m ruff check pet tests scripts` | All checks passed |
| `python -m ruff format --check pet tests scripts` | 392 files already formatted |
| 上述 mypy 目标 | 11 个 source files 无错误 |
| `python scripts/check_docs.py` | 108 份 Markdown 链接检查通过 |
| `python -m pytest -q tests/test_pr_report_discipline.py tests/test_desktop_pet_features.py` | 134 passed，12.42s |
| `git diff --check` | exit 0；Git 提示已有文件 LF/CRLF 转换，不是空白检查错误 |
| HEAD / 暂存区 | HEAD 仍为基线提交；暂存区为空，没有提交或推送 |
| 保护文件 | updater、update_settings、演示 HTML、打包配置、CLI 与 Worker 协议文件无本轮差异 |

上述 134 项包含本报告加入后的纪律检查，不是再次执行全量。全量结论仍为前述 **3224 passed / 11 skipped / 14 warnings**。本轮验证没有调用真实 keyring、截图用户屏幕或请求外部模型；真实凭据迁移、可见设置交互、自动识屏和托盘退出不能由这些自动化结果代替人工验收。
