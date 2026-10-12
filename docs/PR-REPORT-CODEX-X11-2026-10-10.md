# Codex 联动与 X11 桌面兼容：PR 验证报告

基线：`origin/main@ee699c7`；分支：`feat/codex-agent-link`；记录日期：2026-10-10。
本报告记录 fork 预发布与上游集成版本。2026-10-12 确定以同一个 PR 提交 Qt 兼容修复、Codex 联动和 X11 桌面兼容，保留三个主题提交。
相关规范：[Agent 联动协议](AGENT_LINK_PROTOCOL.md)、[打包流程](ONEDIR_PACKAGING.md)、[交付要求](DEV-HANDOVER.md#八开发流程规范)。

## 核心特性

桌宠菜单可以安装、关闭 Codex 插件。hook 负责会话开始、结束和权限状态；会话 JSONL 提供公开进展与最终回复，气泡分别显示会话名、活动状态和正文。
文件变化通过 Qt 通知唤醒读取线程，没有增加周期文件轮询。失败的 hook 返回空成功响应，同时记录经过脱敏的本地日志；Codex 授权仍由用户确认。

Linux/X11 下，Overlay 覆盖虚拟桌面，桌宠可跨屏拖动；气泡使用窗口实际坐标。
原生 XShape 输入区域仅包含可见桌宠像素，透明区域不再阻挡下层应用。其他系统保留原有输入后端。

## 修改文件说明

下面是相对上述基线的实现及验证文件增删数。文档自身和索引为本次新增报告及入口登记，最终精确行数以 `git diff --numstat origin/main...HEAD` 为准。

| 文件 | 增删 | 修改及原因 |
|---|---|---|
| `.github/workflows/pr-test.yml` | +17/−3 | 增加手动入口及 ci/** 分支触发，允许 fork 在上游 PR 之前执行三平台 CI。 |
| `integrations/dsh-pet-codex/.codex-plugin/plugin.json` | +17/−0 | 声明本地 Codex 插件身份与能力。 |
| `integrations/dsh-pet-codex/hooks/hooks.json` | +100/−0 | 注册生命周期、工具和权限 hook。 |
| `integrations/dsh-pet-codex/scripts/bridge.py` | +215/−0 | 将 hook 转为有界快照，维护会话、轮次及并发审批状态。 |
| `integrations/dsh-pet-codex/scripts/diagnostics.py` | +105/−0 | 隔离超时和异常，限制日志大小并避免记录敏感正文。 |
| `packaging/pet_entry.py` | +5/−1 | 打包入口在 GUI 启动前分派 hook。 |
| `packaging/pet_entry_no_chat.py` | +4/−0 | 无 Chat 变体同样支持独立 hook 入口。 |
| `pet/__main__.py` | +7/−0 | 在加载 Qt 前分派 hook 子命令。 |
| `pet/agent_bubble_content.py` | +67/−0 | 分离标题和状态，按字形基线绘制正文，提供截断、省略号和末行淡化。 |
| `pet/agent_file_watch.py` | +80/−0 | GUI 线程拥有 QFileSystemWatcher，合并通知并唤醒工作线程。 |
| `pet/agent_link.py` | +320/−16 | 接入 Codex 监控，处理公开进展、最终回复和气泡所有权，避免过期事件覆盖新会话。 |
| `pet/agent_snapshot.py` | +139/−0 | 读取有界快照，合并会话标题、活动与结束回复，提供监听路径和语义超时。 |
| `pet/app.py` | +3/−0 | 使伴随窗口遵循 X11 工作区显示策略。 |
| `pet/codex_install.py` | +254/−0 | 发现 Codex 客户端并安装本地插件；运行脚本独立于可删除的插件缓存。 |
| `pet/codex_transcript.py` | +251/−0 | 增量读取 JSONL 与会话标题，跳过历史、私有推理和工具输出。 |
| `pet/config.py` | +40/−3 | 增加 Codex 联动配置及默认恢复、归一化处理。 |
| `pet/context_menus/shared.py` | +7/−0 | 加入 Codex 开关及安装入口。 |
| `pet/hook_runtime.py` | +55/−0 | 提供无需 Qt 的打包 hook 入口，隔离工作进程错误。 |
| `pet/linux_overlay.py` | +148/−0 | 增加 X11 虚拟桌面几何与按透明度生成的输入区域，修复跨屏和点击阻挡。 |
| `pet/multi_window_shared.py` | +18/−2 | 转发结构化气泡参数，暴露实际展示气泡供所有权判断。 |
| `pet/overlay_shell.py` | +19/−9 | 转发气泡字段，同步虚拟桌面范围及鼠标穿透状态。 |
| `pet/overlay_window.py` | +22/−6 | 在 X11 使用虚拟桌面与原生输入区域，避免窗口管理器移动大窗口。 |
| `pet/platform_linux.py` | +105/−0 | 为 X11 桌宠及伴随窗口请求所有工作区可见。 |
| `pet/speech_bubble.py` | +59/−4 | 增加自适应 Agent 内容，普通消息保持原布局；接入 X11 工作区策略。 |
| `pet/sprite_bubble.py` | +14/−4 | 转发结构化气泡，使用实际窗口坐标计算锚点。 |
| `pet/window.py` | +7/−35 | 转发气泡参数；将展示辅助逻辑放到 window_alerts，遵守主窗口行数预算。 |
| `pet/window_alerts.py` | +47/−0 | 承接普通及结构化气泡展示，保留提醒优先级和持有时间。 |
| `pet/window_optional_services.py` | +1/−1 | 增加 Codex 在联动服务启用条件中的判断。 |
| `tests/test_agent_link.py` | +1/−0 | 更新既有集成测试的配置或支持列表，与新增 Codex 行为一致。 |
| `tests/test_desktop_pet_features.py` | +13/−1 | 更新既有集成测试的配置或支持列表，与新增 Codex 行为一致。 |
| `scripts/benchmark_codex_link.py`（新增） | +53/−0 | 新增可复现性能测量入口，临时数据不接触用户配置。 |
| `scripts/benchmark_linux_overlay.py`（新增） | +32/−0 | 新增可复现性能测量入口，临时数据不接触用户配置。 |
| `tests/codex_helpers.py`（新增） | +19/−0 | 新增回归用例或共享辅助代码，覆盖安装隔离、hook 容错、会话增量、Qt 事件交付或原生 X11 行为。 |
| `tests/test_codex_bubble.py`（新增） | +114/−0 | 新增回归用例或共享辅助代码，覆盖安装隔离、hook 容错、会话增量、Qt 事件交付或原生 X11 行为。 |
| `tests/test_codex_file_watch.py`（新增） | +127/−0 | 新增回归用例或共享辅助代码，覆盖安装隔离、hook 容错、会话增量、Qt 事件交付或原生 X11 行为。 |
| `tests/test_codex_hook_runtime.py`（新增） | +141/−0 | 新增回归用例或共享辅助代码，覆盖安装隔离、hook 容错、会话增量、Qt 事件交付或原生 X11 行为。 |
| `tests/test_codex_install.py`（新增） | +40/−0 | 新增回归用例或共享辅助代码，覆盖安装隔离、hook 容错、会话增量、Qt 事件交付或原生 X11 行为。 |
| `tests/test_codex_transcript.py`（新增） | +270/−0 | 新增回归用例或共享辅助代码，覆盖安装隔离、hook 容错、会话增量、Qt 事件交付或原生 X11 行为。 |
| `tests/test_linux_overlay.py`（新增） | +176/−0 | 新增回归用例或共享辅助代码，覆盖安装隔离、hook 容错、会话增量、Qt 事件交付或原生 X11 行为。 |

## 性能分析

环境：本机 Ubuntu 24.04、X11/xcb、Python 3.11.16、Qt/PySide6 6.11.2，16 个逻辑 CPU；DP-1 与 DP-6 均为 1920×1080，水平排列。
下面读文件及 XShape 数据来自本轮实测；采样期间有全量测试进程，因此不用于声称相对上游的加速比例。

| 命令或场景 | 样本量 | 中位数 / p95 |
|---|---|---|
| `.venv/bin/python scripts/benchmark_codex_link.py`，16 个活跃会话、快照未变 | 2000 | 96.03 / 146.65 µs |
| 同脚本，直接 bridge 子进程 | 30 | 24.72 / 26.04 ms |
| `QT_QPA_PLATFORM=xcb .venv/bin/python scripts/benchmark_linux_overlay.py`，输入区域未变 | 500 | 4.54 / 5.08 µs |
| 同上，移动并复用透明度掩码 | 500 | 9.31 / 11.97 µs |
| 同上，强制重建透明度掩码 | 500 | 57.01 / 62.47 µs |

读快照基准额外运行 2000 次后，tracemalloc 净保留增加 192 B、峰值增加 7792 B；16 会话快照为 6147 B。这不是整个 GUI 的 RSS 测量。
事件通知延迟现场探针（20 个独立写入子进程）：文件关闭到 GUI 收到快照，中位数 72.59 ms、p95 82.72 ms、最大 83.00 ms；不包含 Codex 客户端写盘前的延迟或绘制耗时。

- 稳态：没有文件通知和语义截止时间时，Codex 读取线程等待 Event；不周期扫描文件。X11 每次绘制检查缓存签名，区域未变不发送形状更新。
- 新增成本与频率：每个 hook 启动 supervisor 和 worker；worker 超时预算 1 秒。文件通知合并窗口 40 ms，气泡最小刷新间隔 0.5 秒；输入形状在动画、移动、显隐或穿透状态改变时更新。
- 系统调用与 I/O：使用已有监控工作线程；新增 Qt 文件监听和一个 X11 连接，不增加轮询线程或网络请求。hook 写入快照和有界诊断日志，标题查询可只读访问 Codex SQLite。
- 内存：JSONL 每次读取有预算、单行有上限；活动会话最多 16 个；原生输入区域为每个可见 sprite 缓存一个掩码，隐藏或移除后清理。没有将会话全文复制到桌宠数据库。

## 实机运行记录

本轮命令：`QT_QPA_PLATFORM=xcb .venv/bin/python -m pytest -q tests/test_linux_overlay.py tests/test_codex_bubble.py`，结果 **15 passed in 1.12s**。
测试使用真实 Qt 事件循环和本机 X server；读取 XShapeGetRectangles 验证透明处无输入区域；通过 Qt 鼠标事件跨屏拖动，核对气泡与桌宠所在屏幕。未向用户的其他应用注入点击。
隐藏 sprite 或打开鼠标穿透时，原生输入区域为空；关闭窗口后 X11 连接释放。

此前现场已复现旧 Overlay 宽度为 1920 而非 3840、气泡锚点沿用旧窗口坐标、透明 Overlay 整屏接收输入。修复后对应断言通过。
用户在前序会话确认 Codex 已出现气泡和动画变化，并反馈了文字截断问题；该反馈用于后续基线绘制修复，不能代替当前安装包验收。

本机有 CLI、IDE 和 App 的 Codex 核心，曾在隔离 CODEX_HOME 中验证插件识别和安装卸载。未自动更改用户的 hook 信任。
本轮 CI 不预装三种交互客户端，因此其中的插件授权与真实会话展示仍需用户在对应客户端确认。
Windows/macOS 原生桌面与混合 DPI/非矩形多屏布局尚未实机验证；offscreen 成功不代表这些环境已验收。

## 测试与验证

新提交用例覆盖 JSONL 历史跳过、半行、替换、私有推理过滤、最终回复仅一次、并发审批、hook 缺失脚本/日志失败/超时、独立进程文件替换、监控线程销毁、共享代理到真实气泡、原生输入区域。
这些行为在开发阶段的 scratch 回归中先观察到失败再修复；本轮把用例整理到仓库测试目录。具体失败类型包括 `unexpected keyword argument agent_title`、首行字形裁剪和整屏输入区域。

本轮新增用例聚焦验证：37 passed、5 skipped；补充安装隔离：2 passed。X11 另跑 15 passed。
最终全量、GitHub CI 和安装包结果在本报告后续验证记录追加；尚未完成的门禁不视为通过。

## 风险与回滚

会话 JSONL 是客户端本地格式，未来格式变化可能让进展缺失；生命周期仍由 hook 提供。
X11 可用工作区使用矩形并集，非矩形布局的屏间空洞没有单独建模。
旧版本遇到新配置键时保留配置；卸载插件后保留不可变 hook 运行脚本，防止仍运行的 Codex 会话调用已删除路径。
回滚按 Codex 联动与 X11 两组实现分别撤销，并退出、重启桌宠。用户的 Codex hook 信任不由回滚脚本修改。

## Fork 预发布与上游 PR

预发布阶段先在 `Huffer342-WSH/dsh-pet-indesktop` 运行 Actions 并发布预览包；2026-10-12 使用上游 [PR #245](https://github.com/MerZlin/dsh-pet-indesktop/pull/245) 提交完整集成改动。
预览版保留上游应用内版本 4.2.1 和更新源；Release 使用独立标签和说明区分，预览用户不要通过内置更新器切换 fork 版本。
正式 PR 的说明应链接本报告，列出影响、验证和回滚；fork 专属发布元数据不混入上游功能实现。

## 本轮本地门禁补充

首轮全量为 4317 passed、42 skipped、1 failed：品牌扫描把新增集成测试视为无关品牌文案。明确允许 Codex 集成测试、本报告及索引后，该检查和关联用例为 114 passed；未删除品牌检查。

满载验证：16 个忙循环进程分别绑定 16 个逻辑 CPU，并行运行 `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_codex_file_watch.py tests/test_codex_bubble.py tests/test_agent_link_threads.py` 三遍，每遍 **35 passed**，总耗时分别 3.22、3.42、3.42 秒。负载进程全部回收。DSH 析构信号警告此前已在原始 origin/main 复现，没有隐藏。

Node bridge 门禁：工作流列出的 7 个 JS 文件共 **34 passed**，`node integrations/dsh-pet-bridge/verify_import.mjs` 输出 `bridge zero-dependency smoke: import + subtraction surface + source bans OK`。

本轮真实客户端核心补测：CLI/App/IDE 共 **9 passed in 0.70s**（隔离 CODEX_HOME）。旧版兼容用例固定读取初始实现 `417a007`，避免 HEAD 前进后把当前实现误当旧版。用户实际 profile 和信任状态未修改。

最终本地全量：`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`，**4324 passed、42 skipped、16 warnings，236.88 秒**。Windows hook 用例选择 `commandWindows` 的测试适配另跑 11 passed；Linux 产品行为未修改。ruff 和 `git diff --check` 通过。

## 首轮 GitHub 验证与测试隔离修正

提交 `3ab32a4` 已推送 fork。Actions 首次启用确认在网页完成（REST 权限接口的 enabled=true 不代表 fork 工作流已激活）。CI run 38037952995、Linux 38037955635、macOS 38037957236、Windows 38037959623。

Linux CI 和构建的 pytest 在约 15% 处 exit134，未进入打包；日志显示 PySide6 6.12.0，本机此前为6.11.2。排查发现新增销毁用例全局冲刷 DeferredDelete，违反既有 conftest 的定向清理约束。增加无关 QObject 的存活断言后旧用例失败，改为仅冲刷 monitor 后13项聚焦通过。此结果证明测试隔离缺陷已修，是否解释远端崩溃仍需同版本复验；不把推断写成根因已确认。

### 原生崩溃根因与版本约束

在独立 worktree 的旧提交3ab32a4、Qt6.12.0 下复现 `Fatal Python error: none_dealloc`，栈落在 `AgentFileWatch.stop → QTimer.stop`。最小探针连续三组各100次 QTimer.stop：6.12.0 的 None 引用计数每组 −100，6.11.2 每组0；因此定向删除修正不是这次原生崩溃的完整根因。新增 `tests/test_qt_timer_refcount.py` 在独立子进程检查此行为，6.12.0 红、6.11.2 绿。不限制PySide6版本，保持Python3.11，通过[原生运行库修复](PR-REPORT-QT612-ABI-2026-10-10.md)恢复正确的None引用所有权。用户已有.venv未更改。

Windows构建首轮4278passed/19skipped/1failed：文件替换测试子进程遭遇WinError5，独立WindowsCI同用例成功。测试写入端仅对PermissionError在10秒内重试，仍要求真实替换及原生通知交付；未增加产品文件轮询或降低断言。

## 脏区域透明擦除补充

Overlay 使用 WA_NoSystemBackground，旧绘制路径没有擦除移走 sprite 的像素。paintEvent 先以 CompositionMode_Source 把 event.region() 写回透明，再恢复 SourceOver 绘制新帧。`pet/overlay_window.py` 本轮 +8/−0；`tests/test_overlay_window.py` +24/−1，新增真实 QWidget.render 的旧区域 alpha 断言。旧提交3ab32a4红，新实现绿；本机原生X11该用例与跨屏族共7passed。

命令：`QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/benchmark_overlay_paint.py`（新增测量脚本），1920×1080窗口、320×180脏区，预热100次后测1000次。旧路径中位数/p95为5.28/5.52µs，透明擦除路径11.29/11.83µs；这只测空脏区的Qt渲染成本，不代表真实动画整帧耗时。只随重绘发生，不增加线程、网络、磁盘或持久缓存。

最新本地集成全量（含残影修复与定向销毁用例）：4326passed、42skipped、240.89秒；满载三遍各35passed（3.12/3.37/3.57秒）。这些结果来自Python3.11.16+Qt6.11.2，不代表Qt6.12问题已解决。Python3.12.3+Qt6.12.0的独立最小探针中，stop/setInterval/start各10000次，None引用计数差均为0，正常退出；最终保留CI的Python3.11，不采用迁移到3.12的方案。

## 保持CI版本的最终本地验证

Python3.11 + PySide6 6.12.0保持不变，原生绑定修复及源码分发详见[Qt运行库报告](PR-REPORT-QT612-ABI-2026-10-10.md)。完整集成版本4328passed/42skipped，进程正常退出；真实X11相关16passed，满载三遍各35passed。逐只配置测试的时间戳前提已修正，未改生产音效逻辑。下一轮远端CI和构建使用这一实现重新验证，不复用旧3ab32a4安装包。

## 最终交付记录（2026-10-10）

完整集成提交`6ae3519`的[三平台CI](https://github.com/Huffer342-WSH/dsh-pet-indesktop/actions/runs/38044045940)及Linux/macOS/Windows三条构建均通过；[预发布](https://github.com/Huffer342-WSH/dsh-pet-indesktop/releases/tag/preview-4.2.1-codex.20261010.1)已公开，包含8份产物。发布标签指向实际构建提交，后续报告更新不改变安装包来源。

最终Linux纯桌宠包在本机隔离配置、缓存、用户数据和网络后，offscreen及原生X11各运行15秒无提前退出或Traceback；frozen hook退出0、输出{}、stderr为空，无_MEI目录。独立进程加载包内两份原生库并核对/proc/self/maps后，六项单例引用探针全0。ZIP SHA256：`5abaa92b2becb6fd2233a30df0d0a42af191a469792af714b06d32eac20fef85`。Windows/macOS的原生桌面人工验收仍未完成。

上游交付现统一为 PR #245，包含 Qt 兼容、Codex 联动、X11 兼容三个主题提交。此前预发布不包含 2026-10-12 的气泡正文去重修复，不能将预发布 CI 结果表述为该修复的验证结果。

## 气泡去重与合并交付补充（2026-10-12）

状态切换此前会把缓存正文当作新的气泡内容。现按 agent、监控代次及回合记录成功展示的正文，状态继续刷新，已展示的同一句进展不随工具切换重复出现。跨会话返回保持去重，新回合与最终回复仍正常显示。缓存最多128项，仅在成功显示正文后更新，不增加线程、网络或磁盘访问。

真实 Qt 事件循环、MultiWindowProxy 和 Overlay 气泡回归在修改前复现重复正文，修复后通过；相关测试209passed、2skipped。测试覆盖状态切换、其他会话抢占后返回、新进展、新回合及最终回复。

合并提交前验证：Python3.11.16、修复后的PySide6 6.12.0、QT_QPA_PLATFORM=offscreen，按`.github/workflows/pr-test.yml`现有进程隔离方式运行全套pytest：主套件4255passed/42skipped，WebM生命周期41passed，低优先级预热27passed，恢复提示5passed，共4328passed/42skipped；ruff通过。主套件保留20条既有警告，没有隐藏DSH线程清理警告。

高负载命令为`python -m pytest -q tests/test_codex_file_watch.py tests/test_codex_bubble.py tests/test_agent_link_threads.py tests/test_now_playing_session_probe.py tests/test_recovery_hints.py`，同时启动16个分别绑定逻辑CPU的忙循环进程，三遍各48passed，总耗时25.85/26.21/25.97秒，负载进程全部回收。报告规范检查75passed。此次只有本报告在全量验证后补充结果，运行代码未再修改。
