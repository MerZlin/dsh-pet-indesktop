# Phase 4A 第三步：屏幕理解菜单与设置贡献

日期：2026-09-28。当前为本地未提交实施记录，不是已经创建或合并的 PR。

导航：[文档索引](INDEX.md) · [Phase 4A 设计](plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md) · [上一切片](PR-REPORT-VISION-CONFIG-2026-09-27.md)

## 范围与结论

本步实现官方屏幕理解的 owner 贡献、菜单/设置注册撤销、实例命令和执行授权，不新增托盘项、整包停用按钮或安装器。宿主状态只在内存中；不等于功能包已独立构建、可安装或物理卸载。聊天文字同步继续使用现有受控回调，通用服务发现另行实施。

保留前序尚未提交的平台查询、独立配置和凭据变更。本报告的文件统计以本任务开始快照为基线，不把工作树对 HEAD 的累积差异误写成本次改动。没有提交、推送或重建。

## 修改文件说明

统计方式：对本任务开始快照与当前文件执行 `git diff --no-index --numstat`；新增文件与空文件比较。快照位于 `.scratch/phase4a-contributions/baseline/`，只作本地证据、不作为发布依赖。共 28 个文件、无删除/移动；下表不是整个脏工作树对 HEAD 的累积统计。

| 文件 | 本轮 + / − | 改了什么、为什么 |
|---|---:|---|
| `LOG-INDEX.md` | +1 / −0 | 为本轮日志增加主题索引。 |
| `LOG.md` | +9 / −0 | 追加本轮执行、首轮失败、修复及实际验证，保留历史记录。 |
| `docs/INDEX.md` | +3 / −2 | 登记实施报告并同步当前设计状态。 |
| `docs/PR-REPORT-SCREEN-CONTRIBUTIONS-2026-09-28.md`（新增） | +93 / −0 | 新增本步逐文件、失败与修复、性能和验收限制证据。 |
| `docs/plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md` | +19 / −6 | 登记贡献合同与实现边界，明确未做通用聊天服务、外置 host 与独立构建。 |
| `docs/plugin-phase-04-updates/README.md` | +2 / −2 | 更新第三切片状态与后续顺序，不将内部贡献冒充可卸载 DLC。 |
| `pet/app.py` | +5 / −0 | 向窗口和设置共享宿主，不改变 Core 更新或 Worker 协议。 |
| `pet/context_menus/registry.py` | +3 / −2 | 菜单可用性从当前注册身份取得，保留原菜单 ID。 |
| `pet/context_menus/shared.py` | +6 / −30 | 现代/legacy 共用识屏贡献渲染，不再重复硬编码识屏菜单。 |
| `pet/feature_bindings.py`（新增） | +96 / −0 | Core 菜单适配、实例命令、窗口销毁解绑及打开菜单的撤销处理；不内嵌功能 UI。 |
| `pet/modern_settings_dialog.py` | +184 / −265 | 挂载功能组件，拥有搜索/深链/保存处理生命周期；移除确认与故障只读草稿，销毁时清理。 |
| `pet/multi_window_shared.py` | +4 / −0 | 共享代理接入同一宿主，进程级停止释放执行租约，单窗关闭不误停其他窗口。 |
| `pet/official_features.py`（新增） | +13 / −0 | 集中官方 allowlist 和惰性描述工厂，不导入功能 UI 或执行任意入口。 |
| `pet/plugins/contributions.py`（新增） | +213 / −0 | 新增 owner/scope 贡献、能力和线程校验、独立注册句柄及批次回滚；错误不会清除其他批次。 |
| `pet/plugins/feature_host.py`（新增） | +200 / −0 | 新增显式官方功能宿主、内存状态、窗口绑定及独立执行租约；先禁止执行再撤销 UI。 |
| `pet/plugins/ports.py` | +23 / −7 | 命令句柄加入注册身份与受控调用；旧句柄不能删除或调用同名新命令，既有节日接口保持。 |
| `pet/plugins/runtime.py` | +7 / −0 | PluginContext 获得绑定贡献端口，dispose 统一清理贡献和原有服务。 |
| `pet/proactive.py` | +19 / −0 | 检查整包执行授权、共享运行租约和晚到同步结果；停用取消既有执行。 |
| `pet/screen_understanding/contribution_settings.py`（新增） | +103 / −0 | 统一设置组件、非敏感草稿、显式视觉保存、策略应用及幂等释放。 |
| `pet/screen_understanding/contributions.py`（新增） | +61 / −0 | 屏幕理解拥有菜单和设置工厂，保留稳定 ID、文案、平台限制和既有布局。 |
| `pet/screen_understanding/strategy_settings.py`（新增） | +276 / −0 | 将原自动识屏策略控件、查询和保存从设置宿主移入功能组件，不改变策略字段。 |
| `pet/screen_understanding/window_actions.py`（新增） | +65 / −0 | 机械提取手动执行 facade 的实现，守住既有 window.py 行数门；仍属后续受限 host 接口的兼容接缝。 |
| `pet/settings_menu_layout_editor.py` | +1 / −1 | 区分空可用集合和未传可用集合；隐藏入口不抹掉原布局偏好。 |
| `pet/window.py` | +47 / −48 | 兼容入口授权、请求代数、晚到结果拦截、窗口关闭解绑；手动实现提取而非放宽行数限制。 |
| `scripts/benchmark_contributions.py`（新增） | +154 / −0 | 新增可复用有界测量脚本，阻断执行/密钥依赖，统计耗时和残留对象；不显示用户窗口。 |
| `tests/test_plugin_contributions.py`（新增） | +183 / −0 | 新增事务、归属、旧句柄、平台、能力和线程边界回归。 |
| `tests/test_screen_contributions.py`（新增） | +417 / −0 | 新增真实 Qt 菜单/窗口/设置、共享生命周期、撤销草稿和独立设置子进程回归。 |
| `tests/test_vision.py` | +4 / −1 | 原快照测试补齐新增代数并验证后台参数；继续断言不修改共享配置。 |

## 验证记录

- 注册事务、旧句柄、菜单、设置草稿/清理、真实 Qt 窗口、共享执行租约及独立设置子进程均有针对性回归。
- 首轮全量：**2 failed、3253 passed、11 skipped、14 warnings，519.28 秒**。两项为本轮引入：`window.py` 超出既有 4671 行限制，以及旧 snapshot-only 测试没有初始化新增请求代数。
- 修复：将手动识屏执行入口机械提取到功能模块并保留原 facade，不提高行数门；snapshot 测试补齐代数并断言传入后台执行，保留不修改共享配置的原断言。
- 修复后专项：`python -m pytest -q tests/test_architecture.py tests/test_vision.py tests/test_screen_contributions.py` → **40 passed、1 skipped，6.86 秒**。
- 最终全量：`$env:QT_QPA_PLATFORM="offscreen"; python -m pytest -q` → **3257 passed、11 skipped、13 warnings，502.62 秒**，退出码 0。运行在本机 Windows / Python 3.11.1，不代表其他平台实机通过。
- 新贡献专项再次复验：`python -m pytest -q tests/test_plugin_contributions.py tests/test_screen_contributions.py` → **29 passed，6.20 秒**。包含真实 Qt、独立设置子进程、禁止加载执行模块、移除/故障草稿和共享租约边界。
- 最终相比首轮多 2 个收集用例，来自新增报告的章节与索引参数化校验；原先两项失败已通过。11 个 skip 未增加，没有新增 skip/xfail。
- 14 → 13 warnings 的日志差异仅是 `test_try_move_success_still_builds_plan_and_moves` 未再触发 `QImage.mirrored` 弃用警告；镜像分支取决于朝向。剩余警告仍是原有 `QImage.mirrored` / `QHoverEvent` 弃用提示，没有屏蔽告警，也不宣称弃用问题已修复。
- `python -m ruff check pet tests scripts` → **All checks passed**；`python -m ruff format --check pet tests scripts` → **403 files already formatted**。
- `python -m mypy pet/plugins pet/content pet/catalog.py pet/config.py pet/workers pet/agent_link.py pet/screen_understanding pet/desktop_query.py pet/credentials.py pet/feature_bindings.py pet/official_features.py` → **45 source files，no issues**。
- `python scripts/check_docs.py` → **109 files scanned，通过**；`python -m pytest -q tests/test_pr_report_discipline.py` → **41 passed，0.59 秒**；`git diff --check` → **退出码 0**（原有 `SPEC.md` LF/CRLF 提示不是错误，本轮未改它）。全量结束后只补证据文档，没有再次修改运行代码或测试。

## 性能分析

命令：`python scripts/benchmark_contributions.py --native`，随后 `python scripts/benchmark_contributions.py`。环境为本机 Windows、Python 3.11.1；Qt 分别使用 `windows` 和 `offscreen`。预热后测量，最终样本在全量测试退出后顺序执行，没有与 pytest 并行；没有前后版本对照，不能据此宣称加速。

| 路径 | 样本数 | Windows 原生平均 / P95（ms） | offscreen 平均 / P95（ms） |
|---|---:|---:|---:|
| 注册＋撤销一批贡献/命令 | 500 | 0.0118 / 0.0155 | 0.0055 / 0.0061 |
| 识屏菜单创建＋销毁（含 Qt DeferredDelete） | 100 | 0.7868 / 1.3946 | 0.6036 / 1.1945 |
| 识屏设置组件创建＋释放（含 Qt DeferredDelete） | 30 | 8.1651 / 17.4709 | 8.8270 / 12.9412 |

- **稳态/触发频率**：注册发生在宿主挂载/重新启用，菜单按原有打开频率创建，设置按原有窗口打开频率创建；未增加后台轮询或系统热键。延续“从前台添加白名单”的用户触发单次 3 秒查询，不新增轮询频率。
- **残留**：两种后端结束后，设置 QObject 残留、widget 数量增量、菜单观察者、贡献和命令均为 **0**；线程增量 **0**。创建设置时活动定时器范围 **[0, 0]**（未点击前台查询）。另有真实 Qt 撤销/关闭测试覆盖已启动查询定时器的取消。
- **内存**：额外 500 次注册/撤销并 GC 后，`tracemalloc` 保留 **632 B**，原生峰值 **20296 B**、offscreen 峰值 **20436 B**。这是有界 Python 分配样本，不是 RSS、Qt 原生堆测量或长期无泄漏证明。
- **I/O 与执行**：导入阻断器禁止 `pet.vision`、`pet.proactive`、`pet.app`、Worker Supervisor 和 `keyring`，两种后端都返回通过；没有截图、视觉网络请求或 Worker/线程启动。设置仍可读取原有配置；本轮注册/撤销不新增持久化状态或磁盘周期任务。
- 本次不做长期 soak、模型请求或包体/构建比较；屏幕理解尚未物理外置。JSON 原始记录在 `.scratch/phase4a-contributions/benchmark-native.json` 和 `benchmark-offscreen.json`，上述数字已写入版本化报告。

## 实机运行记录

本机实际执行 `python scripts/benchmark_contributions.py --native`，退出码 **0**，关键输出为 `qt_platform: windows`、`shown_windows: 0`、`execution_import_guard: passed`，残留计数均为 0；随后 offscreen 变体同样退出码 0。使用真实 Qt 对象及系统后端，但不显示窗口，不触发真实截图、模型请求或 keyring。此探针只能证明本机组件可创建/释放，不能冒充用户完成菜单操作、凭据保存或主动识屏验收。

现有独立识屏设置测试覆盖 720/1100 宽度、浅/深主题、滚动与键盘焦点；本轮全量包含这些用例。它们是自动化布局边界，不是可见桌面人工审美/交互验收。

用户尚未手测当前独立视觉配置及贡献版本；以前“手动看看屏幕正常”不是本版证据。自动识屏、托盘自然退出仍未人工验收，不据此判定故障。涉及用户桌面、真实凭据与模型的手测不能由本轮确定性替身替代，故明确保留人工门。

## 限制、保护和回滚

- 未增加全局快捷键、跨进程安装状态、签名加载、外置 host/Worker 或最小 Core 产物。
- 无功能提供方时菜单/设置宿主不加载功能 UI；这只是贡献层边界，不是整个 Core 已可删除识屏目录。
- 手动入口提取仍通过现有内部窗口兼容接缝运行；将全部策略改为受限窗口 DTO/端口属于后续 host 迁移，不宣称已消除所有私有字段依赖。
- 停用保留安全设置草稿；正常移除确认保存/放弃/取消；故障先禁止执行，保留非敏感只读草稿并清除密码，不调用故障保存逻辑。
- 任务开始 SHA-256 快照核对：`pet/updater.py`、`pet/update_settings.py`、`pet/__main__.py`、演示 HTML、全部 9 个 Worker 模块及已有构建脚本保持原样；打包/保护路径 Git 状态无差异。暂存区为空，构建产物未加入；历史报告没有被本轮覆写。
- 本轮不提交/推送。未来应仅暂存本轮增量形成独立提交后使用 `git revert`；当前不要用 HEAD 回退覆盖前序未提交成果。
