# 简易 API 设置与鲸鱼图标恢复：实施与交付报告（2026-10-09）

> **分支/基线**：`codex/phase3-worker` / `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`。开始时已有 T/R/U 累积工作树，保留全部既有修改；本轮无提交、推送或正式发布。
> **版本**：Core 4.2.3 / AI DLC 1.0.3 / Screen DLC 1.0.2。Core 是既有 `manual-acceptance-only` 路线的候选，不冒充正式签名发布版。
> **状态（2026-10-10 收尾更新）**：实现、全量、三轮高负载、Ruff/格式、重建、隔离冻结程序与报告纪律验证已通过。真实 Provider/真实屏幕留用户验收，Phase5A 不提前关闭。

关联：[设计](plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md) · [PLAN](../.scratch/phase5a-local-distribution/PLAN.md) · [STATUS](../.scratch/phase5a-local-distribution/STATUS.md) · [HANDOFF](../.scratch/phase5a-local-distribution/HANDOFF.md) · [WORKLOG](../.scratch/phase5a-local-distribution/WORKLOG.md) · [SUMMARY](../.scratch/phase5a-local-distribution/SUMMARY.md) · [已验收 Setup 合同](PR-REPORT-SETUP-PROJECT-DIRECTORY-2026-10-09.md) · [索引](INDEX.md)。

## 一、核心特性与范围

1. 恢复旧式“模型与连接”：API 列表、地址、模型、主 Key、测试连接；仅增加视觉 Key（可选）。视觉地址/模型默认折叠。内部 ID/用途授权不出现在表单。
2. 测试使用当前输入，但不会保存；保存/完成成功后才发布。主配置自动供聊天、文件解读、余额使用；新请求读取最新提交，不要求重启已打开窗口。
3. 手动/自动识屏使用专用视觉 Key；没有时尝试主服务和主 Key，沿用旧模型推导。高级项填另一家地址但没有视觉 Key 时仍只请求主服务，不把主 Key 转发。
4. 识屏鉴权/模型不支持时提示配置或检查视觉 Key；网络、限流、截图、Worker 启动错误不混淆。不自动启用识屏、不扫描模型或启动时额外探测。
5. 保留安全存储、已有配置/旧密钥、请求快照与 DLC/Worker 分层，不回滚成 Core 执行 AI，不恢复官方 owner/factory 特判。
6. 附加恢复用户指定的原鲸鱼图标。`assets/icon.ico` 未改图像，Core 两条构建路径与 Setup 嵌入同一个输入；安装/卸载行为不改。

## 二、修改文件说明

### 代码、测试与构建（本轮增量）

下表以 A 开始时保存的 `a01-simple-api-20261009/baseline/` 为基线，逐文件执行 `git diff --no-index --numstat <before> <current>`，而不是把整个 T/R/U 未提交 diff 归于本轮。共 **39 个文件，新增 2 个、删除 0 个**；机器清单为同目录 `file-changes.json`。

| 文件 | 增删行数 | 改了什么与原因 |
|---|---|---|
| `features/ai_chat/host/chat/ai_settings_page.py` | +13 / −7 | 中央接口存在时只展示统一设置跳转，保留提示词等业务行，避免第二份可编辑地址/Key。 |
| `features/ai_chat/host/chat/request_config.py` | +4 / −4 | 新请求从统一主配置解析；把缺配置提示改为普通用户可理解的设置入口。 |
| `features/ai_chat/host/chat/service.py` | +2 / −2 | 保留请求授权/快照与取消边界，将错误提示去内部用途授权术语。 |
| `features/ai_chat/host/chat/settings_dialog.py` | +4 / −6 | 经典设置隐藏重复连接编辑，复用统一表单而不回滚业务配置。 |
| `features/ai_chat/host/config.py` | +1 / −1 | 聊天配置缺失提示指向主 API Key，不让用户填写服务 ID。 |
| `features/ai_chat/host/contribution_settings.py` | +3 / −1 | 保留业务行并排除识屏贡献拥有的 vision_migration 跳转行，修复缺少/卸载识屏时 AI 草稿访问已销毁控件。 |
| `features/screen_understanding/common/models.py` | +28 / −1 | 共享旧模型推导与按凭据来源的错误提示；请求快照携带 main/vision 来源枚举，默认序列化仍无 Key。 |
| `features/screen_understanding/host/config.py` | +8 / −3 | 手动/自动统一解析专用视觉或主配置 fallback，沿用模型推导，不增加探测。 |
| `features/screen_understanding/host/manual.py` | +3 / −3 | 未配置和识屏失败提供可操作的简易设置指引，保持手动请求生命周期。 |
| `features/screen_understanding/host/settings.py` | +37 / −33 | 中央模式仅创建业务行和统一设置入口；避免构造后遗弃旧 API 行导致 Qt 子控件提前释放。 |
| `features/screen_understanding/worker/runtime.py` | +6 / −1 | 将鉴权/模型错误按真实使用主 Key 或视觉 Key 生成提示，保留其他错误类别。 |
| `features/screen_understanding/worker/vision.py` | +14 / −12 | 明确 HTTP 鉴权、限流、视觉协议/模型、网络错误分类，避免所有失败归咎于 Key。 |
| `packaging/core_webm.iss` | +1 / −0 | 仅添加 SetupIconFile 关联原 assets/icon.ico；其余安装/卸载字节与 A 前快照一致。 |
| `pet/__init__.py` | +1 / −1 | 候选 Core 递增到 4.2.3，避免复用已验收 4.2.2 内容。 |
| `pet/api_config.py` | +14 / −2 | 已有请求端口内部适配 simple 主/视觉选择和有效版本，仍逐请求检查执行授权与配置版本。 |
| `pet/api_ports.py` | +2 / −0 | 扩展不可变快照的凭据来源/模型 fallback 字段，不开放密钥枚举。 |
| `pet/api_probe.py` | +2 / −1 | 连接测试复用既有 chat endpoint 规范化，避免 /v1 或完整路径重复拼接。 |
| `pet/balance.py` | +2 / −2 | 将不支持的响应/协议明确归类，不再误报未配置 Key。 |
| `pet/balance_config.py` | +4 / −4 | 余额读取统一主 API，保留 DeepSeek 适配器和最新配置解析。 |
| `pet/modern_settings_dialog.py` | +13 / −0 | 完成、关闭和 Esc 等退出路径等待简易 API 保存成功后再关闭；失败保留草稿。 |
| `pet/settings_api.py` | +198 / −276 | 改为 API 列表/名称/地址/模型/主 Key/可选视觉 Key；高级项折叠，测试不提交，保存异步且保护草稿。 |
| `pet/settings_balance.py` | +3 / −3 | 余额设置跳转统一模型与连接页，不维护第二份凭据。 |
| `pet/simple_api.py`（新增） | +223 / −0 | 新增旧交互到现有安全配置端口的适配：内部生成 ID、主/视觉选择、旧配置预填、CAS/journal/vault 原子保存。 |
| `scripts/build_feature_release.py` | +3 / −1 | 生产 Core 构建复制并嵌入原 ICO；打包失败不静默退回默认图标。 |
| `scripts/build_screen_delivery.py` | +9 / −9 | 候选 Core 构建同样嵌入 ICO；更新两个 DLC 版本及最低 Core，支持独立新输出目录。 |
| `scripts/feature_release_materials.py` | +2 / −2 | 发布材料版本与本次 Core/DLC 最低版本一致。 |
| `scripts/validate_phase5a_delivery.py` | +1 / −1 | 交付校验接受本次明确的新包版本，旧产物不冒充新候选。 |
| `tests/test_build_feature_release.py` | +9 / −0 | 验证生产 Core spec、图标输入和冻结资源合同。 |
| `tests/test_chat_service.py` | +3 / −6 | 结果返回不等于 QThread.finished 已处理；使用已有真实 drain 等待替代猜测时序，不改产品行为。 |
| `tests/test_core_api_consumers.py` | +18 / −8 | 补统一简易配置下多个已打开聊天入口的新请求、凭据切换和失败输入保护覆盖。 |
| `tests/test_core_api_layout.py` | +2 / −1 | 设置布局断言适配简易入口，保留无 DLC 可配置合同。 |
| `tests/test_core_api_migration_ui.py` | +38 / −24 | 更新旧 ID/用途 UI 断言为简易表单、测试与保存分离等新合同，保留底层迁移安全测试。 |
| `tests/test_feature_manual_acceptance.py` | +2 / −0 | 最小构建夹具包含真实 ICO，符合新增冻结构建输入合同。 |
| `tests/test_feature_package_probe.py` | +3 / −3 | 适配新包最低 Core 的探针版本夹具，不放宽兼容校验。 |
| `tests/test_phase5a_repair_regressions.py` | +6 / −6 | 按新候选版本调整历史 R 回归的夹具。 |
| `tests/test_phase5a_setup.py` | +6 / −0 | 断言 Setup 仅图标新增并确实关联原 ICO。 |
| `tests/test_phase5a_t01_regressions.py` | +1 / −1 | T 历史未知 owner 回归采用匹配新包的 Core 测试版本。 |
| `tests/test_screen_delivery_build.py` | +15 / −0 | 补两条 Core 构建路径图标闭合输入与版本断言。 |
| `tests/test_simple_api_settings.py`（新增） | +365 / −0 | 新增主/视觉自动消费、安全地址边界、清除、错误分类、草稿/关闭、模型推导，以及真实 Qt AI-only/Screen-only/双 DLC 控件生命期回归。 |

### 文档与持续记录（Git 累计，非 A 独有增量）

下表执行 `git diff --numstat -- <path>`；未跟踪文档使用 `git diff --no-index --numstat NUL <path>`。设计和阶段文件在 A 前已有 T/R/U 内容，所以这些数值是相对 HEAD 的**累计工作树差异**，不得全部归于本轮 API 改动；无文档删除。完整命令另存 `documentation-numstat.json`。

| 文档 | 累计增删 | 本轮实际意图 |
|---|---|---|
| `docs/PR-REPORT-SIMPLE-API-2026-10-09.md` | +212 / −0 | 新增：逐文件、性能、真实冻结/GUI、失败分类、最终候选与人工步骤。 |
| `docs/INDEX.md` | +13 / −5 | 登记本报告入口，便于交接查找。 |
| `docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md` | +206 / −13 | 同一设计加入简易 API/图标合同与A完成证据，保留旧合同历史。 |
| `.scratch/phase5a-local-distribution/PLAN.md` | +512 / −0 | 同一计划更新A01–A05完成门，不重建阶段副本。 |
| `.scratch/phase5a-local-distribution/HANDOFF.md` | +589 / −0 | 准确最终交付路径、验证与用户确认断点。 |
| `.scratch/phase5a-local-distribution/STATUS.md` | +547 / −0 | 区分工程完成和用户/Phase5A未关闭，保留历史。 |
| `.scratch/phase5a-local-distribution/WORKLOG.md` | +626 / −0 | 追加跨日执行、失败原因/重跑与重建记录。 |
| `.scratch/phase5a-local-distribution/SUMMARY.md` | +467 / −0 | 跨对话摘要统一当前A版本、限制和下一步。 |

### 明确未改变

- Setup 的路径选择、portable 项目布局、可选 DLC、卸载删除/保留 data、关联清理 best-effort 逻辑不变。与 A 前 `.iss` 快照比较仅增加 `SetupIconFile=..\assets\icon.ico`。
- 原 `assets/icon.ico`、已验收 U06 Setup 字节不变。无系统安装器注册表/快捷方式改写，无代理/VPN 策略更改。
- 未自动清理旧配置、密钥、DLC、外部 ZIP 或 staging。旧 DLC 接口保留，不自动启用功能。

## 三、实现要点与边界

- `SimpleApiConfiguration` 是现有配置端口的内部选择适配，不是第二套请求执行器。UI 自动生成配置身份，CAS 校验版本，沿用操作锁/journal/系统 vault；地址变化不复用旧凭据，vault 失败不写明文。旧配置优先聊天绑定、手动视觉绑定，其次自动视觉；打开页面只读元数据，不取出真实 Key。
- 保存后的选择映射到现有 `FeatureHostContext.api`。请求快照继续受 DLC 执行状态、有效版本和撤销校验；普通修改不改在飞请求，配置通知不覆盖未保存草稿。
- 无专用视觉 Key 时使用主地址，即便高级视觉地址不同也不会发送到它。若需要独立服务，输入它自己的视觉 Key；清除视觉 Key 则回主配置。余额仍只有现有 DeepSeek 协议适配，不宣称任何服务都支持查询。
- Qt 对象由其实际贡献页拥有。冻结验收发现“创建旧 API 行后丢弃列表”和“AI 草稿持有 Screen 行”两处 C++ 控件提前释放，已用真实 Qt 重建/DeferredDelete 回归修复，不能仅靠窗口可见判断通过。

## 四、性能分析

### 方法与环境

Windows build 26100，Python 3.11.1，PySide6 6.11.1；20 个可用逻辑 CPU。原生 GUI 使用 Windows Qt 插件；自动化使用 offscreen。配置和测试凭据为自有目录/生成值，vault 用内存后端；**以下请求解析数字不含 Windows 安全存储和真实 Provider 延迟**。GUI/首轮解析测量与全量并发；交错对比在自有高负载进程退出后于 2026-10-10 复测，保存为 performance-comparison-final.json。不是专用空闲机器基准。

可复现命令（工作区根，PowerShell）：

```powershell
$py = 'E:\Program Files (x86)\Dev-Cpp\python.exe'
& $py -X utf8 .scratch/phase5a-local-distribution/a01-simple-api-20261009/verify_gui_perf.py
& $py -X utf8 .scratch/phase5a-local-distribution/a01-simple-api-20261009/benchmark_compare.py
& $py -X utf8 .scratch/phase5a-local-distribution/a01-simple-api-20261009/run_frozen_delivery.py
```

这些是生成证据脚本，不纳入提交；最后一条首次运行要求独立不存在的目标目录，复跑先改为另一个自有新目录，不能覆盖用户安装。

| 路径 | 样本 | 中位数 / P95 | 说明 |
|---|---:|---:|---|
| API 元数据 | 500 | 0.2582 / 0.3355 ms | 不读取 Key |
| 主请求配置解析 | 500 | 0.7256 / 0.8954 ms | 内存 vault、实际配置磁盘版本检查 |
| 视觉请求配置解析 | 500 | 0.70145 / 0.8535 ms | 无视觉 Key 的 fallback |
| 表单元数据读取 | 500 | 0.20695 / 0.2861 ms | 不是整个窗口的启动时间 |
| 同引擎旧绑定 vs 简易选择 | 各 500、交错采样 | 0.83525 vs 0.84775 ms | 中位差 +0.0125 ms；不是重构前二进制基准；P95 1.1988/1.2681 ms |
| 1000 次解析 Python 内存 | 1000 | 保留差 233 B，峰值 55,517 B | tracemalloc；线程 1→1，不代表全进程 RSS |

逐条回答：

1. **稳态成本**：简易选择与同引擎旧绑定路径的测量差为 +0.0125 ms/新请求；未引入常驻轮询线程。原有配置通知/版本检查继续使用，不将这些既有成本称为新增。
2. **新增路径/频率**：主/视觉选择仅在请求解析时执行；高级 UI 仅打开/操作设置时使用。Key 测试与保存采用既有受退出门约束的短生命周期任务，不在启动自动请求 Provider。
3. **系统/磁盘/网络/线程**：版本读取和保存时的 journal/vault 是既有安全流程；新增选择字段随一次提交落盘。未引入后台模型扫描、网络探针或另一份密钥数据库。实际网络只由显式测试或原有业务请求触发。
4. **内存**：1000 次解析 retained +233 B，不可推导无所有类型的泄漏；原生冻结窗口 n=10×0.5s 观察如下。并发负载和 DLL/Qt 缓存使它不是长期稳定性测试。

| 冻结窗口 | 首次打开 | RSS 范围 | 线程 | 进程 CPU 中位数 |
|---|---:|---:|---:|---:|
| 无 DLC 的统一 API 页 | 4.562 s | 145,567,744–145,600,512 B | 14 | 0.0% |
| 安装两个 DLC 后的 API 页 | 3.735 s | 157,511,680–157,540,352 B | 16 | 3.1% |

冻结两个窗口均自然退出 code 0。新增 `_a01b` 总生成量 **3,447,898,528 B**，在约 5 GiB 新构建预算内；不为腾空间删除旧已验收产物。

## 五、实机运行记录

### 失败与根因证据

原始证据目录：`.scratch/phase5a-local-distribution/a01-simple-api-20261009/`。

- 初始简易 API red：10 failed；连接 URL 规范化 red：2 failed；父窗口退出/保存、坏配置重载、图标回归均保留各自 red 日志。
- 初次冻结设置可打开但日志有 `RuntimeError: Signal source has been deleted`，旧识屏编辑行被回收；保留 `frozen-smoke.json`，明确 `complete:false`，不能当通过。
- 随后加入 AI-only、Screen-only、两者共存的真实 Qt reparent/DeferredDelete 回归：`reparent-red.log` **3 failed**。修复后 `reparent-green.log` **60 passed in 23.93s**（包含相关聊天/屏幕/构建测试）。
- 两次中间全量有失败，分类见下一节；均保留原日志，不替代最终固定源码门。

### 原生 UI 与最终冻结程序

1. `verify_gui_perf.py` 在真实 Windows Qt 窗口生成明/暗主题 × 720/1100 logical px 的 8 张自有控件截图，720 默认折叠、1100 展开高级项；水平滚动最大值全部 0，底部保存入口可达。显示缩放 125%，截图物理宽度与 logical 宽度不同是正常 DPI 行为。
2. `run_frozen_delivery.py` 在 `_a01b/runtime-delivery` 放置自有 portable marker，仅使用新 Core、副本数据和生成配置。无 DLC 打开 API 页 code 0。
3. 真实冻结命令 `dsh-pet-core-webm.exe --core-maintenance install-packages <packages-delivery> official.ai-chat official.screen-understanding` **code 0，13.125s**，两个新版本落入该目录 `data/plugins`。
4. 再开冻结 API 页，两个贡献正确加载，日志无 ERROR/Traceback，自然退出 code 0。只 PrintWindow 采集自有窗口；`delivery-both-dlc-api.png` 已查看，版本 4.2.3 和简易表单可见。
5. 生产 Worker 新构建的真实进程边界：HELLO 805ms、READY 829.411ms、租约转移成功、自然退出 code 0、总 867.431ms；0 次屏幕捕获、0 次 Provider 请求。来源 `_a01b/w/evidence/normal-startup/startup.json`。
6. PE 资源检查将原 standalone EXE、最终 Core、最终 Setup 与未修改的 `assets/icon.ico` 对比：10 个尺寸 16/20/24/32/40/48/64/96/128/256 的帧逐字节一致；Core group 1、Setup MAINICON 均匹配，证据 `icons-delivery.json`。

### 未执行的外部/真实数据边界

本轮没有重新执行系统 Setup 安装/卸载（会写真实系统产品注册/快捷方式），而是继承用户已确认的 U 行为、证明 `.iss` 仅图标行变化，并运行实际冻结 Core 安装 DLC。**不能写成本次新 Setup 已再次完成系统安装/卸载。** 编译日志核对实际输入的两个 ZIP/portable marker，生成物哈希与清单校验；本轮未从最终 Setup 反向解包再逐字节核对 ZIP。

没有读取真实用户 Key、没有收费请求、没有捕获真实屏幕。冻结数据根的 `provider_requests=0`、`user_keys_read=false` 是明确探针结果；因此不能判断用户服务是否支持视觉或余额。正常 Worker HELLO/READY 也不是“真实识屏成功”的证明。

## 六、测试与验证

命令使用上述 `$py`，默认工作区根。日志都在 A 证据目录。

| 门 | 命令/证据 | 结果 |
|---|---|---|
| 初始相关 | `related1.log` | 299 passed, 1 skipped in 70.32s |
| 最终 Qt 生命周期相关 | `reparent-green.log` | 60 passed in 23.93s |
| 最终全量 | `run_delivery_full.py` → `python -X utf8 -m pytest -q` | **4472 passed, 15 skipped, 15 warnings in 611.95s**；exit 0，641 输入 `source_unchanged=true` |
| 受影响族高负载 | `run_delivery_highload_v2.py` | 三轮均 **185 passed**，25.81 / 25.29 / 25.87s；CPU 中位 **99.9% / 100.0% / 99.1%**；20 自有逐核负载进程各轮均自然退出，源码未变化 |
| Ruff / 格式 / diff | `python -m ruff check pet features scripts tests`；改动文件 `ruff format --check`；`git diff --check` | Ruff 0.16.6 全通过，38 Python 格式通过，diff exit 0；CRLF 提示非失败 |
| 报告纪律 / 文档链接 | `tests/test_pr_report_discipline.py` 与记录链接检查 | 报告纪律 **63 passed in 2.01s**；链接最终核对见 closeout 收据 |
| 源码/产物一致性 | `audit_artifacts.py` | Core 230 sources/1010 resources；Worker 输入；AI 29 files（27 源码对根）、Screen 118 files（21 源码对根）及 manifest SHA-256 全通过 |

失败分类（不隐藏）：

- 第一轮全量 3 failed / 4463 passed / 15 skipped：最低 Core 新版本的历史夹具未同步；过程中又加入用户图标请求，不是最终固定源码。
- 第二轮全量 2 failed / 4467 passed / 15 skipped：最小构建夹具缺新增 ICO；聊天测试把结果信号误当线程 finished。分别补夹具与真实 drain 等待，不放宽产品门。之后新增冻结 Qt 回归修复，并重新完整全量得到上表绿灯。
- 高负载初次执行的 runner 错写 `tests/test_files.py`，pytest exit 4/no tests ran；20 个自有负载进程均自然退出。修复 runner 清单为真实 `test_file_interpret.py`、`test_file_eater.py`，另存 v2 证据，不覆盖失败日志。
- 最后静态 runner 给 Ruff 同样设置了隔离 APPDATA，导致 Python 用户站点不可见（No module named ruff），不是代码 lint 失败；改为工具原环境运行 Ruff 0.16.6，check/format 均通过，pytest 仍保持隔离数据目录。
- 15 skipped 按本机/平台测试条件；15 warnings 是既有 Qt deprecated 接口与故意重复 ZIP 项目警告，不声称已消除。没有把 skipped 当作通过。

## 七、交付产物与操作顺序

### 当前唯一候选

| 产物 | 相对工作区路径 | SHA-256 |
|---|---|---|
| Setup 4.2.3 | `_a01b/setup-delivery/dsh-pet-core-webm-setup.exe` | `fd27589902adca15900c6e02b9678832d0d1e70f00072dc2eb64730c0bb129d8` |
| Core EXE 4.2.3 | `_a01b/c-icon/dist/dsh-pet-core-webm/dsh-pet-core-webm.exe` | `14b50e4c5a50ba8b2b33efbc211effc231de6dbd11585895903e9bf0bb8b1207` |
| AI 1.0.3 | `_a01b/packages-delivery/official.ai-chat.zip` | `e73df14c825bb22594bfd2495cc012aa2e5bff1bde0fa9ed91ed94d75d775fb6` |
| Screen 1.0.2 | `_a01b/packages-delivery/official.screen-understanding.zip` | `c9dc18dcfbb41d16e68abc3b2e62d4b8da1f1d0f38c1abb2bc54afb66e62bb79` |
| Worker | `_a01b/w/dist/proactive-screen-worker/proactive-screen-worker.exe` | `7c233d37b3e6dd5ad25007963b6faee91a2635d147a4ae387584828a93602320` |

Core EXE 必须与同目录完整 onedir 一起使用，不能单拷一个 EXE。Setup 包含正确 portable marker 和两个选装 ZIP。`_a01b/setup`、`setup-final`、`packages` 是中间候选，不交付；本表指向 delivery。已验收 U06 Setup SHA-256 仍为 `7cecfde7854fa70cbfa98641457b2ade8c8a547887254ff609204ad1815f6e22`。

### 用户一次完成的验收顺序

1. 自然退出旧桌宠/设置，运行上表新 Setup，选现有合法项目路径；**勾选 AI 和识屏选装，才能把已有 DLC 更新到本次版本**。检查 Setup、安装后 EXE 的鲸鱼图标。安装卸载逻辑与已验收版本相同。
2. 打开一个聊天窗口并保留，再进入“AI 与对话 → 模型与连接”。已有 Key 应显示已配置；没有则填主 Key。测试成功提示当前输入已联通但未保存，点击保存/完成。回原窗口直接发送；一并测试文件解读、余额。不支持余额的服务应提示不支持，而不是另填 Key/用途。
3. 视觉 Key 留空、自动识屏关闭时执行“看看屏幕”。如果主服务支持所推导视觉模型，应返回识别；否则鉴权/模型错误提示补视觉 Key，不把 Worker/网络错误说成 Key 未填。
4. 需要时填视觉 Key，高级项填该服务地址/模型，保存后重试；清除视觉 Key 保存后再次走主配置。普通设置刷新不应误取消手动请求。
5. 可选失败路径：错误 Key、网络不可用、限流应分别提示；修改后新请求可重试，不依赖重启。未保存编辑不能被其他窗口的配置通知覆盖。

上述真实 Provider、真实屏幕、系统安装体验由用户最终确认，暂不关闭 Phase5A。

## 八、风险与回滚

本轮添加 API namespace 内部 `simple` 选择字段，但不删除旧 services、grants 或密钥。只回退旧 EXE 可能重新读旧绑定，**不应承诺旧程序会沿用新选择**。回退前备份项目 data（不要导出明文 Key），优先保留已验收 U06 整套候选，并由用户明确决定恢复哪个配置版本；禁止整树覆盖既有工作或自动删凭据。对现工作树只按 A 基线逐文件定点回退，不做 `reset --hard`。

## 九、本次实际可体验的效果与限制

填主 Key、测试、保存即可用聊天和余额，无须 ID 或用途勾选；视觉先尝试主配置，确需另一服务再填视觉 Key。两个 EXE 的原鲸鱼图标恢复，已验收 Setup 安装/卸载步骤不变。真实服务可用性受其模型/协议限制，本地假 Key/Worker 握手不能代替人工确认；工程验证完成也不自动等于 Phase5A 用户收尾。
