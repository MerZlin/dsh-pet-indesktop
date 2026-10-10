# Phase5A 连续记录：WORKLOG

## 2026-10-10 M00 已推送（最新）

已提交并正常推送 `origin/codex/phase3-worker`，完整 SHA `0a299612714e5fad55a24a5506dfce938b9eeb1c`，远程核对一致；159 文件，14196 新增/826 删除。全量 4509 passed、15 skipped；26 族三轮各 292 passed；Ruff、文档与敏感检查通过。下面“待提交”是检查点之前的历史快照。新任务见 [MOD 计划](../mod-authoring-v1/PLAN.md)，不自动发布后续改动。


## 2026-10-10 用户验收与 M00 检查点（当前）

**M00 新门禁已通过（2026-10-10）**：Ruff、工作树/暂存 `git diff --check`；聚焦 **123 passed in 189.57s**；全量 **4509 passed, 15 skipped, 14 warnings in 1910.76s**，exit 0，643 个源码输入摘要前后一致。26 个相关时序族在 20 个自有逐核负载进程下复跑：三轮各 **292 passed**，pytest **198.76 / 143.17 / 137.04s**（调度总时长 **201.656 / 146.203 / 138.828s**），CPU 中位均 **100.0%**，全部负载进程自然退出、残留为空。159 文件敏感模式扫描零命中；540 个相对链接无缺失，报告均入索引。暂存检查发现并修正文档的两行尾随空格；没有更改产品代码来通过门禁。

Windows 11 build26100、Python3.11.1。命令：`python -m ruff check pet features scripts tests packaging`、`python -X utf8 -m pytest -q`（隔离 APPDATA/LOCALAPPDATA、offscreen 和独立 basetemp）、Git 工作树/暂存差异检查。负载族列表与每轮 CPU 样本/退出码由 `m00-checkpoint-20261010/run_highload.py` 和 `delivery-highload-release.json` 留在本机证据目录；不把日志/缓存/脚本化临时数据纳入提交。历史冻结与性能实测仍见 S/U/A 报告，不宣称本轮重新构建或重新使用真实 Provider。

**准确停点**：以上结果已收齐，尚未提交/推送；下一步是最后核对暂存范围和远端未分叉，提交当前源检查点，再正常推送并核对 SHA。新 MOD 功能尚未开始。


- 用户已明确反馈 **“实际体验确认没问题”**；对应当前交付 Core **4.2.4** / AI **1.0.3** / Screen **1.0.3** 与 `_s01b/setup-release/dsh-pet-core-webm-setup.exe`。S 修复的用户体验验收通过，不再列作等待同一轮反馈。
- 自动化/冻结程序证据沿用下方注明日期的历史结果；本轮提交前另行重跑 Ruff、全量 pytest 和受影响时序族满 CPU 三遍，结果已收齐，见上方新门禁。用户反馈不扩张为未测平台、正式签名发布或新 MOD 功能已验收。
- 用户授权当前修改提交并正常推送至 **origin/codex/phase3-worker**；基线 `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`。当前尚未提交/推送，先完成 M00，成功核对远端 SHA 后才开始 MOD 管理中心重做。
- 159 个明确文件（126 已跟踪修改 + 33 新源码/文档）纳入审核；13 个构建/试装生成目录不提交、不删除。原始日志/缓存/测试配置/真实密钥不入库；不触碰真实安装目录。
- 新功能计划尚未实施；其后产生的改动不在本次推送授权内。准确下一步：使用已收齐的 M00 门禁结果，最终审阅暂存、提交、推送并核对。

**实际效果与限制**：4.2.4 的简易 API、项目内 Worker 和已有 Setup 体验已由用户确认；本轮先保存远程回滚点，不提前宣称市场式 MOD 列表或公开 v1 接口可用。


<!-- S04_CURRENT_START -->
## S01–S04 工程交付快照（2026-10-10；用户反馈见上方）

> **S01–S04 工程交付完成；真实 Provider/屏幕效果与用户安装体验仍待确认，Phase5A 不关闭。** 下方 T/R/U/A 和早期 S04 快照保留历史；其旧产物、旧测试数量不是本轮最终结果。

- 唯一待验候选：`_s01b/setup-release/dsh-pet-core-webm-setup.exe`；Core **4.2.4** / AI **1.0.3** / Screen **1.0.3**（Screen 要求 Core ≥4.2.4）。SHA-256 `7282fc05ac0b14138f00d4dbb71edf73af82aee261500f59f2217e8a1756a40d`。`_s01b/setup`、`setup-final` 是中间产物，不交付用户。
- 已修复 portable 项目内 `data/feature-runtime` 误拒绝；仍拒绝 Core/DLC 程序树和 reparse。故障原因先记录再发布状态，手动重试可恢复。真实冻结链路额外暴露“包验证耗时计入运行心跳”缺陷，已改为 READY 后计时；握手超时仍保留。
- 追加用户要求已落实：测试按钮旁显示“连接成功/失败 · HTTP nnn”，超时/网络/TLS 用独立结果码，后方备注解释且明确测试不保存。明暗 × 720/1100、失败保留草稿验证；无 API 架构/Setup 行为改动。
- 冻结最终 Core→项目内已安装 Screen→生产 Worker→租约 HELLO/READY→Core/Worker 自然退出 **0/0**，退出后占用 free；未抓真实屏幕、未读真实 Key、未请求真实服务。Core/Setup/隔离安装副本 PE 10 帧匹配鲸鱼娘 ICO，Windows Shell 私有快捷方式实显一致。
- 最终全量：**4507 passed, 15 skipped, 14 warnings in 802.39s (0:13:22)；exit 0，643 个输入前后摘要一致**。
- 受影响 17 族高负载：**第1轮 169 passed in 47.59s、exit 0、CPU 中位 100.0%（含调度49.875s）；第2轮 169 passed in 47.06s、exit 0、CPU 中位 99.75%（含调度48.656s）；第3轮 169 passed in 47.69s、exit 0、CPU 中位 100.0%（含调度49.813s）；三轮全部通过且自有负载进程自然退出**。
- Ruff、21 个改动 Python format、git diff --check 通过；报告纪律 65 passed，13 份文档 471 个相对链接/尾随空白检查通过；见 closeout-quality.json。相关当前 96 passed（26.59s）；最初目录 red 5 failed、启动诊断 red、心跳/连接反馈 red 15 failed、主题 red 2 failed、已有布局可访问名称 red 4 failed 均保留。
- [工程报告](../../docs/PR-REPORT-PORTABLE-SCREEN-WORKER-2026-10-10.md) 提供逐文件增删、启动/内存/界面实测、冻结/图标原始证据和人工步骤。证据根 `s01-portable-worker-20261010/`；旧候选保留，不提交/推送，不改 `E:/dsh-pet-core-webm`，未清图标缓存或修改用户桌面快捷方式。

### 本轮执行记录（本地 2026-10-10）

1. 保存任务开始 dirty 基线 769 文件，先补 portable/目录/同步故障/重试回归，保留 red 与阶段 green。
2. 放行唯一可信 runtime，修复诊断顺序和失败后的租约释放；213 passed/1 skipped 为加入最终 UI/心跳前的阶段结果。
3. 第一轮冻结接入达到 READY 后仍因心跳失效退出；回溯签名验证耗时，补真实 QProcess 红灯后将心跳基准移至 READY；未用直接 Worker 的绿替代 Core 链路。
4. 用户追加连接反馈：先 red，再实现真实 HTTP 状态结构/按钮旁结果，实际 Qt 截图揭示浅色主题不显眼，补主题回归后修复。既有四布局门再发现按钮可访问名称缺失，保留失败并修复。
5. 前两次全量分别因四个过期版本断言、四个可访问名称断言失败；第二次途中产品修正，摘要不一致，不作为最终通过。第三次锁定最终输入复跑，不掩盖失败。
6. 最终 Core/Setup 重建完成；Screen 1.0.3 ZIP、AI 1.0.3 ZIP、Worker/Probe 闭合输入审计；真实冻结最终副本自然退出 0/0。私有 Windows 快捷方式和两个 EXE 实显鲸鱼娘。
7. 全量/三轮负载/最终文档门的最新准确状态见上方；原始日志、测试数据和旧候选全部保留，不读取真实秘密，不删除未知 staging。

## 本次实际可体验的效果与限制

更新新候选后保留原配置；识屏能从项目内数据目录启动，连接按钮旁直接看成功/失败与结果码。Setup 行为不改；真实 Provider、真实屏幕和用户桌面图标仍需最后确认，Phase5A 不关闭。
<!-- S04_CURRENT_END -->

## 2026-10-10T00:05:34+08:00 A04–A05 最终验证与交付

- 最终固定源码全量 4472 passed/15 skipped/15 warnings，三轮满载各185 passed；Ruff/38文件格式/diff和63报告纪律通过。
- 图标 red3→green；冻结发现 Qt 控件释放 red3→60相关green；修复后两 DLC 冻结日志正常；保留中间失败而不覆盖。
- 首次负载清单写错 test_files.py、静态工具隔离 APPDATA 隐藏 Ruff 都是 runner 环境/清单错误；修正后重新执行通过，不记为产品失败。所有自有负载进程自然退出。
- 新 Setup/Core/两个 DLC/Worker/辅助程序已重建、哈希/输入/原图标10帧核对；_a01b 3,447,898,528B。已验收旧 Setup 未覆盖；安装卸载仅加图标、不改逻辑。
- 报告 [PR-REPORT-SIMPLE-API-2026-10-09](../../docs/PR-REPORT-SIMPLE-API-2026-10-09.md) 与索引已更新，同一组 PLAN/HANDOFF/STATUS/SUMMARY 刷新。日期跨到10月10日，未另开阶段。
- 工程执行完成；用户验收仅在最后报告集中列出。未用真实 Key/Provider/屏幕，未再次系统安装，未提交推送。Phase5A 不提前关闭。

## 2026-10-09 22:27 简易 API A01–A03

- 初始 10 failed red、连接路径 2 failed、父窗口 Esc/关闭 red、配置恢复可编辑 red 均已归档。
- 简易表单/自动用途适配/视觉主 Key 尝试与错误分类已实现；相关 299 passed / 1 skipped。
- Setup 保持不动。新构建递增候选版本，不覆盖既有产物；A04 待全量/负载/实机/重建。

# 简易 API 修复：WORKLOG（2026-10-09）

当前：A01 开始，已确认计划落盘。Setup 已获用户确认，保持不动。实现/自动化/实机/重建均未完成；未读取真实 Key、未提交推送。

任务与合同见 [PLAN](PLAN.md)。准确停点：补失败回归后实现。历史结果在下方，不冒充本轮通过。

---

# U01–U06 WORKLOG（2026-10-09）

> 更新：2026-10-09T21:18:00+08:00。U01–U06 工程实现、自动化、重建和隔离实机验收完成；待用户确认真实功能体验。

设计：[Phase5A 设计](../../docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md) · [PLAN](PLAN.md) · [STATUS](STATUS.md) · [HANDOFF](HANDOFF.md) · [WORKLOG](WORKLOG.md) · [SUMMARY](SUMMARY.md) · [报告](../../docs/PR-REPORT-SETUP-PROJECT-DIRECTORY-2026-10-09.md)

## 本轮操作、证据与分类

1. 读取当前设计/阶段记录，落盘 U01–U06；保留既有 T/R 历史和用户修改。初始失败回归 `U01-setup-project-red.log`：4 failed /5 passed /8 errors。
2. 实现 Setup-owned portable marker、路径页与 native 安装门、项目内 data/DLC、合法 portable maintenance、两种数据卸载边界。
3. 按最新用户指令移除可选 bridge/自启动 false/exception 的阻塞效果；失败/拒绝/异常回归通过，不删除占用/路径保护。
4. U06 followup red 4 failed：相对路径、残留锁重试、保留 data 重装收据、误导文案；green 22 passed /1 skipped。后改用真实 junction 兜底，Windows reparse 专项 1 passed，原权限 skip 已消除。
5. 中间产物真实设置窗口暴露入口绕过 `pet.__main__._main`，卸载误到删除失败。先补 bootstrap/UI red 4 failed，再统一启动；green 62 passed。最终实机设置创建项目 `data/config.json`，占用阻止删除，设置自然退出0。
6. Inno 首次 label 修复使用不存在全局 API，编译失败（新引入且已修）；改为 `SelectDirLabel.AdjustHeight()` 并真实编译，最终窗口完整两行41px、不重叠。
7. 删除预检 red 1 failed；增加选中数据树预检、真实删除失败 Abort；green 17 passed。最终 junction 实机保持 Core 哈希/外部哨兵不变。
8. 构建 Core（149.844s）、生产 Worker、AI/Screen 包、新 Setup；helper 是已固定哈希的既有构建复用，不声称本轮重编。审计223根源码/228stage/1032资源/2137bundle，GUI PE=2，Worker 正常 HELLO/READY/租约/自然退出。
9. 最终 Setup 实机 fresh54.922s / update37.234s / keep4.953s / reinstall55.031s / delete5.984s。三处取消逐文件字节不变；不选DLC更新保持配置；合成 staging/ledger 不阻止删除；外部源哨兵不变。仅渲染自有窗口，不依赖被其他窗口遮挡的桌面裁图。
10. 第一轮全量4437通过是中间快照。第二轮在修复期间运行，2 failed /4439 passed /15 skipped，source_unchanged=false；保留其失败证据，不改名为最终通过。最终门：4442 passed, 15 skipped, 14 warnings in 1594.26s (0:26:34)；高负载：第1轮 242 passed, 1 skipped, 1 warning in 156.61s (0:02:36)，CPU中位99.9%；第2轮 242 passed, 1 skipped, 1 warning in 157.20s (0:02:37)，CPU中位99.95%；第3轮 242 passed, 1 skipped, 1 warning in 155.64s (0:02:35)，CPU中位98.9%。
11. 性能：native门0/5000 data文件各7×100次，中位均1.72ms；冻结设置40×0.5s，CPU中位1.5%、16线程不变、RSS +128KiB（非长期泄漏结论）。详细命令、样本与局限见报告。

## 准确停点

授权范围内无剩余产品实现/构建步骤；接下来用户按本轮报告第八节验收。收到失败反馈后先复现并补 red，不能先关闭 Phase5A。

## 当前合同与保护边界

- Setup 每次显示路径页，旧路径只预填；新安装选择非根、无重解析点的 NTFS 空目录。合法本产品目录可更新；本次卸载留下的 data 由 Setup 自有 `.setup-project.json` 收据识别后可原目录重装，不能接纳任意陌生非空目录。
- Core、`portable.json` 与 `data\plugins` 同项目；RuntimeLayout 唯一 portable 身份仍为根 marker。未发布的旧 APPDATA 组合布局不迁移。
- 更新不勾选 DLC 不会删除已有 DLC 或覆盖配置；安装时只有勾选的官方包从内嵌 ZIP 导入。
- 卸载删除程序、根目录其他内容与全部 `data\plugins`；默认保留个人 data，选择删除需不可恢复确认。确认期间取消不加锁、不运行 maintenance、不改文件。
- **按用户最新要求：关联／桥接／自启动清理 best-effort，返回 false 或异常不再阻止卸载。** 可能留下该可选集成的旧引用；占用、路径边界、重解析和真实删除失败仍提示并中止。不强杀进程。
- 不加载 DLC 工厂、不读取 ledger、不恢复事务、不清理未知外部 staging；不触碰项目外源 ZIP/目录。不恢复官方 owner/factory 特判。
- 无暂存、提交、推送、正式签名或稳定产物覆盖。实机安装／卸载只操作自有生成的 `_u06-*` 目录；不读取真实 Key、不发收费 Provider 请求、不采集真实屏幕内容、不改代理/VPN。

## 当前交付与验证

- 新 Setup：`.scratch/phase5a-local-distribution/u06-delivery-20261009/setup-verified/dsh-pet-core-webm-setup.exe`；SHA-256 `7cecfde7854fa70cbfa98641457b2ade8c8a547887254ff609204ad1815f6e22`。Core 4.2.2 / AI 1.0.2 / Screen 1.0.1，Core 候选为 manual-acceptance-only。旧 r422、r3、u06-final 的 Setup 及 setup（无 verified）目录不作当前交付。
- 最新全量：4442 passed, 15 skipped, 14 warnings in 1594.26s (0:26:34)；最终634个输入核对 `source_unchanged=true`。
- 高负载：第1轮 242 passed, 1 skipped, 1 warning in 156.61s (0:02:36)，CPU中位99.9%；第2轮 242 passed, 1 skipped, 1 warning in 157.20s (0:02:37)，CPU中位99.95%；第3轮 242 passed, 1 skipped, 1 warning in 155.64s (0:02:35)，CPU中位98.9%。
- 真实 Windows：新装两 DLC、路径页预填/完整文案、根/陌生非空拒绝、空目录接受、冻结设置使用项目 data、占用保护、junction 外部目标保护、三处取消不改字节、未勾 DLC 更新、保留 data 卸载及原目录重装、全 data 卸载均通过。
- 收尾只读核对：Ruff／122个改动Python格式／diff通过，报告纪律61通过，11份Markdown的316个相对链接无错误；最终输入、产物哈希和未暂存状态见生成收据 `U06-final-closeout.json`。
- 工程证据详见 [本轮报告](../../docs/PR-REPORT-SETUP-PROJECT-DIRECTORY-2026-10-09.md)；原始日志/JSON 位于同阶段目录，仅作为生成证据，不纳入提交。

## 使用效果与人工剩余项

安装与卸载围绕一个项目目录，不再因为 DLC 状态或关联功能清理失败卡住。用户仍需使用本次新 Setup 在实际选择的空目录确认 UI 体验，并用真实 Provider/余额与真实屏幕完成验收；不能把生产 Worker 握手成功写成真实识屏通过。**Phase5A 尚未获得本轮用户确认，不正式关闭。**

---

<!-- CURRENT_R_START -->
# R01–R07 历史快照（2026-10-08；安装器合同已由 U 替代）

> 本段仅记录 2026-10-08 历史，当前状态与安装器合同以上方 U 记录为准。工程自动化/本机冻结运行与真实服务/系统操作/用户确认是不同的门；**Phase5A 未正式收尾**。更新时间：2026-10-08T17:07:52+08:00。

## 目标与已确认合同

- Core **4.2.2** 管理多服务 API、OS 安全存储引用、owner/purpose 授权与显式迁移；AI **1.0.2** / Screen **1.0.1** 要求 Core >=4.2.2。AI/文件请求仍由 AI DLC 执行，视觉仍由独立 Worker 执行。
- 通用可选 FeatureHostContext.api 返回授权元数据/版本/单次快照/订阅；不开放全局 Config、服务/密钥枚举，不恢复官方 owner/factory 执行特判。受信任 Python DLC 不是沙箱。
- 四类聊天与文件新请求解析最新已提交配置；普通保存不污染在飞快照，撤权/删服务/停用取消并拒绝迟到结果；失效输入和未保存草稿保留。测试连接不等于保存。
- 手动识屏与自动策略分离：自动关闭、空白名单或无关刷新不取消手动；生产 Worker 真实租约/HELLO/READY/自然退出是交付硬门，不回退 Core。
- 系统卸载只删 Core 及其拥有的集成，保留 DLC 已安装副本、原始 ZIP/源目录、配置、Key/个人数据；不运行 DLC 工厂/逐包事务，不被 staging/ledger/pending 阻塞。Core 占用仍要求自然退出；单包卸载独立、只删安装副本。

## 当前验证与版本对应

- 最新未插桩默认单进程全量：**4417 passed /15 skipped /14 warnings /1391.19 s**（wall 1392.541 s），exit0；pet/features/scripts/tests 共 **621** Python 源码前后/最终快照相同，无新 skip、过滤、全局 Qt 刷事件或保活补丁。之前分组与 AV 记录仅作历史。
- 生命周期既有根因真实公开 seam red 1 failed /6.63 s、子进程 0xC0000005；创建时 owner-thread 身份替代借用 QObject.thread()，green-v3 **23 passed /8.48 s**。新增回归实际 GC 三次 cyclic endpoint，再进新 QEventLoop，不是全局 keeper。
- 最新 **15 时序族 ×3轮，每轮152 passed**、20 logical CPUs/20 自有逐核负载 worker，CPU 中位全100%；Event 停止、自然 join、全部exit0、残留[]。v4第三轮20s ready失败与44.040s迟达证据保留；只将测试 Event/自然 join 有界预算改为90s，产品/租约断言不变；最新全量和v5均含该测试变化。
- 当前 c09 Core SHA-256：`1413d5ef5aeb43de9aa693c3d7060a5849fa9b1c7ae8902c24a6c511a11c7264`；**新 Setup 仅用 r422/setup-lifecycle**，SHA-256：`53fe18eff3e2b45acf0a23c1732675316241b9b84fe7d7ee8214b2429e854f38`。旧 setup-final/旧 c07/c08 不作当前交付。Worker、两 ZIP、helper 由本轮 R 重建且输入复核仍相符。
- 审计 root207/stage212（5生成策略源）/resources1000/bundle2103，PYZ2188、GUI PE=2；新 Core 的 empty/AI/Screen/both 保留 profile 均正常菜单/自然退出0、DLC payload与ledger不变；no-DLC API 服务设置可见/自然退出0。不是系统卸载重装证明。
- API 性能 n1000/n50/n200 与约3×5s短窗口；生命周期 guard 7×10000次/每路径预热1000；实测数字、样本边界与线程/RSS归因限制见 [同一报告 §8](../../docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-07.md)。末次 Ruff check、119 个改动 Python format --check、diff check 均 exit0；报告纪律 59 passed in 0.66s；19 份 Markdown 的 424 个相对链接无断链/尾随空白。

## 保护与人工边界

- 工作区 E:/AI/DSH/dsh-pet-indesktop，分支 codex/phase3-worker，HEAD 70ff464f84793f6ea3a342079dcbfb2d991fcf4e；进入 R 前78个dirty路径保留。累计差异含历史 T/用户改动，不全算本轮新增；无暂存/提交/推送/远端验证/子智能体。
- 两个生成根合计封顶 **11.25 GiB（12,079,595,520 B）**：原6 GiB + 用户约5g授权的有界5.25 GiB；保留旧包/Setup/失败证据，没有删除旧生成物或清理未知 staging。
- 不读取真实 Key、不自动截图/收费请求、不改代理/VPN、不自动开识屏、不运行系统 Setup/卸载器、不强杀进程、不正式签名发布。
- 下一步是用户按 [报告 §8.7](../../docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-07.md) 人工验收：先备份、自然退出，用**新 Setup 覆盖安装更新旧 unins000.exe**；再测一次Key/用途、四旧聊天与文件/余额、自动关时手动识屏、两来源Core-only卸载保留/重装和单包范围。真实协议、费用、屏幕与系统行为不能由自动结果代替。

## 最新施工记录与准确停点

1. 已查明HEAD既有生命周期防自等待 guard 的借用 QThread wrapper/循环GC析构问题；独立公开seam重现0xC0000005后改用创建线程身份，queued signal/取消/后台wait不变。原 red、两次脚本失误和 green-v3 原记录保留。
2. v1默认全量4417p/15s/15w/739.87s；v4负载前两轮152通过，第三轮151p+1fail。失败lease文件迟达44.040s、8次就绪探针0.485–2.656s未复现根因；未知底层调度/IO原因不归咎产品或环境。测试90s Event/自然join预算专项32p/19.25s。
3. 最新默认v2全量4417p/15s/14w/1391.19s自然0；最新v5三轮每轮152p、CPU中位100%、20worker全自然0，621源码相同。
4. PyInstaller EXE成功生成但首次验证器错用dist路径；失败未改候选、原日志保留。校验同输入预构建EXE、独占Core删除门后复用已验证bundle仅换EXE/base_library.zip；新Setup在全量/负载结束后独立编译，旧整包/Setup不删除。
5. 当前冻结四保留profile、新无DLC设置、生产Worker正常启动、源码/资源/6项hash审计与两路径性能实测完成。末次 Ruff check、119 个改动 Python format --check、diff check 均 exit0；报告纪律 59 passed in 0.66s；19 份 Markdown 的 424 个相对链接无断链/尾随空白。
6. 当前停止于用户验收交接，不运行真实Provider/屏幕或系统Setup/卸载。用户明确确认后再更新Phase5A正式收尾，未确认不预写完成。

<details>
<summary>历史R工程过程（截至最后恢复Core、非当前状态；保留失败与当时停点）</summary>

# R01–R07 当前权威状态（2026-10-08）：生命周期根因已修复，最新完整快照与交付验证进行中

> 本轮优先于下方 T01–T07 历史；Phase5A **未正式收尾**。默认原生门v1已通过，但租约测试协调预算更新后v2和三轮v5尚在执行；下方带时间的过程记录须按最新条目解读，不沿用旧分组/旧产物。

- 已实现 R01–R05：Core **4.2.2** 统一多服务 API、OS secure refs、显式预览/确认迁移、generic owner/purpose 端口；AI **1.0.2** / Screen **1.0.1** 要求 Core≥4.2.2。四聊天/文件/余额每新请求读取提交快照；自动策略与手动识屏生命周期分离；系统卸载只删 Core。没有官方 owner/factory 运行时特判、Core 内视觉回退或 Python 沙箱宣称。
- red→green 保留；最终修复专项 **106 passed / 10.53 s**，Qt/API/设置进程/真实 Worker 族 **71 passed / 12.27 s**。
- 全部 **4431 项 / 262 文件**按连续文件分成 5 个新 QApplication 进程，**4416 passed / 15 skipped / 14 warnings**，各 exit 0；nodeid 哈希并集完全等于完整收集，未使用 -k/deselect/新增 skip；621 份源码前后相同。pytest 合计 751.47 s，墙钟 755.708 s。证据 `repair-20261007/full-group-results.json`。
- 20 logical CPUs 的满负载三轮，均 **129 passed**，108.82 / 110.98 / 74.18 s，全机 CPU 中位均 **100.0%**。20 自有 worker Event 协作停止、自然 join 全 exit 0、残留 `[]`；v2 后两轮不足满负载未冒充通过。证据 `highload-v3-results.json`。
- **工程未决：默认单进程全量失败**。full2/full3/full4 均 Windows **0xC0000005**（631.93 / 640.48 / 651.15 s），Qt 嵌套循环/`app.exec`/WorkerSupervisor 清理循环；full4 约 79% 无断言失败。65 项最小组合及新增 17 文件 **226 passed /80.35 s** 的 owner 家族组合均未复现；根因待分类，不能归咎环境或截图，不继续盲目整套重试。
- 当前新 Core c09、Setup r422/setup-final、两 ZIP、非 synthetic Worker、probe-clean 与原生 helper 均重建；root207/stage212/resources1000/bundle2103/source 校验匹配，PE=2。哈希/输入见 `final-build-audit.json`；unsigned/manual-acceptance-only，不等于正式签名或系统安装通过。
- 生产 Worker 硬门：租约/HELLO/READY/自然退出 0/占用 free，**2739.376 ms**，零截图/联网。冻结 empty/AI/Screen/both 4 个自有 profile 维护/菜单/startup receipt/自然退出通过；无 DLC 冻结 Core 的 **AI 与对话→API 服务**真实 UIA 可见，**3169.543 ms**、退出 0。12 张仅自有 QWidget render 图已审查，不是用户屏幕截图。
- 性能已实测：metadata/version/request resolve 各 n1000；resolve 中位 **0.7001 ms / p95 1.3117 ms**（**不含 OS vault/Provider**）；显式保存 n50 中位 **48.193 ms**；3 订阅稳态约 **16 配置读/s**。3×5 s RSS 增量 **49,152 B**，原生线程 **5→7** 未归因，Python 线程始终 1；不声称长期零泄漏/零线程成本。证据 `api-performance.json` / 报告 §8.4。
- 无暂存/提交/推送/子智能体；分支 `codex/phase3-worker`、HEAD `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`。保留入轮 69 修改+9 新增和当前累计 109 修改+25 新增（134项）；源码测试均冻结，报告逐文件累计差异不能全算 R 新增。
- 两自有生成根上限 **11 GiB（11,811,160,064 B）**，保留旧产物，不删除。未读真实 Key/profile、未请求收费 Provider、未截图真实屏幕、未运行系统 Setup/卸载器、未改代理/VPN/系统电源策略、未清未知 staging、未强杀进程。

证据：[本轮报告 §8](../../docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-07.md#8-r01r07-人工缺陷修复与本次交付2026-10-07-开始2026-10-08-更新)；[设计](../../docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md)；[PLAN](PLAN.md) / [HANDOFF](HANDOFF.md) / [STATUS](STATUS.md) / [WORKLOG](WORKLOG.md) / [SUMMARY](SUMMARY.md)。

**人工门（完整步骤见报告 §8.7）**：先新 Setup 覆盖安装更新旧 `unins000.exe`；Core 配置一次 Key 并明确文字/文件/手动视觉/自动视觉/余额用途；验证 4 类旧窗口保存后无需重启、迁移/草稿/在飞授权、余额协议区别、自动关/白名单空时手动识屏；两来源 Core-only 卸载保留已导入副本/源 ZIP/配置/个人数据并重装识别；单包卸载只删安装副本。真实 Provider/屏幕/系统安装卸载待用户；**原生全量崩溃是工程责任，不交给用户验收来掩盖**。

<!-- R_CURRENT_FINAL_DOCS_START -->
末次全证据V2（2026-10-08 09:56）exit0：Ruff、117 Python format、diff --check、报告纪律59 passed /0.70 s；15份Markdown/306相对链接无断链/尾随空白，621源码冻结、6产物size/SHA匹配、2自有设置PID身份无残留。两生成根10,992,987,005 B（约10.2380 GiB）<11GiB；无暂存/删除，初始78dirty保护路径完整。证据 `repair-20261007/final-document-verification-current-v2.json`；状态为 `evidence_verified_with_default_native_gate_open`，**不是默认单进程全量通过或Phase5A关闭**。仅状态回填后以 `final-document-verification-closure.json` 留最终只读复核回执，产品/测试源码不变。
<!-- R_CURRENT_FINAL_DOCS_END -->

**实际效果与限制**：API 由 Core 管一次再授权所需 DLC；新请求不靠重启，手动识屏不被自动开关误停，系统卸载不先拆 DLC。模型/余额协议必须由服务支持；默认原生全量门与人工门均未关闭，不能宣称 Phase5A 完成。

## 最新动作与证据（2026-10-08）

- 完成5组全收集覆盖，按nodeid哈希校对4431并集，621源码未改。
- frozen empty/AI/Screen/both与无DLC API设置均真实运行并自然退出；早期自有UIA脚本把sidebar内部key当标题导致超时，修正为“AI 与对话”后成功，失败日志保留，没有产品补丁。
- 满负载v2只有首轮100%，后两轮42.x%，未算完成；只对20自有worker设单CPU affinity的v3三轮实际100%/129通过，Event自然退出无残留。
- API real JSON/CAS/Qt性能脚本结束：请求解析n1000、显式保存n50、poll n200和3×5s稳态，假MemoryVault边界显式排除OSvault/Provider；RSS/原生线程增量不美化。
- 报告§8更新负载、性能、真实profile、候选哈希、人工步骤与原生工程门；同组五记录更新，不创建平行计划。
- 产品/测试源冻结，有界Qt owner家族已226 passed /80.35 s；最终文档V1/V2 exit0，末次59报告纪律/0.70 s通过，306链接/冻结源码/哈希/预算均核验；源码没有变化。

<!-- R_NATIVE_PREFIX_DIAGNOSTIC_START -->
### 2026-10-08 默认单进程原生门：连续前缀与纯 Python 取证

- 最后只读 closure 已通过：59 报告测试、117 Python 格式、Ruff/diff、306 链接、621 冻结源码及 6 交付哈希（10:00:38，预算当时 10,993,038,307 B < 11 GiB）。
- 连续 120 文件 / 1717 项：**1710 passed / 7 skipped / 6 warnings / 516.78 s**，墙钟 517.810 s、exit 0、621 源码相同；不是默认全量原因确认。证据 `native-continuous-prefix-v1-results.json`。
- 扩大到完整收集前 **3600 项 / 220 文件**时，v2 在 `test_fs_watch_loop_survives_transient_runtime_error` 的 **诊断 record() 第20行**原生 AV（exit 3221225477，190.749 s），收集顺序哈希匹配、621 源码不变。该测试临时 monkeypatch `shiboken6.isValid=lambda obj:True`，诊断据此错误进入 C++ 指针读取；这是取证脚本无效证据，**不归因产品，不作为原始 full4 AV 的复现**。旧日志/trace/回执完整保留。
- 自有 QObject 有效性探针（不进入用户 profile）：删除前原始 guard true，删除后原始 guard false，临时替换后 guard true；不对已删除对象调用 getCppPointer，不处理全局 Qt 事件。证据 `diagnostic_validity_guard_probe.py`。
- **v3 完整单进程已通过**：4431项/262文件，**4416 passed / 15 skipped / 15 warnings / 898.65 s**（wall899.838 s，10:32:13–10:47:38 +08:00），收集顺序SHA与原inventory完全一致，621源码未改。hook只读Python字段/identity；不调用Qt/Shiboken原生方法、不推进事件、不改生命周期/收集/skip。证据 `native-full-python-v3-results.json`。两生成根当时 **11,617,865,458 B** <11GiB。
- 无诊断插件的完整 `pytest -q -o tmp_path_retention_policy=failed` **再次失败**：10:52:29–11:10:22 +08:00，wall1046.989 s、exit3221225477（0xC0000005），621源码不变。日志已报告3599项（3586点/13skip），最后报告位置按inventory哈希校准为 `test_shared_watcher_tick_survives_idle_windows`，崩溃在teardown `WorkerSupervisor.wait_for_stopped_for_tests:645`；当前位置不是遗留资源owner的证明。证据 `plain-full-retention-failed-v1-results.json`。pytest仅清理本次成功临时目录，保留新根50,859 B及所有旧产物；两根当时11,617,936,845 B <11GiB。
- 自有pytest故障的Windows Application/1000记录指向 Qt6Core6.11.1 RVA `0xb2ff8`；本机PE导出/反汇编定位为 **QEventLoop::exec+0x28 的进入段读取**，而非已确认派发旧Qt事件。此前缺少寄存器/owner证据，不能据栈直接归因；下面入口取证现已捕获NULL线程字段，owner仍待定位。`tests/test_single_process_shared.py` 单独无插件-q **17 passed in3.57s**。
- 只读入口取证 `qt-entry-prefix-v1` **复现同一原生异常**：11:19:51–11:35:31 +08:00，907.564 s、exit3221225477，前3600项/220文件、621源码不变。在目标用例 teardown 的原始 exec 调用前，fresh loop 原始有效性=True，主线程 QThreadData 的 thread 字段已为 **NULL（ReadProcessMemory成功）**；此前同一数据地址的 thread 字段非NULL。这与Qt6Core RVA0xb2ff8入口读取吻合，排除了“已确认由排队事件派发导致”的旧猜测，但尚未证明谁销毁/清空主QThread。两根11,618,225,425 B <11GiB。证据 `qt-entry-prefix-v1-results.json` / `native-qt-entry-prefix-v1.trace.jsonl`。
- `qt-phase-prefix-v1` 已完成（11:40:17–11:52:10 +08:00）：684.519 s、exit3221225477、621源码不变。只读标量首次非NULL→NULL出现在完整收集第 **3139 项** `test_requested_regressions::test_modern_settings_save_warns_on_failure` 的 **call 前后**；同一主线程QThreadData一直保持该NULL，后续第3599项共享Worker清理入口才崩溃。该边界是定位线索，不证明当前用例就是错误owner。两根11,618,397,923 B。
- 仅 requested regressions + shared触发用例两次诊断均通过：43 passed /1 skipped（27.41 s；加非持有 destroyed观察后29.84 s），不能替代默认全量，也未复现前序污染。
- `qt-owner-prefix-v1` 被用户中断：无完成回执，日志停于46%并有两个F标记，未取得失败详情；不分类为产品新失败/通过/原生复现，保留原日志与新测试临时根。工具会话已不可重新加入；GDB进程名限定查询无残留。
- `qt-destroyed-prefix-v2` 已完成：13:44:06–13:53:47 +08:00，前3145项 **3133 passed /12 skipped /1286 deselected /10 warnings /514.28 s**，wall519.093 s、exit0、621源码不变。主线程字段未变NULL，弱 destroyed/GC观察可能改变绑定或时序，因此不作为根因修复或默认全量门。进程正常退出时 app.destroyed 回调尝试写已关闭句柄，产生诊断 ValueError，明确是观察器收尾缺陷，非产品失败；原记录保留。两根11,618,689,763 B。
- 此前在新自有pytest子进程执行、现已结束的 `qt-native-watch-prefix-v1`（前3600项），使用不连接Qt信号的只读phase入口记录与一个原生硬件观察点，捕获谁将主线程字段写NULL。GDB仅配置自有子进程调试寄存器，不写Qt对象/改变删除/刷事件，不附加用户进程。GDB退出码不等于pytest通过；必须读取原生写入位置、完整测试结果。独立自然退出smoke已证实观察点可捕获 QThread 析构的非NULL→NULL写入，退出0；两次工具路径/嵌入Python环境失败保留。
- 2026-10-08 原生硬件观察已完成：`qt-native-watch-prefix-v1-results.json` 记录 1097.382s、621 源码指纹未变、占用 11,618,873,756B。自有 pytest inferior 在 `test_modern_settings_save_warns_on_failure` call 内由 Python GC → `SbkDeallocWrapper` → QtCore.pyd 虚析构派发 → `QThread::~QThread` 清零主线程数据字段（写入 RVA `0x1a49b3`）；随后自然退出 `0xC0000005`。GDB exit 0 不是 pytest PASS。精确调用栈与已证明/未证明边界写入 `qt-native-write-evidence-v1.json`；尚需查明错误销毁权的来源，不以保活引用掩盖。
- `qt-gc-owner-prefix-v1` 已完成：14:26:07–14:38:06 +08:00，前3139项 **3127 passed /12 skipped /1292 deselected /10 warnings /599.86 s**，wall601.880 s，621源码相同。观察到已存在主线程 wrapper 在 GC start 仍为 `ownedByPython=False`，referrers 含 QApplication；没有 NULL 写入。GC 枚举临时引用可能影响时序/回收，**不能作为根因排除或全量通过**，不据此加保活补丁。两根11,621,407,906 B。
- 当前执行新自有进程 `run_qt_owner_scalar_watch_prefix_v1.py`：本机已核实 `BindingManager::retrieveWrapper` C++ header/export，以 `ctypes.PyDLL` 在 GIL 内读取借用 raw pointer，只保留整数；不枚举 GC、不生成/持有 Qt wrapper、不连接信号、不刷事件。首次找到已有主线程 wrapper 后，在自有 GDB inferior 上同时硬件监视所有权低位与主线程 NULL 写入，读取原生调用栈与真实 wrapper 类型。前3139项为诊断，不是全量门；session55264。
<!-- R_NATIVE_PREFIX_DIAGNOSTIC_END -->

- 2026-10-08 15:10 原生门根因已缩小：公开 QObject/thread 创建顺序最小复现 3×3 次 0xC0000005，正常 parent/child 对照 3 次 exit0；真实 FeatureLifecycleEndpoint.register_draft + QueuedFeatureLifecycle.prepare 的循环回调 seam 连续 3 次崩溃（`qt-feature-lifecycle-draft-public-minimal-v1-results.json`）。主线程 wrapper 的 QObject.thread() Python parent heuristic 与 GC clear/dealloc 交互是已证明机制；当前计划先补独立进程 red，再将该 GUI 防自等待判断改为 endpoint 创建时的 Python owner-thread 身份（与 FeatureHost.registry 同一约束），保留 queued signal/后台 wait/撤权顺序，不用全局 keeper、private bit 写入、全局刷事件或新 skip。Shiboken 6.11.2 单库混合诊断仍 3 次崩溃，不作为受支持升级或交付；全局 Python 环境未改。scalar-watch-v1 已自然结束，3127p/12s/1292d/10w/1226.29s 后 inferior 0xC0000005，GDB exit0 不是 PASS。准备的 v2 大前缀不再作为下一步。

- 2026-10-08 15:12 生命周期 public-seam 回归完成 red→green：独立子进程的循环 draft 收集测试原版 exit0xC0000005（red 1 failed /6.63s），改为 endpoint 创建时 `threading.get_ident()` 后，真实 GUI/background/IPC/shared Worker 族 **23 passed /8.48s、自然 exit0**；回归 3 个创建/GC/新 QEventLoop 轮次都实际收集 Endpoint，未加主线程 keeper。初次 green-v1 文件名选择错误、green-v2 CRLF 拼接未命中造成 NameError，日志保留，均未冒充通过；正确实现为 green-v3。无 Qt 包升级。下一步执行完整未加诊断插件的 4432 项单进程 -q，全量完成前不修改产品/测试输入；随后 Core/Setup 精确受影响重建与3轮满 CPU 族。


### 2026-10-08 15:29 默认单进程硬门已修复；三轮高负载与当前产物收尾进行中

- 新增真实公开生命周期 seam 的失败回归先 red（0xC0000005），修复 `QObject.thread()` 对借用主线程包装器的误归属风险；以创建线程 identity 守卫，不引入 Qt 全局 keepalive、事件冲刷、依赖升级或额外 skip。专项 23 passed /8.48 s、自然 exit0。
- 当前无诊断插件、无选文件的默认单进程完整 `pytest -q`：**4417 passed /15 skipped /15 warnings /739.87 s**（wall741.365 s；15:13:12–15:26:00 +08:00），自然 exit0，621源码前后不变。证据 `plain-full-lifecycle-fixed-v1-results.json`；新增1回归后共4432项。旧 AV 和中间脚本错误均保留，历史分组通过不再作为当前默认门替代。
- 正在执行 `highload_repair_v4.py`：20 logical CPU，原129项加23项生命周期/IPC/共享清理，3轮各152项；第一轮已 exit0，CPU中位100%。其余未完成前不预写通过。
- 用户已授权约5g生成空间扩展；最终只重建受影响Core/Setup，复核并复用未变Worker/ZIP/helper。硬上限调整为两生成根合计 **11.25 GiB（12,079,595,520 B）**；预计新增约0.3GiB，仅更新自有c09候选，完整旧Core和setup-final保持不变。持有CoreCodeGate独占删除/替换门，不强杀；新Setup写setup-lifecycle，不运行系统安装器。
- 当前工程下一步：三轮自然退出→增量重建/当前6产物审计→4个已有自有profile保留包识别和新的无DLC设置→真实守卫性能/最终全证据复核。人工门仍为真实Provider、屏幕、新Setup安装卸载；Phase5A未关闭。


### 2026-10-08 15:48 高负载失败已分析，测试协调预算修正后重新完整验证

- v4前两轮152通过/100%CPU；第三轮151通过、1项既有租约测试20s ready预算超时，**三轮门未通过**。失败根保留的lease显示状态提交→子进程实际取得lease44.039805s，该PID已自然退出；不能将一次延迟归因Provider或租约逻辑坏。独立8次同一真实target满CPU启动0.485–2.656s、全exit0，没有复现迟达；底层调度/IO延迟仍未归因。
- 测试层保持Event/所有占用与退出断言，协调预算90s、hold release180s；给两处无finally的ready路径补自然join。**未修改产品租约时限，不加入skip、不强杀**。相关32 passed /19.25s；Ruff/format通过。证据 `lease-ready-failure-timeline.json` / `lease-ready-latency-v1-results.json` / `R06-lease-budget-focused-v1.log`，v4失败保留。
- 因测试源码有变化，重新默认全量v2，随后满CPU三轮v5；旧v1默认全量绿色不冒充最新快照。Core增量构建可与默认测试并行，但Setup编译严格留到全量/负载自然完成后，避免生成峰值越过11.25GiB；旧完整Core/Setup保留。



### 2026-10-08 16:05 受影响 Core 增量重建验证完成，Setup 等待全量与负载退出

- PyInstaller 已完成分析/PYZ/EXE，但自有验证驱动把 exclude_binaries=True 的 EXE 输出误认为 dist；实际在 build/validation。失败退出与日志保留（lifecycle-core-build-failed-v1.json），该次未修改候选。修正取证路径，重新生成仅源码的闭合输入、核对212 staged/207 root和旧资源/完整2103文件，再复用已成功生成的EXE，不重复构建或删除旧包。
- 受限恢复持有CoreCodeGate独占门，实际仅替换自有c09的EXE和base_library.zip；旧完整core-final bundle逐字节保留。最新Core SHA256为1413d5ef5aeb43de9aa693c3d7060a5849fa9b1c7ae8902c24a6c511a11c7264，17292328B；PYZ2188、GUI PE=2，已包含_owner_thread_id修复、不含旧QThread借用守卫。恢复exit0，107.075s；初次PyInstaller日志205.246s为工具自身耗时。
- 当前默认全量v2仍自然运行，随后highload-v5三轮152项；新Setup不与测试生成峰值重叠。两生成根此时11,692,079,894B<11.25GiB。当前receipt状态core_rebuilt_setup_pending，不预写Setup或最终门通过。

</details>
<!-- CURRENT_R_END -->

---

# 原 T01–T07 历史状态（2026-10-07，不代表当前 R 修复通过）

> 以下是本轮代码实施的最新实测；分隔线后的早期停点/文档清理记录按当时状态保留。

## 当前判断与范围

T01–T07 本轮授权范围内实现、最终全量、满负载三轮、当前构建、授权实机、性能与报告/索引/五份终态留档已完成。不开子智能体，不进行真实系统安装、Provider、用户数据或 Git 发布。

## 实际命令、预期与结果

- T01 产品修改前：`4 failed in 5.24s`；中性注册/路由、Setup 内嵌、GUI spec 均先红后绿。
- 最终全量：`4334 passed, 15 skipped, 15 warnings in 759.16s`，exit 0；原始日志 `implementation-20261007/pytest-final-current.log`。首次完整的 17 个旧合同预期失败已分开记录并修正，不跳过测试；最终全量后没有产品/测试源码改动。
- 20 CPU worker 满负载三轮相关族：各 `97 passed`，pytest 200.99s / 182.02s / 177.87s；全机 CPU 中位均为 100.0%。20 worker 协作停止、自然 join，全 exit 0、残留 []。
- 最终 Ruff、63 个改动 Python format --check、git diff --check、当前 Core/source/resource 审计均 exit 0；保留 2395 行预算，现代设置实际 2377 行。
- 当前 Core root201/stage206/resources1021/bundle2126 均匹配，PE subsystem=2；helper 与非 synthetic Worker 输入核验。

- 冻结 empty/AI/Screen/both 四种自有 profile：无界面维护（empty 不执行）→正常 GUI Core→startup receipt 清除→对应菜单出现/缺席→自然退出 0；不冒充 Inno Setup 四种人工勾选。
- third-party.example：真冻结 LPAC helper 的目录/ZIP 安装、真实双进程启动/重启、停用/卸载全过。额外当前冻结 Core ZIP 首次启动/重启均通过，revision=4、pending=null；自然退出后停用/卸载 completed、revision=7、versions={}，没有模拟热卸载。
- 默认创建参数（creationflags=0）下自有进程树 62 次观测无可见终端，ffmpeg ConsoleWindowClass visible=false；自然菜单退出 0。不等同 Explorer 人工双击。
- 原生 UI 的 720/1100、浅/深色四张截图已审查；ZIP/目录入口可见可用、横向滚动 0；宽布局滚动遮罩略裁节标题是已记限制。
- 路由/完整验证 50 样本及 empty/third 两组 30s settle 后 3×5s Core PID 数据已落报告；third 15.043481s 的 RSS 净 +892928 B，只是短样本，不能据此宣称长期零泄漏/可归因性能改善。

- `python .../final_code_verification.py` → Ruff 0.127785s、format 0.1552643s、git diff --check 0.1639295s、构建审计 3.8821307s，四项 exit 0。
- 高负载驱动全链 212.2985937s / 184.4014972s / 189.5171436s；CPU 均值 99.9972067 / 99.9993506 / 99.9993590%，性能采样在负载之前完成。
- 失败分类保留：17 个旧合同预期、行数门、pending 重入、长路径、旧 Worker 输入、stdout 编码、自有 Config/窗口探针假设；报告逐项说明，不假称未执行门失败。

## 最终持久化与交接

已更新本轮 PR 报告、INDEX 与原五份记录；最终文件表含 69 修改 + 9 新增、无删除、无暂存，明确区分前轮已有脏改动，不全算本轮新写。
<!-- FINAL_DOCS_VERIFICATION -->
最后新报告专项 `59 passed`，exit 0；13 份相关 Markdown 的 267 个相对链接无断链、无尾随空白。累计 69 修改 + 9 新增的 78 项文件说明/行数已核对，无删除、无暂存；20 负载 worker 全 exit 0、残留 []，5 个已记录自有进程身份复核无残留，未枚举/操控其他用户进程。结果 `implementation-20261007/final-document-verification.json`。 新报告首轮专项 `59 passed in 0.58s`（最末次留档复查见结果 JSON），无失败。
<!-- FINAL_DOCS_VERIFICATION_END -->

无新增确认项；本轮授权范围无剩余执行，真实系统安装/Provider/用户确认/正式发布另获授权。

## 实际使用效果与限制

ZIP/目录按包自身注册走统一生命周期，Setup 已内嵌两个官方包，Core 为 GUI PE；Python 不是沙箱，已加载版本等待进程自然退出，构建和测试不能冒充真实系统安装或人工发布门。

---

## 以下为历史过程记录，保留追溯，不代表当前停点

## 2026-10-07 自主收口：当前构建与实机证据

- 109 项相关集合绿；完整套件 17 failed/4317 passed/15 skipped 暴露旧焦点和官方-only scope 测试合同，按新合同补断言后 65 passed in48.37s，最终全量复跑中。
- 当前 Core 构建 142.5049351s；PE=2，helper 固定 digest；旧 Worker 校验因源码变化拒绝后重建当前 Worker，官方 AI/Screen ZIP 重新组装。
- 首次 ISCC 压缩 311 字符路径失败；只将本轮新 Core bundle 移到 c07，源/目标边界确认且 2126 文件哈希/大小/路径清单全相同，最长 255；Setup 编译退出0，未运行系统安装器。
- 独立自有资料中 frozen empty/AI/Screen/both 全通过，维护无可见窗，首次 Core receipt pending 清除，菜单按 owner 分别出现/缺席，全部自然退出0。
- 未知第三方目录/ZIP 使用真实冻结 LPAC helper，真实双进程启动/重启及停用/卸载通过；首次错误把 binding.close 当成租约释放，实际 awaiting_release 证明守卫有效，改由自然进程退出再完成，不改产品 lease 语义。
- 原生 UI 重抓四张，逐一审查；输出 cp1252 编码失败归为诊断 stdout 环境问题，PYTHONIOENCODING=utf-8 重跑退出0。
- 50 样本路由/验证性能已记录；只读构建审计 root201/stage206/resources1021/bundle2126 全匹配。合并自有 profile 后生成物 6,264,876,280 B，仍在6 GiB内。
- 下一步：等最终全量、冻结第三方/稳态补测、高负载相关3遍、正式报告及最终五记录。

## 2026-10-07 续接实现与自主收口

- 续接独立导入红 1 failed,1 passed in4.38s；补入口后96 passed in101.36s。
- 全量 -x 揭示架构行数2455>2395（1 failed,373 passed,1 skipped）；抽 UI 组装到 feature_management_ui，2377行，不修改预算。
- 维护重复安装红1 failed in1.84s；修 awaiting_startup_confirmation 的合法重入及 StateResult.state 读取，专项75 passed in78.61s。
- 扩展页所属域红1 failed in1.22s，增加 capability claim；深链接红1 failed in0.99s，聚焦统一 ZIP 导入。相关109项中旧焦点断言24失败，已同步新合同待重跑。
- 拉取已固定 SHA-256 的 libsodium1.0.22官方归档到自有 inputs，当前 helper 构建成功，Core 正在构建；尚未新 Setup、实机安装或 Git 发布。
- 当前停点和目标按同目录 PLAN/STATUS/HANDOFF/SUMMARY 当前段；用户已授权自主运行至T07。

# 2026-10-07 当前施工记录：Phase5A 文档与 `.scratch` 清理


## 2026-10-07 T01 红测试（本轮实现起点）

1. **保护与阅读**：按用户指定顺序阅读项目入口、Phase5A 状态/计划/交接/摘要、closeout implementation plan 和 code implementation handoff；核对 dirty worktree，未重置、未覆盖、未提交、未推送。
2. **失败回归**：新增 `tests/test_phase5a_t01_regressions.py`，固定四个新合同：unknown owner local v2 verify/bind；AI ZIP/目录从同一入口按 manifest registration 路由；Setup 嵌入官方 ZIP 且不读取 `{src}\packages`；Core 生成 spec 使用 `console=False`。
3. **红灯证据**：`$env:QT_QPA_PLATFORM='offscreen'; python -m pytest -q tests/test_phase5a_t01_regressions.py` → **4 failed in 5.24s**。失败分别为 official owner 硬校验、router 缺失、Setup 外部 packages 路径、生成 spec `console=True`。
4. **下一步**：进入 T02/T03，先修改中性 registration 与 local/official verifier 分支，再贯通 descriptor 驱动的 transaction/state/lease/startup/loader；不把红测试改回旧 hardcoded contract。


> 这是本轮可复用的过程记录；旧施工记录保留在下方。没有把失败调试原文、密钥、用户数据或生成物复制进文档。

## 本轮目标与范围

- 目标：把用户确认的“Setup 官方选装 + Setup 外本地第三方 DLC”计划落盘，重写相互矛盾的 Phase5A/Phase6 说明，并清理占空间的过时 `.scratch` 构建物。
- 范围：文档与 `.scratch/phase5a-local-distribution` 清理。
- 明确不做：产品源码、测试源码、构建脚本、Core/Setup 重建、真实安装/导入、真实 Provider 请求、提交/推送。

## 实际步骤与结果（2026-10-07）

1. **读取入口与规则**：确认工程模式、项目入口、文档索引、计划/交接模板和 Phase5A 当前记录；确认工作树已有大量用户/前轮脏改动，采取保留策略。
2. **盘点清理范围**：以 `.scratch/phase5a-local-distribution` 为唯一清理根，统计清理前 `53,298` 个文件、`9,876,078,289 B`；只选择已确认未被当前文档/阶段记录引用、且明显属于旧构建/诊断轮次的目录。
3. **先验证再删除**：一次批量 `Remove-Item` 被安全策略阻止，未发生删除；随后改用显式目标清单，逐项确认解析路径位于 Phase5A 根内、不是符号链接/重解析点后执行清理。
4. **清理结果**：删除 38 个目录，实删 `9,404` 个文件，释放 `4,905,969,506 B`；清理后复核为 `43,894` 个文件、`4,970,108,783 B`（约 `4.629 GiB`）。
5. **保护复核**：确认三组最终验收 build、三个交付/发布证据根、Setup 短路径产物、五份阶段记录仍存在；未触碰 `.scratch/phase4b-local-management`、其他阶段、源码和真实用户数据。
6. **计划/交付文档**：创建 `docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md` 与 `docs/PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md`；同步项目入口、Phase5A/Phase6、索引和 PR 报告。
7. **阶段记录**：在 `PLAN.md`、`STATUS.md`、`HANDOFF.md`、`WORKLOG.md`、`SUMMARY.md` 顶部写入当前权威停点，保留旧记录以便追溯。

## 验证状态

- 文档差异：`git diff --check -- docs .scratch` 无 whitespace/error；Git 仅提示工作树 LF 在未来被 Git 接触时可能转为 CRLF。
- 报告纪律：`python -m pytest -q tests/test_pr_report_discipline.py` → **57 passed in 3.15s**。
- 文档结构：13 份本轮相关 Markdown 均存在、无尾随空白；相对链接检查（含 URL 解码）→ **0 个断链**。
- 清理后即时基线：`43,894` 个文件、`4,970,108,783 B`（约 `4.629 GiB`）；末次只读重算因保留目录/记录后续变化为 `43,901` 个文件、`4,970,128,950 B`（约 `4.629 GiB`），本轮未追加删除。
- 代码测试：本轮未运行，不能宣称新实现通过。
- 构建/实机：本轮未执行；旧构建只作历史材料。

## 下一步与准确停点

新对话先读 `docs/PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md` 和本目录 `HANDOFF.md`，核对 `git status --short`，再按 T01–T07 实施。当前记录到此停止，不在本轮修改代码。

---

## 当前行动：无人值守剩余验收（2026-10-06 本机时间）

用户已授权休息期间独立测试、修复和自验，所有人工／敏感确认统一留到最终汇报。生成物临时合计上限 **12 GiB**；保留 Core 2 GiB、probe 512 MiB 预留，跨盘合并计量；旧清理拒绝不绕过。无提交、推送、发布、子智能体。

### 同一轮执行顺序与验收门
- [x] V0：核对原 WIP、保护范围、材料，创建仅源码／文档白名单基线快照与自有目录。
- [ ] V1：仅 AI 菜单／自然退出根因；驱动问题只修驱动，产品问题先红后绿；不盲点击、不强退。
- [ ] V2：官方冻结四组合、Core 自确认、双包启停／卸载／重装、并发及恢复；验证构建的合成截图和故障单列。
- [ ] V3：受影响生产链重新构建／审计；若正式重新签名需密码则留待人工，不保留密码或弱化信任。
- [ ] V4：Setup 静态／生成边界与可独立验证的安装合同；新 Windows 用户真实安装／更新／卸载需用户登录，留到最后。
- [ ] V5：普通 ZIP、NTFS 便携及生成数据导入／中断；仅操作本轮自有目录，真实数据不自动导入。
- [ ] V6：专项、相关、全量／静态／高负载、性能与报告；不把历史通过变成新通过。

正式产物用于真实启动、UI、factory、receipt、租约和事务；必要验证边界明确标记。所有证据记录 passed／failed／blocked／not_run 及材料摘要。UI 操作必须绑定 PID＋创建时间＋exe＋自有根，不沿用旧 PID。只自然退出本次启动的应用。

人工留项：新账户创建／登录、正式密钥解锁／加密备份目标确认、真实 Setup 目标确认、真实模型／截图观察与干净环境效果；不在夜间等待用户回复。

当前停点：开始读现有冻结驱动与公开 Qt seam，未启动真实安装／导入／密钥操作。旧证据仍以其日期／摘要为准。精确证据：`acceptance-night-20261006/ownership.json` 与白名单 `wip-baseline.zip`，不含生成物或私钥。

### 完成后的实际使用效果
补齐双包本地交付可复验的证据和实际缺陷；人工门仍明确待确认，不宣称正式发布、Authenticode 或全新系统已验收。

# Phase 5A 工作记录

## 2026-10-04 5A-0

用户批准完整计划；复核 HEAD e2687be、工作树干净。读取工程协议与交接，持久化设计和同组五记录。白名单ZIP只含将涉及的源码/测试/文档，不含程序、用户数据或秘密。没有读取真实密钥、没有删除、没有提交。

下一步5A-1测试先行；当前不宣称运行能力。

## 2026-10-04 基础合同与公开 seam 红绿

2026-10-04；codex/phase3-worker；基线 e2687be，所有实现仍为未提交 WIP。5A-0 已完成；5A-1/5A-2 仅基础合同部分实现，不能勾选整阶段完成。

已实现：固定双官方身份、manifest v2 与签名 scope、AI host-only（明确不适用 Worker，不伪造握手）、独立账本/租约/事务 owner、真实加载 receipt 的 AI 端口合同、Core 短暂管理锁竞争的有界自动重试。RuntimeLayout、NTFS 标记、稳定数据根与凭据命名空间已接入通用配置/资源入口。

最近验证：基础 81 passed / 32.72s；AI 真实子进程加载新增 3 passed / 1.71s；RuntimeLayout/配置端口/屏幕配置 62 passed / 5.34s；管理/启动/双包合同 45 passed / 27.29s。31 个受影响 Python 文件 Ruff check 通过、format 已执行。以上命令均使用 offscreen 和本任务 E 盘 basetemp；不等于全量验证或冻结验收。

尚未完成：应用/独立设置双 owner 接线、导入事务、AI 实现物理拆包、线程排空、生产信任锚与构建、Setup、双包管理 UI、冻结及人工/干净环境验收。UX-M3 已有锁竞争公开 seam 红绿证据，但不能断言就是历史故障原因，必须以新冻结 Core 验收。

约束：6 GiB 生成峰值；保护旧人工程序、profile/凭据；真实密钥/数据导入/安装卸载另行确认；不提交、不推送、不发布、不使用子智能体。

失败经验：首次 registry 改写遗漏 SCREEN_OWNER/default_feature_host，导致 40 个回归；恢复原公开 API，而不是放宽验证。Worker 的旧夹具缺 descriptor 字段，改为闭合 owner 注册表判断执行类型。探针父端旧 7 字段策略仅明确兼容旧屏幕路径。RuntimeLayout 的 StateError 使用 .code，不猜 .reason。首次编辑未指定 UTF-8 导致解码失败、未修改产品；后续所有 Python 编辑明确 UTF-8。

Core 锁竞争测试修改前 pending 保持不变（1 failed /13.13s），修改后事件循环真实重试通过（1 passed /1.52s），不借助普通设置或 UI 成功回执。AI receipt 使用实际子进程 factory 与 host lease，但 factory 为生成夹具，未代表产品 AI 实现。

## 2026-10-04 后续切片：确认恢复、Core 双包与导入
- 真实内核锁故障注入复现 receipt 已提交后授权读取失败误入加载失败；修为保留 sealed receipt 和版本 pin，重试不导入代码。
- Core 双包入口/关闭公开 seam 先 ImportError 红，后管理/启动/双包 49 passed / 29.19s。
- 导入合同缺模块 13 failed；实现后边界新增发现迟到编辑覆盖与取消入口缺失（2 failed / 2 passed）。修目标最终 CAS 和仅取消未接受 owned staging；导入/布局 44 passed / 3.48s。
- 新增 AI 生命周期四项失败测试；实施异步退出门之前不删除 GUI wait，避免线程销毁回归。尚未物理拆包、构建或执行真实用户迁移。

## 2026-10-04 请求、会话排空与 Core 依赖边界
- 四项生命周期公开 seam 先 4 failed；实现异步 Quit 门、非 GUI response.close/线程 join、request_id + generation 验证后 4 passed /4.65s。两项旧测试错误假设 stop 即同步排空，修为真实事件循环等待，不放宽产品线程边界。
- 请求/导入等 30 passed /26.19s；UUID 冲突先红，修 staging 创建拥有证据后转绿。
- 余额通过禁止 AI 导入的 seam 先红；改用已有 pet.http_compat 后 AI/余额相关 256 passed、1 skipped /68.07s。
- 会话排空缺 API 先 2 failed；新增后台排空、全 root 写入冻结及失败保留后新会话/请求专项 28 passed /5.32s。会话异常关闭/重叠恢复及真实 host 接线尚未完成。
- 最新改动尚未全量/lint/冻结验收；没有真实密钥、秘密、安装、系统改动、提交或推送。

## 2026-10-04 连续实施补充
会话关闭异常与重叠恢复专项已转绿（30 passed /5.73s）；双包管理 UI 已接入独立 owner、结果与草稿隔离（54 passed /92.63s）。AI 实现源码已迁至 features/ai_chat/host，旧模块仅保留明确 BUILTIN_AI 门控的兼容别名，不移动旧冻结程序或数据。迁移及相关专项 53 passed /1 deselected /8.50s；被排除的是新增 lazy factory 合同，尚未实现，不能标记完整拆包或生产可用。
下一步：实现真实惰性 factory、owner 配置/凭据适配和请求/会话生命周期；运行 Ruff 与相关回归。正式密钥、生产构建、Setup、实际导入/人工/干净环境和完整质量门仍未执行。

## 2026-10-04 当前追加进展（AI host/设置接入）
已实现 Qt 无关的惰性 AI factory、owner 配置 CAS/安全凭据适配、真实请求/会话生命周期和普通设置贡献挂载；小 Core 禁止旧源码回退。独立设置子进程的原生退出定位为测试未关闭调用方拥有的管理监控线程，补真实关闭后 5 passed /2.75s，不改产品所有权合同。
新增四个失败回归（4 failed /4 passed /1.20s）后修正：重挂设置行不重建 QObject、聊天/文件设置一次 CAS、停用 AI 的新输入 Key 不能绕过测试授权、延迟布局贴底且保留用户上翻。当前专项 15 passed /6.79s。
相关累计回归曾为 282 passed、1 skipped、1 failed /352.55s，失败为聊天贴底，现 focused 转绿；该相关族仍需重新全跑。实际 AI 全窗口/快捷/灵动岛和文件理解路由、正式签名/构建/Setup、人工和干净环境仍未完成。正在补写盘排空中重新启用及共享排空恢复回归；全量、mypy、高负载和冻结验收尚未执行。
无新增实际密钥/真实导入/真实安装/卸载；全部未提交 WIP，保护范围和 6 GiB 上限不变。

## 2026-10-05 连续实现与全量失败复盘

- AI 全窗口无效 QObject、可撤销 finished 订阅、小 Core 退出、文件偏好重载：7 个公开 seam 先失败 /1.19s 后通过 /1.13s；相关 241 passed、1 skipped /40.10s。根排空/重启 59 passed /6.38s，实际路由 94 passed /7.37s。
- 离线签名、分发、密码 GUI 工具仅生成夹具密钥；签名 8 passed /1.05s，分发先 7 failed 后 combined 15 passed /1.86s，CLI 先 4 failed 后 combined 19 passed /1.93s。真实签名 CLI 新回归暴露 descriptor 字段错误，修正 id/raw_manifest SHA-256。
- pytest-full-17 全量 6 failed /3988 passed /13 skipped /15 warnings /759.94s；不是通过。真实六失败隔离重现 6 failed /1.34s。另一次错误解析测试索引得到 4 passed /1.09s 不属于失败复现证据。
- 六失败根因：现代设置文件行预算、聊天迁移后旧路径断言、已有 README Git ref 被品牌误判、新长页面原生滚动条造成标题偏移、旧无聊天变体缺 pet.chat。保持预算与品牌禁令，通过生命周期机械提取、读取实际 owner 源码、Git ref 窄豁免、viewport 布局与明确缺模块处理修复。
- focused 与签名相关 55 passed /3.52s，相关累计 213 passed /104.70s。全量未复跑；Ruff/format 当时 106 个 Python 文件通过，后续新增测试待重新检查。
- 生产材料 seam：10 failed /1.09s，scripts/feature_release_materials.py 尚未实现。无生产冻结/正式 key/实际导入安装卸载；五记录同步准确停点。

## 2026-10-05T00:58:51+08:00 材料、opaque 配置与余额独立边界

更新时间：2026-10-05T00:58:51+08:00（本机时间）。基线 2026-10-04 / codex/phase3-worker / e2687be，均为未提交 WIP。无提交、推送、发布或子智能体。

## 当前事实与证据
- 固定双 owner、v2/host-only、独立账本/租约/receipt 和 Core 启动恢复已有源码 seam；新冻结 Core 尚未验证。
- unsigned 材料与签名/分发工具：pytest-materials-green-27，34 passed /6.10s。材料有固定来源、独占目标、同次拥有范围和 6 GiB 预算，未执行代码。
- 小 Core 的旧 AI 配置保持 opaque；AI 策略迁入 owner：pytest-opaque-green-31，91 passed /4.31s。
- 余额独立 vault/CAS/请求配置：pytest-balance-green-34，26 passed /0.96s；实际 General 余额授权、异步保存、窗口先关闭、退出排空及相关族：pytest-balance-ui-green-37，73 passed /22.39s。
- AgentLink 余额路由不借 AI；AI 内置背景从版本-owned resources 读取：pytest-owner-routes-green-40，24 passed /0.97s。误写不存在 test_chat_theme.py 的命令只得到 no-tests-run，不计通过；真实文件为 test_chat_themes.py。
- pytest-full-17 历史：6 failed /3988 passed /13 skipped /759.94s；六项 seam 修复与相关族转绿。新的全量 pytest-full-41 正在运行（会话 35548），尚无最终结果。
- Ruff --fix 已修 6 项；format 9 reformatted /108 unchanged，当前白名单117个 Python 文件；仍需后续最终 check。

## 未完成与硬门
5A-1/2/3/5 与 5B-1 均仅部分完成。正式信任锚为空、probe 摘要未生成；没有新生产冻结 helper/Worker/Core/DLC/Setup。新的 Setup/更新/卸载前置门、授权凭据/托管附件导入及 UI、完整布局、mypy/高负载/性能、实机安装/人工/干净环境均未完成。不能宣称 Phase 5A/5B-1 完成。

## 保护与授权
保留 .scratch/phase4b-local-management/manual-core-03 与 manual-session-01/no-chat/APPDATA、旧产物、真实 profile/凭据及其他任务。峰值6 GiB，构建前重新计量。未生成正式私钥、未导入真实数据、未真实安装/卸载；这些目标操作须再次确认。生成夹具和测试密钥不是生产证明。

余额 UI 红测6 failed；初次绿测3 failed12 passed来自测试 getter 与安全存储 envelope 断言，修正测试并补 job finally 排空后73 passed。背景/AgentLink路由红测2 failed再24 passed。真实秘密未读取。


## 当前停点 2026-10-05T01:50:31+08:00（本机观测时间）

- 基线 e2687be / codex/phase3-worker；所有新修改仍为 WIP，没有提交、推送或发布。
- 全量快照 pytest-full-41 已回收：4034 passed /13 skipped /14 warnings /934.25s。其后新增构建器及类型修复未覆盖在该次全量中，不能当最终门。
- 新 scripts/build_feature_release.py 实现可信 probe、冻结 Worker、正式小 Core 的闭合材料/源码摘要/独占输出/预算/实际 PYZ 与原生依赖审计。专项红绿见42～48、60～61日志；最新 builder 12 passed /3.02s。
- 在本机实际重建 native-01、Worker worker-01、helper probe-02。helper bundle SHA256 f0445bad4ef722c23f6c75038d103d57aa9f9bcdfd7356bc82d79894967b40f3；没有生成正式私钥或生产 Core。
- 两包真实 LPAC 自检通过（two-owner-lpac-63.log）：screen host + Worker HELLO/SHUTDOWN/优雅退出；AI host-only，Worker 明确 not_applicable。仅内存生成的集成测试密钥，不是正式签名验收。
- 失败复盘：probe-01 未包含公共 feature_ports，真实两包 factory 加载失败。先补公开材料测试（red60），加入公共 SDK 后绿61并重建 probe-02，真实重试成功；未扩大 ACL 或放宽 Win32k/Job/LPAC。
- probe-01 的原生权限和崩溃/超时/输出/内存/父退出清理矩阵通过；probe-02 的同矩阵正在 native-canary-64.log 中复验，未回收前不计通过。
- 配置的26文件 mypy 门通过；新增 owner/build 55文件尚有21项类型错误，广义受影响95文件的207项含存量债，尚未最终归因/清零。
- 构建前实测本轮拥有根1,128,741,036字节（1.051 GiB），低于6 GiB；未清理旧验收目录、真实profile或凭据。

### 尚未完成 / 下一步
修复并复验新增类型门与新helper权限；继续 Core文件更新/卸载的自然退出前置门、旁置本地包 Setup、授权凭据/附件导入与UI。正式信任锚仍为空。生产 Core/DLC/Setup、性能/最终全量/高负载、真实安装/人工/干净环境均未完成，不宣称Phase5A或5B-1完成。

### 授权和保护
不使用子智能体；不提交、推送、发布。不触碰旧 manual-core-03、manual-session-01/no-chat/APPDATA。正式密钥创建、真实数据导入及真实安装/卸载前必须再展示目标并确认；密码只经本地可信masked UI。仅本轮自有生成夹具已经用于测试。


## 连续实施准确停点（UTC 实测 2026-10-04T18:40:42+00:00）

- 仍以 e2687be / codex/phase3-worker 为基线，所有变更为 WIP；未提交、推送或发布，未使用子智能体。
- 新 probe-02 的原生 canary 权限/资源/父退出矩阵已回收通过（native-canary-64）；两包真实 LPAC 自检通过（two-owner-lpac-63），仅生成集成测试密钥，不是正式信任验收。
- AI 真实 Qt 对话框 result 方法遮蔽回归已修复：green66 56 passed /4.43s。受影响 owner/build/维护入口 mypy83 为52文件通过；Ruff83尚有导入顺序，lambda问题已修，未计最终通过。
- 卸载版本目录中新出现的孤立文件及空账本孤立版本不得误报成功：red73 2 failed → green74 12 passed /71 deselected /26.59s；完整事务族仍待重跑。
- 新真实双包 Core 卸载协调器、异步 Qt 确认窗及受限冻结维护入口：green77 9 passed /7.35s，green80 14 passed /10.81s，green82 31 passed /1 skipped /6.81s。仅生成夹具，未真实卸载产品。
- 原生 Inno 与 Python 共享/独占同一字节范围的文件锁已实际编译并跨进程互验；green88 4 passed /36.95s，包含硬链接拒绝、便携目标拒绝。夹具在 InitializeSetup 退出，不安装文件、注册表或快捷方式，不是正式 Setup 验收。
- 旁置本地 ZIP 意图适配器已实现：green90 4 passed /1.20s；真实后台管理预检，不执行代码或激活账本。正常入口路由和新产品 Setup 尚未接通。
- 私钥未创建；正式信任锚为空，生产 Core、正式 DLC、完整 Setup、凭据/附件授权导入及导入 UI 尚未完成。
- 历史全量41 4034 passed /13 skipped /14 warnings /934.25s 不含上述新改动，不能算最终门；完整全量/高负载三遍/性能与体积/人工/干净环境仍未完成。

### 下一条操作与保护
先补旁置包入口公开 seam 的失败测试，接通新冻结 Core-only 路由，再实现独立 core_webm.iss 并编译自有夹具；不修改旧安装器或旧验收运行目录。继续保持6 GiB上限；正式密钥、真实导入及真实安装/卸载前再次展示目标与影响确认。密码只经本地可信 masked UI。



## 当前实施状态（UTC 本机观测 2026-10-04T19:07:11+00:00）

- 基线 e2687be / codex/phase3-worker；仍全部为 WIP，无提交、推送、发布或子智能体。
- 旁置包冻结入口已接通真实管理预检/确认；独立 packaging/core_webm.iss 已编译生成夹具。旧 packaging/dsh-pet.iss 及旧验收 Core 保持不动。原生安装器锁增加依赖子树 reparse/hardlink 拒绝。
- red92 10 failed → green93 27 passed /1 skipped；red94 1 failed → green96（原生锁＋模板）8 passed /40.08s；red95 3 failed 后模板转绿。均为自有生成夹具，不是正式 Setup 安装验收。
- related102 完整本次事务/卸载/UI/原生入口回归：133 passed /1 skipped /1 warning /179.92s；跳过项为平台相关测试，警告是重复 ZIP 条目夹具。Ruff104 133文件 check/format-check通过；mypy104 53文件通过。
- 正式私钥未创建，正式信任锚为空；生产 Core/正式 DLC/真实 Setup 尚未完成；授权凭据与附件导入、导入 UI、卸载跨 Core 副本的持续冻结边界仍需补齐。历史 full41 4034 passed 不含新改动，不计最终门。
- 本次测得自有根 1,310,425,785字节（1.220 GiB），低于6 GiB；未清理旧人工验收目录或其他任务。

### 测试隔离事故与更正（不得被后续通过覆盖）
red91 的入口回归缺少正常 Core 防落入 stub，APPDATA 未隔离，意外进入真实 Config；已中止（退出1，不计通过）。在 C:/Users/DELL/AppData/Roaming/dsh-pet-core-webm 新建目录，触发旧 dsh-pet-standalone 配置及会话自动复制，约26KB。仅核对元数据，未阅读私人内容；不能从元数据证明初始化未访问凭据或其他服务。原始数据未删除，意外目录也保留，未擅自清理或用作验收。因此更正此前“仅生成夹具、未触碰真实数据”的笼统结论。
根因回归 red99 2 failed → green101 120 passed /3.44s：新产品直接 Config 入口不自动迁入旧数据；新冻结 Core 缺 RuntimeLayout 必须启动拒绝。入口测试增加正常 Core 防落入 stub；后续所有 pytest 子进程显式 APPDATA 指向本轮自有测试根（related102 已隔离）。red100 误用不存在的测试路径，未收集测试，不计绿灯。

### 下一条操作和保护边界
补公开 seam 回归：Core 卸载回执不能在其他 Core 副本重新安装 DLC 后仍被用于删 Core；安装器必须持续持有数据根级卸载门直到删除结束，正常 Core/设置先取得共享门。再执行新旧布局、原生安装器、维护入口相关回归。正式密钥创建、真实数据导入与真实安装/卸载前另行展示目标并确认；密码只经本地 masked UI。未创建正式密钥，不以测试锚代替正式锚。


## 实施增量（UTC 2026-10-04T19:50:29+00:00）

- [x] 卸载跨副本持续冻结边界及维护父端固定根证明：green107/114。
- [x] 会话映射对接真实 AI 数据根，结构化秘密字段拒绝：green112。
- [x] 明确来源凭据引用迁移公开 seam：red115 7 failed（未实现）→ green116 4 failed/64 passed（JSON journal 误共享 dataclass 数据）→ green117 68 passed；复制 plan 字典后保留 JSON 序列化合同。red118 2 failed/9 passed（来源 UUID 未在接受前绑定、余额引用尚不支持）→ green119 81 passed/8.88s。
- [ ] 导入 UI/实际入口、附件迁移及新代码 Ruff/mypy，进行中。
- [ ] full108 不是最终门：1 failed/4111 passed/14 skipped，测试执行中改动造成混合源码版本；稳定后重跑。
- 限制：旧原始 vision 引用兼容保留，不等于所有旧屏幕 profile 已自动重映射；不认识的秘密 schema 安全拒绝。未生成正式密钥，未真实导入/安装/卸载，无提交/推送。


## 实施增量（UTC 2026-10-04T20:32:05+00:00）

- [x] 5A-2/5A-5 显式导入界面、凭据授权提示、后台排空与关闭窗口后异步安全的公开 seam 专项。
- [x] 新冻结 Core 专用 --import-local-data 入口与常规数据交付命令；搜索/真实 data-import 深链。
- [x] 管理锁/普通数据锁分离：正常 Core 在运行时也能取消未接受预检；取消管理锁竞争后重试只取消，不重新确认；接受后禁止撤回。
- [x] red126→127→green128（121/12.07s）；red129→green130（104/13.37s）；修正错误夹具 red131 后产品 red132→green133（179/62.46s）。Ruff/format134 139文件，mypy134 56文件通过。
- [ ] 固定源码全量134运行中，未计通过；之后高负载三遍、性能实测与正式生产材料审计仍待执行。
- [ ] 正式密钥/正式锚/生产 Core+两包+Setup+ZIP/完整数据与附件导入/真实安装及人工、干净环境门尚未完成。
- 当前空间134为1,490,975,073B/6GiB；有界 no-follow 计数，不沿负向测试链接走出根。没有自动清理旧产物；red91事故仍保留，不以新通过掩盖。

### 本步实际效果与限制
普通设置可以找到显式数据导入工具，用户预检后看到单一来源、映射与凭据授权；未确认不写目标普通数据，已接受中断可恢复。当前是源码/生成夹具验证，不是正式冻结分发或真实个人数据迁移验收；自定义媒体与托管附件尚未交付。


## 验证与准确停点增量（UTC 2026-10-04T20:54:56+00:00）

- [x] 固定源码全量134：4171 passed /14 skipped /14 warnings /701.35s，退出0；执行结束后未改产品/测试Python。
- [x] Qt/IPC/进程族真实CPU高负载136连续三遍：178 passed各遍，87.67/99.89/92.87s；CPU median/p95各100%，20个自有负载进程已完整回收。
- [x] 源码生成数据基准135：预检20样本41.052/115.257ms，apply10样本168.159/382.651ms，恢复10样本79.154/121.167ms；计数/RSS/线程/句柄附报告，不冒充冻结性能。
- [x] 报告和入口/路线图当前状态保存；report139 101 passed/0.76s，docs139 129文件通过，diff139退出0。
- [ ] 正式密钥、正式生产材料/完整冻结验收尚未进行；再次确认目标后才执行创建，不把测试锚转为正式可信。
- [ ] 自定义媒体、托管附件、外部路径提示及新产品自启动清理仍需补齐；真实安装、用户/干净环境门仍未验收。
- 空间138 1,645,640,384B/6,442,450,944B，未清理旧产物或真实数据；red91事故和网络负项阶段限制持续公开保留。

### 本步实际效果与限制
源码工程门与生成夹具高负载测试已通过，证据报告和准确交接已保存；这不等于Phase5正式交付已完成。用户下一步只需确认外仓密钥/公开政策创建目标，不发送密码；正式构建和剩余功能/人工步骤继续由代理负责。


## 正式密钥创建授权（UTC 2026-10-04T21:55:26+00:00）

用户在本轮明确回复“确认”，授权仅限新建 E:/AI/DSH/release-signing/feature-release-ed25519.pem、E:/AI/DSH/release-signing/feature-release-public-policy.json，key_id official-release-2026；不覆盖既有文件，不授权真实数据导入/安装卸载/提交推送发布。两目标及release-signing父目录在启动前均不存在；父路径E:/AI/DSH为普通目录。库写入采用独占创建，私钥加密PKCS#8，密码只在本地masked Qt窗口输入，无密码argv/env/log。

启动前生成夹具专项 key-preflight141：20 passed/1.62s（签名CLI、加密密钥、分发政策），显式自有APPDATA和独立pytest根。此记录只证明授权和预检，尚未启动密码窗口，不标记正式密钥已创建。后续只读取公开政策/公钥指纹和进程结果，不把私钥内容输出到聊天或日志。

### 当前实际效果与限制
正式目标已获确认，下一动作是启动本地密码界面；用户仍需在本机输入并确认密码，未完成该步骤前不标记创建成功。


## 本地签名窗口启动（UTC 2026-10-04T21:58:40+00:00）

- [x] 已取得上述两目标和key_id的本次明确授权；生成夹具专项141 20 passed/1.62s。
- [x] 签名CLI PID 37572 的自身密码窗口 visible已核验；首次隐藏窗口只恢复该窗口可见，不重启进程或读取密码。
- [ ] 用户本地密码输入、准确进程完成/公开策略指纹核验尚待执行；窗口核验时两目标尚不存在，不标记创建成功。
- 证据 signing-launch-142/launcher-receipt.json、window-receipt.json；只记录公开元数据，密码不入聊天/argv/env/log。没有授权真实安装迁移或发布。

### 当前实际效果与限制
本地窗口可以完成新密钥创建，代理不代填密码。用户完成后，代理核验公共结果继续实施；创建成功不是生产Core/分发验收成功。


## 当前停点：正式密钥创建已核对（UTC 2026-10-05T01:14:39.201695+00:00）

- 用户本轮回复“已输入”，已完成之前授权的本地密码操作。可信CLI公开成功记录与正式公开策略一致，诊断日志0字节；私钥文件302B、创建UTC 2026-10-05T01:09:19.881520+00:00。仅检查私钥元数据，没有读取/展示私钥内容。
- key_id `official-release-2026`，公钥指纹 `dd18cbd51d2c2367e45efe7ec697a5e27e9b1654e54137f5f2734eec80f4d9ab`；公开策略限定两个官方owner及现有能力，未撤销。证据：`signing-launch-142/key-creation-receipt.json`。脱离式启动没有捕获退出码，不能补写退出0；可信CLI仅在两个文件成功写入后输出该成功记录。
- 正式加密密钥/公开政策位于仓库外 `E:/AI/DSH/release-signing/`；备份副本、解密/恢复检查、正式签包/冻结Core/分发验收尚未执行。创建授权不延伸到真实数据导入、安装卸载或发布。
- 下一步先补生产PYZ必须包含导入/维护真实入口的失败回归，以及LPAC网络canary必须实际抵达Winsock连接调用的证据，保留安全边界；随后继续剩余资源导入、新产品自启动清理和正式构建。
- Phase5A/5B-1仍未完整交付。既有full134/负载136为修改前源码历史；无提交/推送/发布/子智能体。

### 当前实际效果与限制
密码操作已完成，本轮不需要在聊天提供任何秘密；尚不能把新小Core视为正式交付。以下密码等待状态为当时的历史事实，不是当前状态。

---



### UTC 2026-10-05：143～149审计/网络seam与首轮编译复盘
- PYZ公开审计：新增8个必须包含真实导入/维护模块的回归，red143 8 failed；补CORE_REQUIRED后green145 51 passed/6.41s。green144仅是误引用不存在的测试路径、退出4/no tests，不算测试失败或通过；修正实际路径后独立pytest根运行。
- 原生网络公开seam：red146 3 failed，green147 69 passed/1 skipped/6.75s；skip为未指定原生bundle，不算沙箱通过。新增直接Winsock canary与阶段证据，Python ImportError不能作为网络拒绝。
- native149编译失败根因：Windows头定义ERROR宏，与本次函数指针typedef冲突；仅改为WSA_ERROR_FN，不改沙箱能力/ACL/Win32k/Job合同。保留native-02失败编译日志，下一次创建独立native-03，不覆盖产物。
- 空间148 no-follow 1,657,892,045B/133,483节点，后续原生+helper+canary预计400MiB，总量小于6GiB；仍须监控Core构建峰值。


## 当前停点：正式构建候选与待签名材料（UTC 2026-10-05T02:01:59.460059+00:00）

- 正式密钥创建已核对：key_id official-release-2026，公开指纹 dd18cbd51d2c2367e45efe7ec697a5e27e9b1654e54137f5f2734eec80f4d9ab。私钥仅检查元数据；创建窗口的分离启动退出码未捕获，不虚填 0。备份恢复未验收。
- 新生产 LPAC helper probe-04 原生矩阵通过（native153，13,950.618 ms）。网络负向抵达原生 Winsock 初始化，10107/未尝试连接；不冒充测到连接层或防火墙拒绝，不以 Python 导入失败充当隔离通过。
- 冻结小 Core core-01 构建/产物审计通过：455,759,283 B/1,424 文件/2,258 PYZ 模块/142.202 s；独立 Worker worker-02：66,339,336 B/97 文件/459 模块/17.557 s。它们仍是累计修改前的候选，非最终分发验收。
- AI/screen 各一份 v2 未签名材料已生成：ai-unsigned-01（37 文件/2,617,564 B），screen-unsigned-01（117 文件/66,487,121 B）；尚未安装，不能执行为可信包。冻结 Core 显式导入界面在自有 APPDATA 中真实打开并自然关闭，退出 3；未选真实来源、未导入，不能据此宣称完整冻结运行通过。
- 生产 Core 原构建误递归收集桥接 node_modules 硬链接已修：改为现有发布合同的三个文件白名单，仍拒绝发布文件链接。公开 seam red157→green158（53 passed）；正式 Core159 随后构建通过。
- 增加双包离线签名 CLI：一次本地解锁、完整目标预检、确认后再次校验两包、独立结果；部分失败保留首包结果但不报总体成功。red161/red163→green164（67 passed/8.97 s）。待补长目标窗口布局，再展示目标并启动本地解锁，不再次创建密钥。
- 最新回归172：88 passed/1 skipped，11.16 s。跳过为非当前平台用例；最新 full134/负载136 是本次新改动前历史，不能代替最终累计验证。
- 接下来：长目标本地密码窗口红绿→签两正式包→正式 LPAC 自检；并继续自有资源导入/新产品注册清理/冻结四组合及 Setup/ZIP。真实数据导入、安装卸载、备份副本仍另行确认。无提交/推送/发布/子智能体。

### 当前实际效果与限制
正式公钥与小 Core 的构建边界已有真实证据；两个 DLC 还未正式签名，Phase5A/5B-1 仍未完整交付。旧人工验收目录、凭据及意外新 AppData 根仍保护，未删除。

---



## 当前停点：正式双包签名与原生自检已通过（UTC 2026-10-05T02:27:29.577861+00:00）

- 工作区 E:/AI/DSH/dsh-pet-indesktop，分支 codex/phase3-worker，HEAD e2687be；保留原 WIP。本轮未提交、推送、发布或使用子智能体。
- 正式双包本地签名178已完成，可信进程 PID30964/退出0；公开策略 official-release-2026，指纹 dd18cbd51d2c2367e45efe7ec697a5e27e9b1654e54137f5f2734eec80f4d9ab。没有再次创建密钥，未读取或展示私钥/密码；备份/恢复检查未验收。
- 独立正式验证185：AI manifest SHA256 1084919fa1a4b5afb693ee33a382c72e030e3e058e26a52edf34c50f825acb10（host-only），screen 8229887fd9d097a7c6f89ec85ff565badbdff6a0a89c81a9e28c249e312d7198（host-worker）；外部已核对公钥策略，不用候选自带信任。验证无候选执行，分别26.980/174.190ms，样本各1，非最终性能门。
- 真实正式 LPAC185通过：AI host有效/Worker不适用/父端隔离为真，6.000s；screen host有效、冻结Worker HELLO→SHUTDOWN→优雅退出、父端隔离为真，64.339s。耗时含材料复制/哈希/清理，子探针预算仍30s；三个进程退出0，无普通子进程回退。
- 新增 pet/runtime_resource_import.py：仅已验证 content/characters 资源，manifest哈希/兼容性/角色与版本/active-previous指针完整校验；单媒体128MiB、资源总512MiB，拒绝代码/未登记资源/逃逸。凭据适配器仅处理普通JSON，不读取媒体为JSON。部分删除/写入后恢复仍有pending执行围栏，完整验证后才完成。red179→green181（156 passed/37.89s）；均生成夹具，不是用户真实导入。
- 导入窗口 scroll 属性遮蔽 QWidget.scroll 原生方法已公开seam红绿：red182 1failed→green183 87passed/24.73s；改 scroll_area，已有两处布局用例同步。10受影响源mypy通过；全 pet/scripts/tests Ruff通过；9变更format通过。
- 本地签名长目标窗口采用有界只读滚动文本，不增加权限。red174→green175/177，67/28相关历史已留日志。
- 生成空间184（本次LPAC前）：2,787,736,082B，12 reparse剪枝；硬上限6,442,450,944B。下一次构建/复制前再次估算，不清理旧人工验收目录或其他任务文件。
- core-01/worker-02已构建审计，但core-01早于新资源导入/UI修复，不能当最终累计交付。下一步：核对新产品Agent全局注册拥有边界，再构建core-02，并继续冻结四组合/真实启动确认/Setup与ZIP。旧源角色目录、外部角色/附件提示和大资源内存成本仍待核实，不冒充所有旧资源迁移已完成。
- full134/负载136为新改动前的源码历史，最终全量/负载/性能/真实安装、便携、用户及干净环境门尚未执行。真实数据导入、安装卸载、密钥备份新目标须另行展示并确认。保护意外新AppData根和Phase4B目录；既有APPDATA隔离事故不能抹去。

### 当前实际效果与限制
两个正式签名DLC已有独立验签和原生安全自检证据，但尚未安装或发布。Phase5A/5B-1仍在实施，不能宣称分发与用户体验验收完成。


## 当前停点：新 Core 四组合启动与分发候选已生成（UTC 2026-10-05T03:08:38.470993+00:00）

- codex/phase3-worker / e2687be，原 WIP 保留；无提交、推送、发布或子智能体。正式双包签名178与原生 LPAC185已通过；不再次创建密钥或读取私钥内容，备份恢复仍待确认。
- Core02真实生产构建：455,763,782B /1,424文件 /2,259PYZ模块 /199.044s；实际 PYZ、原生依赖与可信 helper 摘要审计通过，没有 AI/识屏业务实现或源码/测试信任回退。helper probe-04固定摘要66b94b18f3b971879bb73c4948d2e62c80048bba99659e49d0d55da2146c808f；最终源代码一致性需再审计。
- 真实 UI 安装191：两包分别完成预检/用户界面确认/正式 LPAC/切换，退出3且等待启动确认，未冒报已可用。正常 Core195自行清除两包 pending（revision4），未打开设置；菜单有AI对话/看看屏幕/主动识屏，实际“退出”菜单自然退出0。192～194只是探针窗口过早查询/短暂UIA句柄失效，修正事件与句柄重取后195成功；不强杀。
- 冻结四组合：none/AI/screen（201）及both（195）均普通Core实际启动、对应贡献准确、自然退出0。none账本为空，不存在加载receipt，201日志的固定“BOTH/TWO_PACKAGE”标签不作为事实证据；以receipt states与菜单树为准。没有真实模型/截图/用户数据导入验收。
- 新档案构建公开seam：red196（18failed，接口缺失）→197/198（实际descriptor/stage API纠正）→green199（67passed/7.88s）；新增scripts/build_local_archives.py与测试，Core审计复用只读inspect_core_bundle，不覆盖既有artifact。流式写入、源码哈希复核、独占输出、体积预算；未知用户数据与非空代码锁不入ZIP。2脚本mypy/3format通过，最终全量/Ruff仍需累计重跑。
- archive200退出0：AI ZIP2,334,659B/0.332s；screen ZIP24,455,042B/4.049s；普通ZIP259,350,992B/27.117s；便携ZIP259,351,159B/27.685s。便携标记显式固定data，无用户数据。实际Setup202编译成功95.000s、222,165,149B；仅编译，未安装，Windows发布者认证/编译工具许可未验收。
- 空间203：5,169,418,473B，余1,273,032,471B，上限6,442,450,944B；12测试reparse剪枝且未跟随。Core186预算初次被pytest测试链接拒绝，改为生产拥有根的独立预算并扣除其他占用，未削弱路径安全。下一次复制/构建前估算；未清理任何旧验收目录。
- 准确下一步：补Agent全局hook/bridge新产品拥有边界与Core删除前收尾，后续累计回归；分发总清单签名、Setup更新/卸载实测、便携移动/导入、请求体验/人工/干净环境与性能门仍待执行。新真实安装/卸载、真实导入、密钥备份目标须另行展示确认。既有APPDATA事故及其保护根保留，不抹去历史。

### 当前实际效果与限制
正式签名双包和五项交付候选已生成，真实冻结Core可自行确认两个功能包加载、四组合入口正确。但尚不是完整分发验收，不自动操作真实用户安装，也没有发布。

---


## 工作记录212：注册拥有边界回归已通过，准备累计回归与 Core03（UTC 2026-10-05T03:34:55.460339+00:00）

- 分支 codex/phase3-worker，HEAD e2687be；所有 WIP 保留；无提交、推送、发布或子智能体。
- 正式密钥及双包签名178已成功，真实 LPAC185、Core02四组合195/201、流式ZIP200、Setup编译202已有历史证据；没有新增真实安装或导入。
- Agent全局注册采用新产品数据根ID与当前Core路径的明确拥有边界：不认领旧hook/外国bridge，不自动pnpm或递归清理未知依赖；Core收尾失败保持可恢复而非宣称卸载。
- Agent公开seam red204/206→green210（259passed/1skip/8.31s）；208巨大参数ID/原生junction夹具及209脚本缝合失败已复盘，不是产品权限回退。修正短ID、原生reparse断言及精确方法编辑。
- 受影响mypy211失败后，212修正Connection可空类型、避免SemanticEvent/str变量混用、tracker接受实际语义事件并使dict类型可判定；3源mypy通过，4文件Ruff/format通过。
- 累计相关212：267passed/1skipped/8.08s，真实Qt及生成HOME夹具；不访问真实Agent配置。新增档案合同199：67passed/7.88s。
- 空间测量：5,183,206,893B，余1,259,244,051B；测试reparse剪枝未跟随。计划只清理本轮旧候选core-01（先保留证据/查边界和占用），保留Core02；并非旧Phase4B运行目录清理。
- 下一步：全量pytest及最终质量门；Core03源码一致性重建/冻结矩阵；Setup/便携/请求/性能及分发签名。真实安装/卸载、真实数据导入、密钥备份新目标另行确认。

### 当前实际效果与限制
正式签名双包与交付候选已生成，真实Core02四组合与自身加载确认已验证；新注册拥有边界只在源码回归中验证，Core02尚不包含它。Phase5A/5B-1未完成，没有发布或给用户安装新候选。

---

## 2026-10-05 213～225：累计回归与新 helper 正式双包 LPAC 通过，最终 Core 重建受空间门阻塞（UTC 2026-10-05T04:18:01.321967+00:00）

- 分支 `codex/phase3-worker`，HEAD `e2687be`；全部原 WIP 保留；没有提交、推送、发布或子智能体。
- 稳定全量214：4258 passed /14 skipped /15 warnings /816.22s；执行期间没有产品源码修改。216：全 pet/features/scripts/tests Ruff、151个本轮Python文件format、配置mypy26源通过。LOG末尾多余空行使首次diff-check失败，精确修正后退出0；历史失败仍留日志。
- 真CPU高负载223：20个自有below-normal负载进程，三遍分别227 passed /110.79、112.01、124.03s；每遍CPU median/p95均100.0%。不是模拟CPU负载，未改变系统配置。
- 新冻结 helper probe-05（222）：17,267,820B /63文件 /176 PYZ模块，构建21.27s，父端总49.323s；摘要3087700e533095c4a7fc8cd1923baf463f9414108c134cace1426ab5a3ff5e6a。上游libsodium完整ZIP经既定摘要验证，不以单DLL或源码回退替代。
- 原生Windows canary224完整权限/限制/超时/输出/崩溃/父退出清理通过，总54.514s；权限probe4435.31ms（n=1）。只使用生成秘密/网络/文件夹具。普通正对照IPv4/IPv6 connect成功；LPAC在WSAStartup 10107失败且未抵达connect，不能写成已实测防火墙连接拒绝。
- 正式双包225以新helper真实LPAC通过：AI host-only 8.696s，Worker not_applicable；screen host-worker 66.641s，HELLO→SHUTDOWN→优雅退出；三个实际探针退出0，隔离证据均由可信父端验证。225初次绝对脚本启动因可信父端sys.path缺repo导致ModuleNotFoundError，未进入probe；改为repo cwd的runpy启动可信测试父端。候选仍冻结运行，PYTHONPATH为空，无普通子进程/源码回退。
- 217正式目录/ZIP预检：AI、screen各目录/ZIP20次，合计80预检+80未接受取消；没有factory/Worker执行。218真实NTFS自有非可执行夹具移动和生成OS凭据恢复通过，仅同卷同用户；不等于最终冻结便携、跨卷或跨用户验收。
- 215源码一致性审计：Core02的pet/agent_link.py、pet/core_maintenance.py、pet/model_access_tracker.py已落后，需要Core03；Worker02及正式DLC未变。Core03需嵌入新helper05，旧候选不能宣称最终交付。
- 221空间5,332,452,814B，剩1,109,998,130B（随后helper/canary等新增，下一次先实测）；6GiB硬上限。213清理命令被工具策略拒绝，未执行删除、未重试绕过。现余量不足Core的2GiB构建预留，停止该构建切片，不扩大范围或降低门。
- 219启动10样本基准首样本自然退出动作未找到，220一次有界诊断仍失败；不算启动性能通过。仅本轮Core02 PID36460（bench-frozen-core-219隔离APPDATA）仍驻留，不强退、不认作旧常用桌宠；后续先分析实际输入/菜单根因或让用户从它自己的“退出”菜单自然关闭。
- 准确下一步：受影响扩大mypy与文档/报告纪律；空间允许后最终Core03/ZIP/Setup/冻结业务矩阵。真实安装/卸载、真实导入、密钥备份新目标须另行确认。正式密钥/双包签名已成功，不再生成密钥；总分发清单签名、备份恢复、用户/干净环境/发布者认证未验收。

### 当前实际效果与限制
正式签名双包、新可信helper以及Core02历史四组合启动已有真实证据；源码累计回归已通过。但最新Core尚未重建，Setup只是编译，完整请求/更新/卸载/最终便携与人工验收未完成。Phase5A和5B-1仍实施中，不让用户安装旧候选冒充最终成果。


## 2026-10-05 228～236：卸载窗口公开合同与最终复跑

扩大56源mypy228红：CoreRemovalEvidence赋值到推断None；Qt原生scroll公开seam229红：1 failed。首次修改脚本误判6处字段引用，未写文件，后核对5处。230只做类型注解、scroll_area与关联断言，186 passed/53.93s；56源mypy通过，另231完整AI host26源mypy通过。228文档129文件链接72.838s、专项165 passed/5.88s、diff-check退出0。脚本字段/未知文件守卫没有被降级。

空间232测5,713,826,179B，余728,624,765B；新full234与满CPU233组合预留512MiB通过，Core2GiB预留不通过。两累计复跑仍运行，期间不修改产品/测试Python；原6GiB门和清理拒绝213事实不变。启动219/220自己的Core02 PID36460仍待自然退出，不强杀、不当成旧常用桌宠。正式密钥/双包签名和新helper225通过不受窗口修改影响，但最终Core03需加入第4个过期模块修复。

实际效果：卸载确认窗保留原生QWidget接口，真正的后台清理回执具有明确可空类型；没有改变用户数据、卸载顺序、外观或扩大执行权限。Phase5A/5B-1仍未完整交付。


## 237/238 最新累计收齐（UTC 2026-10-05T04:49:44.602394+00:00）

## 当前停点：最新全量与满 CPU 三遍通过，最终冻结重建仍受空间门阻塞（UTC 2026-10-05T04:49:44.602394+00:00）

- `codex/phase3-worker` / `e2687be`；原 WIP 全部保留，无提交、推送、发布或子智能体。
- 最新稳定累计 full234：4258 passed /14 skipped /14 warnings /861.35s（父端863.204s），退出0，pet/features/scripts/tests 的哈希复核无运行中源码修改。14 skip 未执行不计通过，warnings 为实际Qt弃用与重复ZIP负向夹具，不过滤。
- 最新真实高负载233：20个本轮自有 below-normal CPU进程，三遍分别227 passed /109.71、122.16、128.58s；每遍 CPU median/p95 100.0%/100.0%；父端114.500、127.578、133.625s。退出0且负载进程已回收。不是模拟满载，不修改系统设置。
- 229→230卸载窗公开Qt回归先红后绿：不再用scroll控件属性遮蔽QWidget.scroll；回执使用CoreRemovalEvidence可空类型，186 passed /53.93s。56受影响pet/scripts源以及231 AI host26源mypy通过；此前214/223是修复前历史，不代替最新门。
- 正式加密PKCS#8及公钥策略已成功创建，双包签名178完成，不再等待密码、不得重建密钥。可信新helper05为17,267,820B/63文件/176PYZ，摘要3087700e533095c4a7fc8cd1923baf463f9414108c134cace1426ab5a3ff5e6a；原生权限矩阵224与正式双包LPAC225通过。AI host-only不伪造Worker；screen完成HELLO→SHUTDOWN→优雅退出。沙箱网络负对照为真实WSAStartup10107且未connect，不写成已测防火墙connect拒绝。
- 217正式AI/screen目录/ZIP合计80预检+80未接受取消，无factory/Worker执行；218真实NTFS生成夹具同卷移动与同用户OS凭据恢复通过。最终冻结便携/跨卷/干净用户仍未验收；probe目前正式端到端各n=1，不满足n>=10。
- Core02已有真实四组合普通启动确认195/201，未借设置提交成功。但Core02落后于pet/agent_link.py、pet/core_maintenance.py、pet/model_access_tracker.py、pet/core_uninstall_ui.py四源，并携带旧helper04；必须重建Core03和后续ZIP/Setup，不将旧候选冒充最终交付。Worker02/正式两包材料不因这四处Core改动变化。
- 空间237：本轮拥有根5,871,902,944B（113953文件，28 reparse剪枝且不跟随），6GiB上限6,442,450,944B，余570,548,000B。Core03的2,147,483,648B预留不满足，差1,576,935,648B。213清理被工具策略拒绝，未删除且不重试绕过；不降低预留、不超6GiB，不清理旧Phase4B或真实数据。
- 219启动基准首个样本及220有界退出诊断失败，0有效启动样本；仅本轮隔离Core02 PID36460仍驻留，APPDATA为bench-frozen-core-219/APPDATA。不强退、不注入退出、不冒充旧常用桌宠。停止第三次猜测，待实际输入路径诊断或用户使用这个测试Core自己的退出菜单自然结束。
- 准确下一步：更新逐文件/行数证据并运行累计Ruff、format、配置+受影响mypy、文档/报告纪律、diff-check；收齐后再记结果。空间安全门满足后执行Core03源码一致性构建，嵌入helper05，重做冻结业务/分发。正式总清单签名、完整请求/升级/卸载/最终便携/真实Setup/人工与干净环境仍未完成。真实安装卸载、数据导入、密钥备份新目标须另行确认；Authenticode/SmartScreen/Inno许可未验收。

### 当前实际效果与限制
正式两包和可信自检已有证据，最新源码累计与满载回归通过；仍未完成最终冻结交付。不要安装旧候选冒充最终成果，不宣布Phase5A或5B-1完成。密码已输入且已成功用于签名，本轮无需再提供秘密。

- report文件表发现9个本轮源/测试漏行及4行置于表外；按实际diff和新UTF-8行数修复，不用表完整性代替产品验收。model_access_tracker为修改+10/-6，不能列为新增。


## 239～242 最终源码门及环境停点（UTC 2026-10-05T05:00:38.497955+00:00）

- 最新综合质量239/240已收齐：Ruff pet/features/scripts/tests通过（0.249s），151文件format通过（0.079s）；配置mypy26（4.702s）、受影响56（2.649s）、AI host26（0.785s）通过；文档129（61.411s）、构建/信任/注册/报告165 passed（5.94s）通过；tracked diff-check退出0。239首次ruff/format因隔离USERPROFILE使Python user-site模块不可见而失败；240使用已安装绝对路径原生ruff0.16.6通过，不重装依赖、不注入PYTHONPATH、不把239退出1涂成0。
- 241只读新清理提案共8项存在的旧生成物/1,633,900,888B，无删除命令/实际删除。不是重试213被拒绝的core-01目标；保留此前拒绝及所有保护项。必须独立核对拥有证据/占用，明确目标和影响获确认且工具允许后才可处理。空间门和最终Core03仍未解除，不因提出清理方案而计通过。
- 准确下一步：等待新清理白名单目标/影响确认与安全可执行条件，重新核对拥有/占用/边界，操作后重测空间；不能自动重试213或绕过工具拒绝。空间满足2GiB后重建Core03并嵌入helper05，重新冻结业务/分发；旧Core02的4处源不一致不得忽略。正式总清单签名、完整请求/升级/卸载/最终便携/真实Setup/人工与干净环境仍未完成。真实安装卸载、数据导入、密钥备份新目标须另行确认；Authenticode/SmartScreen/Inno许可未验收。

实际效果：最新源码质量门已通过，末端冻结/安装/分发仍未完成。没有删除、真实数据导入或安装、Git提交/推送/发布；无新秘密操作。

## 243～248 追加审计、文本复盘与未清理停点（UTC 2026-10-05T05:22:53.605378+00:00）

## 当前停点：文本复验完成，等待限定空间清理确认（UTC 2026-10-05T05:22:53.605378+00:00）

- 用户“已输入”已用于正式加密密钥与双DLC签名，未再次创建密钥、不读取私钥、不通过聊天索取秘密。分支codex/phase3-worker / e2687be，原WIP全部保留，暂存为空；无提交、推送、发布或子智能体。
- 最新完整Python累计门仍为full234：4258 passed /14 skipped /14 warnings /861.35s，运行期间源码不变。真实满CPU233三遍各227 passed；Ruff/151 format、配置26/受影响56/AI host26 mypy、129文档及165相关门已通过。239 Ruff模块路径失败保留，240使用既有绝对路径原生工具通过，未注入PYTHONPATH或重装。
- 243最终补充检查：文档129、报告101、tracked diff-check通过，但发现新文件3处空白（AI QSS空EOF与Setup英文消息2处行末空格），因此该追加审计总体退出1，不能写成整体通过。244首次修正按LF检查原始CRLF而失败，发生在所有写入之前；245保留原行尾方式，只修上述文本，并把消息分隔符写在Pascal字符串连接处。
- 246文本相关回归190 passed /1 skipped /140.61s；文档129、报告101、tracked diff-check与所有新增文本空白复核通过。pet/features/scripts/tests的Python源码与full234哈希一致。没有新增Python逻辑、配置、线程或持久化变更，故这次纯空白/提示分隔符切片采用专项+相关验证，不重复14分钟全量；不能把full234说成测试了未来改动。
- 已签名AI源包和ZIP均未改写；AI仓库QSS少1空行使最终payload哈希需要重新生成并签名，现有正式包作为历史候选保留，不原地修签名。Core02落后4处Core源且带旧helper04，Core03必须重建并携带helper05；Worker02与screen签包未因文本修正变化。最终统一签名另走可信本地解锁，当前不再索要密码。
- 空间247只读测量：5,901,946,176B，余540,504,768B，29个reparse剪枝未跟随；不足2,147,483,648B的Core03预留。241八项目标1,633,900,888B只是提案，未删除，须删前再核对边界/拥有/占用、保留结果并获得目标确认且工具允许。213/core-01拒绝不重试、不绕过，旧Phase4B/真实profile/凭据/事故根始终保护。
- 247确认仅本轮空包测试Core PID36460仍驻留，exe与创建时间匹配，APPDATA为bench-frozen-core-219/APPDATA。不强退；用户可使用这个测试Core自己的退出菜单自然退出。219启动性能仍0有效样本，不能写达标。
- OPS-PROBE-MATERIALS（Status: ready-for-agent）：生产run保留自检文件快照，现有cleanup_owned_probe只恢复记录profile，未被pet生产调用；事务GC仅处理已退役versions。实际成功225材料仍在，确认文件积累缺口，但未做生产崩溃profile泄漏实测，也不等于隔离突破。下一切片先在公开seam补红测：活进程不得清理、已释放的明确拥有材料有界回收、未知/证据冲突/链接不删除、清理失败留可恢复记录；接入恢复只信拥有记录和真实释放，不能全根扫描猜测归属。新逻辑会要求重跑全量/高负载及重建。
- 准确下一步：请求用户确认PLAN241八项明确白名单；仅在原生工具允许、安全边界/占用证明成立时处理，并重新核对6GiB/2GiB空间门。同步补有界probe材料恢复后再最终Core03、双包和分发签名、冻结请求/升级/卸载/便携/Setup闭环。真实安装卸载/数据导入/密钥备份新目标另行确认；人工/干净环境、Authenticode/SmartScreen/Inno许可仍未验收。

### 当前实际效果与限制

本轮只完善源码文本与证据，没有新增安装、删除或真实数据操作。正式签名与安全自检已有阶段证据，但最终Core、安装分发和Phase5A/5B-1仍未交付，不能安装旧候选冒充完成。

### 248记录缝合失败与249只修统计（UTC 2026-10-05T05:24:00.839365+00:00）

248已写同组当前记录/设计/入口/报告，但最后行数刷新误按五列表解析真实四列表，断言0行失败，未产生完成receipt。249读取既有242实际解析方式后仅刷新行数/两处源文本说明，不重复追加记录、不改源码、不把失败涂成通过。

## 250～253 最后证据与未授权清理停点（UTC 2026-10-05T05:31:29.892754+00:00）

- 最后文档复验250（UTC 2026-10-05T05:26:20.978759+00:00）：129文件链接通过67.059s，报告101 passed /0.70s；tracked diff-check退出0，所有新增文本空白问题0，Python哈希仍与full234一致。249已按实际四列表刷新172文件证据；248统计解析失败保留，不重复追加记录。
- 更晚空间251：本轮拥有根5,909,529,950B，余532,920,994B，29 reparse剪枝未跟随；尚不足2GiB。仅假设241八项全部安全释放才有2,166,821,882B，比预留多19,338,234B（约18.44MiB）；不是已清理结果，后续每次复制/构建前再测。252对枚举出的真实packages/两DLC ZIP复核SHA256与200一致，未修改签名材料。251第一次按错误假设名称查找为not_found，不当成校验通过；252以实际目录/名称修正。

准确下一步不变：先确认241八项白名单和安全条件，再过空间门；probe材料有界恢复、最终Core03/AI重签/分发与真实验收仍待完成。没有删除或新秘密操作。

## 2026-10-05 254–255：用户确认后的有界清理被工具拒绝

八项拥有证据、无链接/硬链接、独占打开及沙箱PID释放检查通过；保存54份必要证据。执行固定绝对路径、同一PowerShell的Remove-Item之前，exec工具返回`rejected: blocked by policy`，没有命令进程或删除动作。全8项仍在，释放0B；不换工具绕过。空间255为5,910,508,198B。保留原WIP、密钥、签包、旧人工目录和个人数据；后续补生产自检有界回收。证据：cleanup-approved-254/pre-delete.json、result.json、space-after-rejected-cleanup-255.json。

## 2026-10-05 256–268：自检材料泄留的生产根因修复（尚待完整复验）

260启动没有材料恢复、gc未路由材料服务、UI没有独立清理警告，先红后接入。259旧合同39 passed；262新相关族176 passed、2 skipped、1 failed（折行文本断言）、1 warning，未虚报绿。Windows真实生成子进程已验证cleaned标记不能绕过活PID的kernel检查，自然输入结束后才能恢复；从不打开PROCESS_TERMINATE。

新增聚合清单回归前两次失败是夹具错误：不可变verifier不能赋值；在策略已绑定后变更limits应被拒绝。暂停叠加补丁，回溯夹具创建点，将同一limits在source/policy/OS launch fixture构造前绑定。266才是实际合同红：host/Worker两套合法快照超过单包GC清单边界。改聚合GC预算而非放宽执行verifier，267正在验证。263受影响mypy两项为新代码类型问题，已准确标注thread owner可空和parent-owned phase不变量，待267复验。保存历史失败，不删除日志或旧产物。

## 2026-10-05 空间预算调整（用户当前明确授权；2026-10-05T12:34:02.262Z）

用户允许适当增加生成物预算。本轮当前上限由6GiB提高至**8GiB（8,589,934,592B）**，保留Core构建**2GiB（2,147,483,648B）**预留；E盘实测余216,251,744,256B。6GiB仍是此前命令/历史结果的实际上限，不改写历史。

不扩大清理权限：254八项及213被工具拒绝的删除不重试、不绕过；旧人工验收目录、真实数据、凭据和密钥不动。新构建/复制前重新测量并估算，预计超出8GiB先报告；不提交、推送、发布或使用子智能体。

267专项已通过95 passed /2 skipped /84.04s，5个受影响源mypy通过。此前269启动命令尚未产生脚本或日志，属执行工具未送达，不能算原生验证成功；改用新的明确拥有脚本270，不复用同一产物路径，也不尝试受拒绝删除。

## 2026-10-05 已实现增量：生产 probe 材料回收合同

- 新材料按官方owner分区，父端在复制/启动前写入 intent，绑定随机attempt、候选摘要、helper摘要和源/父事务诊断。每根最多4个未释放attempt、8条精简回执、64KiB journal；源路径仅诊断，不作删除权威。
- 专用内核maintenance锁覆盖材料复制、原生启动和恢复；不占用management/state锁，不在Qt GUI线程进行哈希/进程等待/删除。每次host/Worker分别拥有只读运行副本与scratch。
- 原生父端关闭自有Job后检查进程退出，持久化实际process_released；恢复再次核对PID/creation identity。缺PID且不能证明释放、活进程、ACL/链接/reparse/hardlink/未知节点/证据冲突均保留，不能仅采信cleaned标签或子进程自报。
- 仅在全部拥有证据通过后提交deleting intent，再删除本attempt目录；部分删除可重放而不要求已删除helper重新验签。未知目录不收编、不扫除；先前被工具拒绝的清理目标不属此机制。
- GC清单按两套candidate+helper有界聚合，候选执行验证上限不变。恢复失败与材料警告独立展示，不改变包state/enabled、不把已完成安装误标失败；重试仍安全，不强退进程。
- 生产初始化queued启动专用后台恢复；recover/gc复用同一入口，回执以queued信号回GUI。关闭窗口后不访问失效QObject，也不取消已接受事务。

公开回归256/260/266分别先红，267转绿95 passed /2 skipped /84.04s及5源mypy；真实正式双包270通过LPAC、优雅退出和文件回收（AI3.126s，screen17.497s，各n=1）。十次基准271进行中；最新累计全量/冻结Core尚未验证，不以此宣称分发完成。

### 当前实际效果与限制
源码安装自检结束会安全回收自己刚创建的材料，启动和安全重试可恢复中断记录；活进程和不明目录仍保留并解释原因。该实现尚未进入用户运行的旧Core，最终Core03正在准备。

## 当前停点：8GiB空间门、小Core与五产物签名通过，完整验收未完成（UTC 2026-10-05T14:39:49.608Z）

- 分支 `codex/phase3-worker` / HEAD `e2687be`，原WIP保留；暂存为空，无提交、推送、发布或子智能体。Phase5A/5B-1仍实施中。
- 2026-10-05用户追加授权峰值**8GiB（8,589,934,592B）**，不取消预算、不扩大清理。305末次只读no-follow实测**8,374,960,188B（7.799789GiB）**，136,776常规文件、36链接/reparse剪枝，余**214,974,404B（205.016MiB）**；E盘余211,506,638,848B。随后仅文本/小证据补记，不把保留量说成连续构建峰值。2GiB仍为新Core构建预留；213/254删除被拒绝、释放0B，未重试绕过，旧人工构建/真实profile/凭据/事故AppData根/密钥保护。
- Core03生产构建/审计通过：455,780,427B /1,424文件 /2,260实际PYZ，构建服务180.321s；helper05摘要`3087700e533095c4a7fc8cd1923baf463f9414108c134cace1426ab5a3ff5e6a`。没有AI/屏幕业务实现、测试锚或源码回退；Authenticode仍未具备。
- **五产物平面签名目录 `delivery-set-293` 已形成**：Setup222,182,771B；普通ZIP259,368,713B；便携ZIP259,368,880B；AI1.0.1 ZIP2,334,658B；screen1.0.0 ZIP24,455,042B；合计767,710,064B。293逐项独占复制并复核SHA256；保留旧候选，不覆盖/删除。Setup279编译119.579s退出0，仍不是安装验收。
- **总分发正式签名294通过**：UTC2026-10-05T13:58:26.426093+00:00，7.809986s/n=1；`distribution.json`摘要`fc7157ead52bc73146be20e1662515e46719eb8fc795767e76cafba0ca46ec5e`。既有`official-release-2026`加密密钥未创建/覆盖；本地masked窗口可见且签名进程退出0，密码没有进入聊天/argv/env/log。296/305从外部已核对公钥政策**独立验证通过（各n=1，1.249367s/1.963210s）**，不是使用产物自带公钥自证可信。签名密钥备份/恢复、Authenticode和正式发布仍未验收。
- AI1.0.1正式manifest摘要`e4faefde007278aa16ae82ee6b984e0b208bea37ad68afbb4b29392f0cfbbc5a`，281正式签名及完整Verifier通过。screen1.0.0保持`8229887fd9d097a7c6f89ec85ff565badbdff6a0a89c81a9e28c249e312d7198`；旧AI1.0.0与旧正式目录未覆盖。
- **最新产品Python质量仍是273/275/276**：全量4274 passed /15 skipped /15 warnings /657.90s；Ruff、155文件format、配置mypy26/受影响57/AI26及165相关通过。20个自有below-normal负载、17族连续三遍各251 passed /1 skipped，118.76/121.54/115.61s，CPU median/p95各100%/100%，负载已回收。后续只有文档和忽略目录驱动脚本，302再次证明产品Python未变；不以这些门冒充安装/人工/干净环境。
- 271真实LPAC自检各10样本通过、拥有材料安全回收1,874,358,751B；AI median/p95 3552.794/3814.0633ms，screen16583.77145/19648.1784ms。原271末尾NameError退出1保留，独立验证核对20有效样本/30原生启动；RSS/IO未采集，不编造。
- **当前冻结证据分开计**：278空包/仅screen的普通Core、自身加载确认/菜单隔离/自然退出0通过。287仅AI的UI安装和普通Core真实加载确认通过（active1.0.1、enabled、revision4、pending=null），但菜单/自然退出驱动退出1，因此仅AI整行和both仍未通过；不借设置替代Core、不覆写287原失败。290/291/292/295/297/298均保留失败；291的未核实Qt源码推断已撤回。295真实卡片按钮是“隐藏桌宠”，297实际该按钮使本进程窗口恢复可见但脚本成功条件仍失败；298无自有像素命中，未发送右键/退出、不强退。304只读身份/命中元数据再次看到目标原生hidden，未移动鼠标、截图、发消息或终止进程，不能反推298时的原因。下一次先定位本测试进程实际Qt可见性/渲染与点击边界，不追加盲猜。
- **文本门302已通过**：129文档链接202.207596s、101报告测试1.63s、tracked diff-check退出0，所有新增文本空白/EOF、176逐文件覆盖及产品Python与273一致性均通过。301在文档遍历240s时超时、父端240.87s退出1原记录保留；302只是把同一文档检查的驱动预算改600s，未跳门/改产品源码/放松probe限制。逐文件计数的旧闭包映射已改显式新numstat参数并复核0差异。最终补记不改链接目标，追加文本/报告/source/行数复核306已退出0（101 passed /1.43s、diff-check退出0、176文件覆盖/空白/源码一致性通过），公开证据归于quality-306，不把289/301说成全绿。所有五MD沿用本组，历史日期/原失败保留。
- **准确下一步**：继续解决Core03仅AI菜单/自然退出及both实际加载；再准备真实Setup/更新/卸载、冻结请求/升级/回滚/便携、>=10启动/各状态性能、用户/干净Windows验收。仅余约205MiB，任何后续解压/构建/复制先估算，不自动提高8GiB。真实安装/导入/密钥备份须再次展示新目标与影响。
- 分发签名根必须保持五文件+json/sig的平面合同，**不能在其内新增packages或证据文件**。真实Setup选包使用安装器旁独立`packages`，准备时先验证签名根，再将相同DLC字节复制到另一个已确认的Setup验收根；不是修改平面信任合同，也不自动安装。

### 当前实际效果与限制
空间阻挡已经解除，最新小Core、两种ZIP、正式双包及总分发签名均已产生并核验；这不是Phase5A交付完成。已验收旧桌宠和个人数据保持不动；自动化测试进程只允许实际菜单自然退出。尚无真实Setup安装、真实模型体验或干净环境通过记录，不需要用户提供聊天密码。

---

## 历史停点：8GiB授权后Core03/ZIP/Setup生成，全量与满CPU三轮通过（UTC 2026-10-05T13:16:35.263Z）

- 分支`codex/phase3-worker` / HEAD `e2687be`，原WIP保留；无暂存、提交、推送、发布或子智能体。Phase5A/5B-1仍在实施，未宣布交付完成。
- 用户2026-10-05追加授权：本轮生成物峰值从6GiB调至**8GiB（8,589,934,592B）**，Core构建预留仍为2GiB。280只读no-follow实测7,599,215,563B（136,544常规文件；36链接/reparse剪枝），余990,719,029B；E盘212,282,949,632B可用。254/213删除被工具拒绝、实际0B释放，未重试或绕过；不扩大清理权限，不触碰旧4B/真实profile/凭据/事故AppData根/密钥。
- Core03已构建并审计：455,780,427B /1,424文件 /2,260实际PYZ模块；PyInstaller133.113s、构建服务180.321s、含预算扫描父端219.955s。嵌入helper05摘要`3087700e533095c4a7fc8cd1923baf463f9414108c134cace1426ab5a3ff5e6a`及正式信任策略；Core无AI/屏幕理解实现，无测试公钥或源码回退。Authenticode仍未具备。
- 最新稳定全量273：**4274 passed /15 skipped /15 warnings /657.90s**（命令父端660.976s），退出0；pet/features/scripts/tests运行期源码哈希未变。275全范围Ruff、155文件format、配置mypy26/受影响57/AI26、129文档链接、165报告/构建用例及tracked diff-check通过。最新文档修改后另行复验。
- 276真实20个自有below-normal负载进程下，17个Qt/IPC/生命周期族连续三遍各**251 passed /1 skipped**：118.76、121.54、115.61s；每遍CPU median/p95均100%/100%，全部负载回收，源码哈希未变。与273共同覆盖当前产品Python；后续只有文档和忽略目录验收脚本修改，不冒称全量覆盖未来变更。
- 267相关专项95 passed /2 skipped；270真实新父端LPAC双方各1次通过。271生成夹具20次自检/30次原生启动及拥有材料GC全部有效，AI median/p95=3552.794/3814.0633ms，屏幕=16583.77145/19648.1784ms；各10次累计回收198,911,947B/1,675,446,804B。原271汇总NameError退出1，独立验证保留失败并核实样本；RSS/IO未采集，不填造。
- 冻结278空包/仅屏幕两组合通过：Core03自行实际加载确认，screen修订4/active1.0.0/enabled/pending=null；真实菜单owner隔离、自然退出0，不借普通设置替代Core。最终仅AI/双包仍待新AI正式签名；旧Core02四组合只为历史证据。
- 277普通ZIP259,368,713B、便携ZIP259,368,880B、正式screen ZIP24,455,042B已审计。首次Setup因验收脚本字符串转义破坏ISCC路径、WinError2未启动编译器；279只重试未创建的Setup，编译退出0：222,182,771B /119.579s，SHA256 `1dbbe87cbf21abebb81825d46c5632e4667a0d0f632e0b4069959cee7a6d7233`。**编译不是安装验收**；未执行真实Setup安装，Authenticode/SmartScreen/Inno许可单列。
- 新AI **1.0.1** 于281正式签名并完整验证通过（UTC2026-10-05T13:14:16.163926+00:00，0.726302s/n=1）；manifest摘要`e4faefde007278aa16ae82ee6b984e0b208bea37ad68afbb4b29392f0cfbbc5a`，目标`production-builds/ai-signed-02`。旧AI1.0.0及既有加密私钥未覆盖；密码只在可信本地遮罩窗口输入，无argv/env/日志/聊天秘密。窗口初查30秒短于预算扫描，284确认同一PID可见，没有重复启动。签名不执行factory、不等于生产加载成功；287最终仅AI/双包真实矩阵正在执行，未先计通过。
- 准确下一步：收齐287最终仅AI/双包真实Core矩阵与最新文档门→核对预算并整理五产物平面分发目录→本地解锁签总分发清单。真实Setup安装/更新/卸载、冻结请求/升级/回滚/便携、>=10启动/状态操作性能、用户与干净Windows门仍未完成；真实安装/导入/密钥备份目标须再次展示确认。

### 当前实际效果与限制
空间上调已让最新小Core及ZIP/Setup产出，而不是跳过安全门。全量、滿CPU三轮和空包/仅识屏真实启动通过；AI1.0.1已完成正式签名，最新双包矩阵未结束，不影响已验收旧桌宠。最终交付及人工/新用户环境验收仍未完成。

## 无人值守继续：2026-10-06 长路径故障定位与最新全量门

- 本轮最新源码全量：`python -m pytest -q --basetemp=acceptance-night-20261006/pytest-full-01`（`QT_QPA_PLATFORM=offscreen`，TEMP/TMP 指向自有目录）**4294 passed / 15 skipped / 14 warnings，796.84s，exit 0**。历史 4274 通过不替代此证据。
- V1 驱动根因已定位：Qt 需要键盘语义的 `WM_CONTEXTMENU(-1)`；已修自有 PID 身份绑定驱动并通过 9 个专项测试。仅 AI 正常 Core 无参数启动自行清除 pending、实际菜单正确、自然退出 0 已实测；单次启动 4071.0195ms，不冒充 10 次性能门。
- 实际界面缺陷：双包确认卡片缺少可见 owner 标题；公开 QDialog seam 先红后绿，添加标题；DPI 物理截图证实按钮未裁切，不因错误逻辑坐标捕获而修改布局。
- V3 真实深目录 LPAC host 已通过；Worker 的归档读取与 Python getpath 初始化仍有故障。native-04/probe-07/worker-03 生产构建及依赖审计完成，但生成验证签名候选的真实 Worker 在 getpath:635 失败，**不可标工程门通过**。
- 同类失败后已停止追加生产补丁，改用固定 CPython/PyInstaller 源码及自有原生 PathCch canary 查实际路径/初始化参数。隔离五项均为父端核验 true；不放宽 ACL/能力/Job/Win32k、不回退源码。
- 精确停点：构建仅诊断的 `builds/native-home-debug` 与 `builds/worker-home-debug`，用于打印自有测试目录的 home 长度和拼写；完成定位后再写公开 seam 回归、生产修复、真实 LPAC 复验。
- 本轮私钥未读取、未解锁；新正式 DLC 重签／Core 分发更新仍留到用户本地确认。人工安装／新账户／真实模型门未执行。无提交、推送、子智能体。


## 2026-10-06 收齐已有工作并暂停


- V1：仅 AI 的 Core03 无参数正常启动完成真实加载 receipt，实际菜单正确，原生“退出”自然退出 0；9 项身份绑定/UIA 驱动回归通过。驱动改用 Qt 接受的键盘 `WM_CONTEXTMENU(-1)`，不移动全局鼠标、不强退。
- 确认卡片缺少可见包 owner 标题：公开 `create_local_package_dialog()` seam 先红后绿，23 项本地意图相关测试通过；720/1100、长文案/字体检查。物理 DPI 截图仅本应用，未因逻辑/物理坐标误判改布局。
- V3：定位并修复冻结路径三层问题（MSVCRT 归档打开、CPython3.11 getpath 拼接、PYZ/native 路径），59 项相关测试通过；新增 seam 红测为 4 failed /25 deselected，绿测 59 passed /3.96s。
- 生产构建 native-05、helper probe-08、Worker-04 成功。**生成验证签名候选**在真实 LPAC 长/短路径各一次完成 host factory + Worker HELLO→SHUTDOWN→exit0；父端五项隔离均 true，无 ACL/能力/Job/Win32k 放宽，无源码或普通 subprocess 回退。不是正式 DLC 重签。
- 新 helper 完整原生权限 canary 通过：授权只读、scratch读写，未授权文件/账本/lease/凭据/DPAPI/注册表/桌面/网络/子进程拒绝；超时、崩溃、输出/内存限制、父端退出及 profile 恢复通过。网络负对照在 Winsock 初始化阶段被拒绝（10107），不谎称抵达 connect；正常 IPv4/IPv6 正对照连接成功。
- Core04 用已核对的正式公钥策略与 helper08 构建并审计：455,783,226B /1,424文件 /2,260 PYZ 模块；无 AI/识屏实现。**未执行 Core04 冻结启动矩阵，未重新签名或打包其分发产物。**

### 最新验证及边界
- full-01：4294 passed /15 skipped /14 warnings /796.84s，exit0；它早于最后的显式 runtime path 补丁。
- full-02 已收齐：**4298 passed /15 skipped /14 warnings /924.58s，exit0**；命令 `python -X utf8 -m pytest -q`（生成的 basetemp/cache 均在本轮拥有根），证据为 `acceptance-night-20261006/full-02.log` 与 `.exit`。已覆盖最终原生路径修复；运行启动后仅给验收驱动补了类型注解，该文件另经 mypy/9项回归通过，未重新运行整个受影响60文件 mypy。
- quality-02：Ruff 全范围、162文件 format、配置 mypy26、AI host mypy26、129文档链接、167报告/构建测试、tracked diff-check 通过。受影响 mypy60 初次在新验收驱动报7个类型推断错误；补显式 dict/list 注解后该文件 mypy 与9项专项通过。**未重跑整个60文件集合，不把 quality-02 初次总体失败改成通过。**
- 最新源码的 CPU 高负载三遍、Core04 四组合/业务/升级回滚/卸载重装、便携/导入实机、Setup真实安装更新卸载与完整性能样本均未完成；旧结果只为历史。


用户要求先总结交接，未开始验收不再启动。Core04构建/完整权限canary完成；最新full-02为已有运行，收齐后仅更新记录。原失败日志保留，59专项、9类型/驱动回归和完整LPAC门不等于新版正式分发。无暂存、提交、推送、发布、子智能体或个人数据操作。

交接封存文档验证（2026-10-06）：仅复核13份本次交接文档、322个本地文件链接及LOG索引锚点，全部通过（0.0842s）；报告纪律57 passed /0.67s；git diff --check exit0、暂存为空。不是新增Phase5A运行验收，也不覆盖此前受影响mypy60未复跑的事实。证据 `acceptance-night-20261006/handoff-doc-check.json`、`handoff-report-check.log`。

## 2026-10-06 用户授权当前检查点推送并恢复实施

本地/远程e2687be一致，178产品文本文件+5任务记录白名单，已保存逐项摘要/敏感位置扫描和约1MiB封存快照。无源码修改/暂存/提交/推送；准备新质量/全量/高负载门。无子智能体、真实安装、数据导入或私钥读取。

## 2026-10-06 授权检查点验证门收齐

4298 passed, 15 skipped, 15 warnings in 929.75s (0:15:29) /wall 931.253s；第1遍 361 passed, 1 skipped, 1 warning in 332.06s (0:05:32)，wall 336.500s /CPU median 100.0% /p95 100.0%；第2遍 361 passed, 1 skipped, 1 warning in 260.70s (0:04:20)，wall 264.844s /CPU median 100.0% /p95 100.0%；第3遍 361 passed, 1 skipped, 1 warning in 259.37s (0:04:19)，wall 263.437s /CPU median 100.0% /p95 100.0%。两轮驱动均冻结全部产品/测试Python SHA，源码未变。Ruff/162文件format及26/60/26文件mypy通过；167报告/构建相关通过。183逐项暂存，唯一敏感模式命中为加密PKCS#8格式常量检查，非密钥。纠正报告历史命令中不存在的测试路径，不猜测原始argv。当前仍未创建提交/推送，待最终文档门通过。

## 2026-10-06 续接实施：推送检查点后自动验收

### 已完成

- 已按本轮明确授权，将推送前已有白名单内容提交并推送到 `origin/codex/phase3-worker`：提交 `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`，远端 `ls-remote`、fetch 后远端跟踪引用与本地 HEAD 一致，`HEAD...origin/codex/phase3-worker` 为 `0 0`。该提交不是正式发布，也不包含生成物、原始日志、私钥或个人数据。
- 对 `scripts/build_feature_probe_native.py` 先补失败断言，再增加 `Py_SetPath` 分号失败关闭：候选路径含 `;` 时 native bootstrap 返回失败，不允许被解释为多个路径。聚焦测试先为 `20 passed, 1 failed`，修复后为 `20 passed in 0.72s`。
- 使用既有 `Worker04` 冻结产物完成真实非 probe 入口验收：子进程先通过交接 token 接管 Worker lease，发送 `HELLO`，返回 `lease_claimed=true`，再发送 `SHUTDOWN`，退出码 `0`；父端关闭自身 reservation 后版本占用为 `free`、剩余 lease 为 `0`。去掉交接 token 时退出码为 `77` 且没有协议输出。该证据证明冻结入口的租约顺序和失败关闭，不代表 Worker04 已包含本次尚未重建的分号补丁。
- 受影响专项回归：`267 passed, 2 skipped in 37.29s`。随后全量回归：`4298 passed, 15 skipped, 146 warnings in 712.39s`，exit `0`；该次显式设置 `PYTHONWARNINGS=default`，警告数包含已有 `ResourceWarning`，不把警告伪报为零。
- 本地质量复核：Ruff 通过、两份修改文件已格式化、`scripts/build_feature_probe_native.py` 受影响 mypy 通过、24 项聚焦测试通过。新文档记录写入后仍需再次执行文档链接、报告纪律、`git diff --check`。

### 当前边界

- 本次推送授权只覆盖提交 `70ff464` 之前已经存在的检查点；新增的分号失败关闭源码/测试及本节记录目前是**本地未推送修改**，不得把远端分支描述为已包含它们。
- 生成总量当前约 `9.728 GiB`；若按既定 `2.5 GiB` 构建预留直接重建 Core/Worker，理论峰值会超过当前 `12 GiB` 上限。因此本轮没有擅自启动新的大体积生产构建，也没有触碰旧人工验收产物。`worker-04`、`core-04` 仍按其各自构建时间和摘要记录。
- 双包 Core04 完整四组合矩阵此前仍有 `both` 组合因 `management_confirmation_timeout` 未完成；该失败已保留原始诊断，不宣称通过。新版正式 DLC 重签、Setup/普通 ZIP/便携 ZIP重建、真实安装更新卸载、迁移/干净环境和人工模型体验仍未完成。
- 未读取仓库外私钥，未执行正式签名解锁、真实数据导入或个人数据操作；没有强制关闭 Core、设置或外部进程。

### 准确续接步骤

1. 先执行本节记录后的文档链接、报告纪律和 `git diff --check`；确认工作树只包含本轮两份源码/测试及同组记录变更。
2. 在不超过空间合同、且用户明确允许新的构建材料后，重新构建带分号修复的 native/helper/Worker/Core，再跑正式 trust/probe 和四组合矩阵；旧 Worker04 只能作为历史验收证据。
3. 最后集中等待用户执行私钥可信解锁、真实 Setup/便携/导入/模型/干净环境人工门；未执行门逐项保持未验收。

### 用户可见效果与限制

当前源码的路径封闭性更严格，正常冻结 Worker 不能在没有父端租约交接时运行；既有已推送检查点远端可复核。正式新版分发物和真实安装/迁移体验尚未产生，不能把当前结果当成 Phase 5A 完整发布验收。

## 2026-10-06 夜间续接：冻结 Core 原生菜单验收收尾

### 操作与根因

1. 收取 `pytest-full-02`：`4299` 项通过后唯一失败为 `tests/test_architecture.py::test_window_py_line_budget`，实际 `window.py=4672`、预算 `4671`。这不是运行回归，而是本轮为键盘上下文菜单增加说明 docstring 后多 1 行。
2. 核对失败前两次验收：v11 的菜单等待超时与 v12 的前台激活拒绝。确认冻结窗口使用 `WS_EX_NOACTIVATE`，因此“先激活再发菜单”方向错误；没有继续追加抢焦点或屏幕坐标猜测。
3. 修复产品 seam：`PetWindow.contextMenuEvent()` 识别 `QContextMenuEvent.Reason.Keyboard`，以稳定身体锚点调用现有菜单逻辑；修复驱动只发送键盘语义 `WM_CONTEXTMENU(-1)`，不调用 `SetForegroundWindow`。
4. 重新构建 `core-11b`，携带修复后的 `pet/window.py`；不读取私钥、不修改正式包和正式信任策略。

### 自动与实机证据

- screen v13：真实 Windows 冻结 no-chat Core，通过生产加载确认清除 pending，并由自有 UIA 读取实际菜单、调用自有 `退出`；结果 `CASE_PASSED`，启动 `3763.6489 ms`、RSS `191500288`、线程 `34`、自然退出 `0`。
- empty v14：同一 core-11b 空安装根真实启动，结果 `CASE_PASSED`，启动 `2830.1063 ms`、RSS `203771904`、线程 `33`、自然退出 `0`；菜单没有识屏入口。
- 生成根进程核对后，core-10 两个遗留验收进程已自然停止；再次扫描为 `NO_OWNED_ACCEPTANCE_PROCESSES`。
- 受影响专项、架构门与格式门：`66 passed in 10.64s`；Ruff `All checks passed`；12 个修改文件 `ruff format --check` 通过；`git diff --check` 通过。
- 最新全量命令：`QT_QPA_PLATFORM=offscreen`，`TEMP/TMP` 指向 `.scratch/phase5a-local-distribution/acceptance-night-20261006`，`python -m pytest -q --basetemp=.../pytest-full-03`；结果 `4300 passed, 15 skipped, 14 warnings in 671.83s (0:11:11)`，exit `0`。

### 失败复盘与边界

- v11/v12 没有被改写为通过：它们分别证明“仅改驱动仍不足”和“不能要求冻结 no-activate 窗口激活”。本次采用产品键盘语义处理与无激活驱动后，v13/v14 才重新构建并通过。
- 非 offscreen 相关拖拽族此前为 `190 passed / 6 failed / 4 warnings`，失败是显示几何夹紧期望，不属于本次菜单改动；没有用无关修改掩盖。
- 生成物 `.scratch/.../core-11` 空占有目录仍保留，只有自有构建证据，未扩大清理范围。

### 当前停点

代码和记录保持本地 WIP；没有提交/推送。现阶段可自动验证的生产菜单、空安装与全量质量门已收齐。剩余正式签名解锁、Setup/ZIP/便携真实安装、旧数据导入、干净 Windows、真实模型/截图与用户人工体验留给后续明确授权和人工确认。

## 2026-10-06 质量门补充

- `ruff check pet tests scripts` 通过；`ruff format --check pet tests scripts` 报告 `544 files already formatted`。
- `scripts/check_docs.py` 通过：`Markdown link check passed: 129 files scanned`。
- `python -m mypy pet` 失败，准确输出为 `Found 439 errors in 46 files`，涉及项目原有宽范围类型债务；没有修改这些无关文件。
- 对本轮 `pet/feature_management.py`、`pet/window.py`、`scripts/validate_phase5a_delivery.py` 的定向 mypy 仍显示 `pet/window.py` 既有 19 个位置错误，新增键盘上下文菜单行没有诊断；因此只记录为限制，不宣称 mypy 通过。


## 2026-10-06 自动实施：补齐生产冻结验收

### 1. 真实失败复现

- 诊断 Core `core-diag-screen-20261006-b` + screen v23 在生产冻结 probe 阶段失败。
- 真实 stderr：`Could not load PyInstaller's embedded PKG archive from the executable`；路径显示 helper 位于深层验收 `HOME/AppData/Local/...`。
- 临时 trace 证明 `self_check_done`、`management_action_done`、`management_emit`、`management_deliver` 均返回 `host_probe_failed`，因此没有将问题误判为 Qt 信号/事务锁死。
- 临时 trace 已恢复删除，源文件不保留诊断开关。

### 2. 根因修复

- 在 `pet/feature_management.py` 增加 Windows known-folder 解析，使用 `FOLDERID_LocalAppData`；深路径采用短的、按 data-root identity 隔离的 probe 根。
- 在 `tests/test_feature_management.py` 增加 legacy worker 深路径回归，断言短根、identity 目录、功能 ID 和无副作用。
- 受影响测试 `20 passed in 2.51s`；受影响代码 Ruff/format/mypy 通过。

### 3. 最终本机冻结验收

- Core `core-05-final-20261006` 构建耗时 `127.475s`，dist `455,783,648B`，EXE SHA256 `64C5686717906E1112D1AAE9BC1AEAD5CEBC509F5F8EB6CCD4D9842336201382`。
- AI final：`CASE_PASSED`，启动 `3750.3321ms`，RSS `198,176,768B`，37 threads，exit 0，菜单/状态/生产加载 receipt 正确。
- screen final：`CASE_PASSED`，启动 `4368.526ms`，RSS `183,676,928B`，32 threads，exit 0，菜单/状态/生产加载 receipt 正确。
- 两个冻结验收根均完成回收检查；无活动 dsh Core/probe 进程。

### 4. 质量门

- `python -m pytest -q`：`4301 passed, 15 skipped, 14 warnings in 745.31s`。
- `python -m ruff check .`：通过。
- 受影响 12 个 Python 文件 format-check：通过。
- `python -m mypy --follow-imports=skip pet/feature_management.py scripts/validate_phase5a_delivery.py`：通过。
- `python scripts/check_docs.py`：129 Markdown 文件通过。
- `git diff --check`：通过。
- 仓库全量 `ruff format --check .` 仍报告 7 个历史 Markdown 未格式化；未改动这些文件。

### 5. 未完成门

本停点不包含真实 Setup/便携/旧数据导入/干净环境/另一台机器/真实模型和截图/Authenticode/SmartScreen/正式发布，也不包含本轮新 Git 提交或推送。

## 2026-10-06 用户人工验收：安装后真实 Core 启动确认

时间：2026-10-06 22:29:19 +08:00（Windows 本机，用户人工确认）

- 受控人工根：$root\manual-user-acceptance-20261006-v2。
- 用户关闭本地官方包确认窗口后，续接器仅等待安装进程自然退出，再启动正常生产 Core；没有强制结束进程、没有提交伪造 receipt，也没有放宽隔离策略。
- 用户可见结果：设置中的 official.ai-chat 与 official.screen-understanding 均正常；两个入口恢复；原设置保留；未出现错误或恢复提示。
- 账本复核：AI ctive=1.0.1、nabled=true、
evision=4、pending_transaction=null；屏幕理解 ctive=1.0.0、nabled=true、
evision=4、pending_transaction=null。
- 事务复核：两笔安装事务均为 phase=completed、ccepted=true、self_check_passed=true；生产 Core 当前由同一受控数据根运行（PID 31308，复核时仍在运行）。
- 本条只证明本机用户人工完成了“本地 ZIP 安装 → 确认 → 沙箱自检 → 状态切换 → 正常 Core 启动加载确认 → 入口/设置恢复”门；不替代真实模型请求、真实截图识别、Setup/便携/更新/卸载、干净环境或正式发布者认证门。

## 2026-10-06 用户人工验收补充：冻结 Core 凭据边界

时间：2026-10-06 22:56:43 +08:00（Windows 本机）

- 用户反馈：关闭刚才运行的冻结 Core 后，源码入口可以执行 AI 操作；这不能直接证明冻结 Core 失败，必须以冻结 Core 自己的日志和账本为准。
- 冻结 Core 的受控人工根为 manual-user-acceptance-20261006-v2，启动日志记录的实际异常为 pet.credentials.CredentialError: credential_missing。日志没有暴露任何凭据内容。
- 同一根目录的安装/加载证据仍完整：AI ctive=1.0.1、屏幕理解 ctive=1.0.0，均 nabled=true、pending_transaction=null；两笔事务均 phase=completed、self_check_passed=true。
- 结论修正：冻结 Core 的“安装、自检、状态切换、正常启动加载确认”通过；“使用真实 Provider 凭据完成 AI 请求”尚未通过。源码使用真实数据根时成功，不能替代冻结 Core 的独立验收。
- 不把真实用户凭据复制到验收根，不修改冻结 Core 的 credential resolver，不把 credential_missing 降级为成功。后续如继续人工门，只能由用户在冻结 Core 的隔离设置中通过本地可信界面自行录入测试凭据，或使用明确的本地模拟 Provider；凭据不得通过聊天、命令行或日志传递。


## 2026-10-06 屏幕理解提示误导修复

1. 读取受控 v2 配置，确认 `official.screen-understanding.settings.migration_state=confirmed`，绑定 profile `shared` 存在，但 `credential_ref` 为空；结合 `VisionConfigService.resolve()` 的 `credential_missing` 分支确认这是凭据缺失而非迁移未完成。
2. 先增加两条失败回归：manual host 对 `credential_missing` 给出补 Key 且无需重复迁移；settings page 对空 `credential_ref` 给出相同语义。
3. 实现原因化文案和 profile/绑定/凭据分层状态；保留未知原因的旧兜底，不扩大执行权限，不改变凭据存储。
4. 运行 screen 相关 4 个测试文件，结果 `53 passed in 16.48s`；Ruff check、format-check 和 `git diff --check`（受影响源码/测试范围）通过。
5. 重新构建 `worker-03-screen-ux-20261006` 与 `core-06-screen-ux-20261006`；构建清单明确为 human acceptance only，未作为 release。
6. 创建 v4 隔离人工根和脱敏 fixture；PowerShell script parse、fixture JSON parse 和合同断言通过。
7. 全量回归最新结果为 `4298 passed, 14 skipped, 14 warnings, 6 failed`，失败可单独复现于 `test_drag_move_coalescing.py`，已记录而未修改无关代码。

准确停点：没有替用户录入真实 screen Key，没有发起真实 screen request，没有提交/推送。


## 2026-10-06 最终复核：屏幕 UX 验收包证据

- 使用 Python `zipfile` 直接读取 4 个 screen 包 ZIP，均确认包含修正后的 `host/manual.py` 与 `host/settings.py`，并确认包内含“补填视觉 API Key”“无需重复迁移”文案；未依赖未安装的 `7z` 命令。
- `screen-config-fixture.json` 的原始字节无 UTF-8 BOM，JSON 合同断言通过；不会因 PowerShell 编码导致产品配置读取失败。
- v4 安装脚本 PowerShell AST 解析通过，fixture 只在配置不存在时复制；不会覆盖用户后来在隔离 UI 中保存的凭据引用。
- 受影响源码/测试范围的 `git diff --check` 通过。整个 WIP 工作树仍有历史任务记录中的尾随空白告警，未进行大范围格式化，避免改写既有证据。
- 尚未代替用户输入真实 screen Key 或发起真实 screen 请求；这仍是唯一需要人工执行的本次修复验收门。

## 2026-10-06 — AI 对话凭据显式补齐修复与人工验收包重建

- 用户反馈：屏幕理解继续提示配置/迁移，尽管 AI 对话中已有 DeepSeek API Key；同时人工启动再次遇到 self_check_isolation_not_enforced。
- 根因：迁移源只读取旧的顶层 chat，没有读取 plugins.official.ai-chat.chat；已有屏幕配置但 credential_ref 为空时没有“显式补齐”入口。
- 修复：支持从 AI 对话配置读取脱敏的 provider 元数据并在用户明确确认后写入屏幕理解自己的安全存储；保留用途隔离、不可覆盖已有绑定和安全门；新增
epair_missing_credentials()；更新设置文案与缺失凭据按钮。
- 自动验证：	ests/test_screen_ai_migration.py 等相关测试共 55 passed in 14.48s；Ruff 通过。
- 重新构建：core-12-screen-ai-credential-20261006，使用独立 Worker worker-03-screen-ux-20261006 与固定 probe manifest 摘要；构建耗时 448.852s，生成 chat/no-chat Core、屏幕理解包和 manual-artifacts.json。
- 已启动：manual-user-acceptance-20261006-v5-ai-screen-credential/launch-install-screen-chat.ps1，可见窗口标题为“安装本地官方扩展：分别预检和确认”。
- 注意：未记录或输出任何真实 API Key；本次人工验收根目录使用隔离的 APPDATA、LOCALAPPDATA、HOME 和 TEMP。


## 2026-10-06 — `worker_probe_failed` 根因修复与 v6 启动确认

- 用户人工验收反馈上一版安装预检结果为 `worker_probe_failed`。状态账本保持未安装，证明没有误提交候选版本。
- 根因确认：`worker-03-screen-ux-20261006` 的旧证据范围标记为非 synthetic，但产物没有 `_internal/_dsh_probe_native.pyd`；Worker 在 `screen_entry` 的隔离检查处返回 77，未发送 `HELLO`，因此适配器返回 `worker_probe_failed`。这不是密钥、迁移或状态损坏。
- 修复：`scripts/build_screen_delivery.py::verify_worker_inputs` 对真实 Worker 强制检查 `headless-input.json`、原生隔离叶及其摘要与 artifact 清单一致；新增回归测试，防止普通 PyInstaller Worker 再次进入人工验收包。
- 自动验证：`tests/test_screen_delivery_build.py tests/test_screen_worker_build.py tests/test_feature_probe_build.py tests/test_feature_probe_adapter.py tests/test_feature_probe_windows.py` 为 `80 passed, 1 skipped`；Python Ruff 模块检查通过。
- 使用 `worker-06`（`synthetic_boundary=false`，含 `headless-input.json` 和 `_internal/_dsh_probe_native.pyd`）重建 `core-13-screen-ai-credential-native-20261006`；chat/no-chat Core 均构建成功。
- 新屏幕包 SHA-256：`98032a5e20b0b5649adc0945e6ccc3059a444dfae1ce1dddca3ceeb4a3ccac49`；包内确认存在 `_dsh_probe_native.pyd`。
- 新建隔离人工根：`.scratch/phase5a-local-distribution/manual-user-acceptance-20261006-v6-ai-screen-credential-native`。
- Codex 先正常关闭安装管理窗口，再启动 v6 chat Core；启动后 `state.json` 从 `pending_transaction=tx-...` 变为 `pending_transaction=null`、`revision=4`，证明真实 Core 启动加载确认成功。
- 未读取或记录真实 API Key；未强制终止进程；未提交/推送。


## 2026-10-06 — v6 Core 用户可见启动反馈

- 用户反馈：启动初期先看到灵动岛和控制台，角色稍后出现；随后角色正常出现，用户判断“有点慢”。
- 解释与边界：当前人工验收构建为 `--console`，控制台用于观察探针/启动日志，不代表正式发布 UI；角色延迟涉及 Core 初始化、资源加载、pending 事务真实确认、租约接管和 WebM 窗口初始化。
- 状态：本次可见启动未判定为失败；“首次启动角色出现延迟”登记为待量化性能项，未宣称正式分发性能达标。


## 2026-10-06 产品行为修正：取消 screen 首装主动迁移

用户确认屏幕理解应直接显示默认初始配置，后续手动编辑；若已有有效 screen 自有配置则正常继承。此前为了修复“AI Key 已存在但 screen 仍提示配置”而加入的迁移/补齐 UI，反而造成迁移失败和人工验收阻塞，因此已移除 UI 入口和相关提示。显式 `VisionMigration` 模块只保留为内部兼容接口，不由正常启动/首装调用。

源码改动：`models.py` 增加 credential-free `VisionSettings.default()`；`host/settings.py` 使用默认配置并移除迁移 UI；`host/manual.py` 改为独立配置提示；对应设置、人工 host 和显式兼容测试已通过 `51 passed`。

当前停点：下一步重建新的人工验收产物，不读取用户真实凭据，不提交/推送。用户最终只需确认设置页默认地址/模型/路径显示正确，再按自己的需要填写 API Key 并测试识屏。
## 2026-10-06 产品口径修正与实施记录

用户补充确认：Phase5A 的选装应只发生在 Setup 安装向导；离开 Setup 后，用户自行下载 ZIP 或准备目录并放入相关文件即可通过本地选择激活，不应要求当前公钥/私钥打包和验证。根据该决策重新评估现有实现，结论是原“签名分发”主路径过度设计，保留结构/完整性/LPAC 约束但移除 Phase5A 的发布者认证前置条件。

已实施：

- `pet/plugins/package_trust.py` 增加 `allow_local_packages` 与统一 `accepts_descriptor()` seam；本地用户显式选择得到 `local_user`，不要求或验证签名。
- 事务、probe、版本租约、安装状态和构建策略统一改用该 seam，避免某个边界仍硬编码 `trusted_official`。
- `scripts/build_screen_delivery.py`、`scripts/build_feature_management_manual.py`、`scripts/build_feature_management_delivery.py` 的主路径停止生成密钥和 `manifest.sig`；历史签名参数只留给兼容测试。
- Setup、扩展管理 UI、Phase5A 设计/README/项目入口/验收脚本同步改文案，明确 Setup 选装与普通 ZIP/目录导入的差异。
- 新增 `tests/test_phase5a_local_activation.py`，已验证无签名本地包可通过、清单/文件篡改仍拒绝、signed-only verifier 仍拒绝。

当前验证：新专项 `3 passed`；先前受影响 focused `53 passed`。下一步为新 GUI 构建与真实 Windows 验收，不读取用户真实密钥，不提交/推送。


## 2026-10-06 收尾工作日志：probe 根因、真实验收与清理

### 1. 本地激活重写

将 Phase5A 从“默认签名分发”收口为“Setup 选装 + 非 Setup 用户显式选择普通 ZIP/目录”。`FeaturePackageVerifier` 在显式 `allow_local_packages` 下返回 `local_user`，所有安装/探针/租约/启动边界统一调用 `accepts_descriptor()`；固定 feature/factory、compatibility、路径/大小/文件数量、manifest SHA-256、事务和 LPAC/Worker 仍然是硬门。主构建路径不再生成私钥、公钥锚或 `manifest.sig`。

### 2. headless probe 启动根因

用 fresh AI 包和 probe09 复现得到 `host_probe_failed`。继续下钻发现 PyInstaller 的 `pyi_rth_multiprocessing.py` 在 headless 入口前导入 `socket` 并调用 `WSAStartup`；LPAC 预期拒绝网络初始化，返回 WinSock error 10107，导致未进入业务 probe。`scripts/build_feature_probe.py` 现在排除未使用的 `multiprocessing` runtime hook；probe10 的 `pyi_rth_multiprocessing.py` 不再出现，source sandbox 的 AI host-only outcome 为 `host_valid=true`、`isolation_enforced=true`、`trust_status=local_user`。

### 3. 最终构建与真实 UI 验收

manual20 使用 probe10、worker08 和 native05 构建完成，构建耗时 295.814 s；AI ZIP 118,378 B，screen ZIP 24,455,393 B，均无 `manifest.sig`；probe manifest SHA-256 为 `915f87620c3a84219bf7310e0ff805ff3c25d0b05e2f0ba2d4b95c5eba9a3232`。
v13 使用真实冻结 Core 运行 `empty` / `ai` / `screen` / `both` 四个选择矩阵：均 `CASE_PASSED`、exit code 0、pending transaction 为 null；启动耗时分别约 3137.282 / 3444.613 / 4069.834 / 4686.232 ms，RSS 分别 185,180,160 / 205,598,720 / 211,054,592 / 193,515,520 B；菜单只出现已选功能。AI-only v12 也通过，修复了旧管理确认超时。

### 4. 质量门

已完成 focused 144 passed in 47.82 s；全量 `python -m pytest -q` 结果 `4313 passed, 15 skipped, 14 warnings in 717.00s`；Ruff check、Ruff format-check、`git diff --check` 均通过。14 warnings 为 Qt/既有测试告警，没有失败。

### 5. `.scratch` 清理

在清理前生成 `cleanup-manifest-20261006.json`，逐目标解析并验证绝对路径位于 `.scratch/phase5a-local-distribution` 内；第一次递归删除遇到历史 pytest junction，随后改用不跟随 reparse point 的逐文件/目录删除器完成清理。247 个目标全部删除（其中 12 个在重试前已消失），共计划/删除 221,340 文件、19,215,410,032 B；清理后阶段目录为 9,623,836,176 B / 53,299 文件。保留 manual20/probe10/worker08/native05、v8-v13 和正式 Markdown 记录。未触碰项目外路径。

### 6. 收尾停点

本机找不到 `ISCC.exe`，因此未宣称真实 Setup 向导通过；用户只需在有 Inno Setup 的环境确认 `ai`/`screen` 任务、旁置包导入和实际安装体验。真实 Provider/视觉请求也不能由 Codex 替用户输入凭据。当前未提交、未推送；未来正式签名另开发布任务，不再阻塞本地激活收尾。

## 2026-10-07 操作记录：编译 Phase5A Setup

1. 查阅 `docs/ONEDIR_PACKAGING.md`、`packaging/core_webm.iss` 和 `.scratch/phase5a-local-distribution/setup-compile-279.py`，确认当前 Core Setup 编译入口和历史成功参数。
2. 确认本机已有 `E:\tools\InnoSetup6\ISCC.exe`，版本 `6.7.3`，未执行安装操作。
3. 直接使用 manual20 Core 长路径编译失败；测得最长路径 `343` 字符、超过 `260` 的路径 `649` 条，判断为 Inno legacy path handling，而非 `.iss` 语法错误。
4. 将 Core 原样复制到短路径 `.scratch/p5a-core-20261007`（最长路径 `242` 字符），重新编译成功：`116.031 s`、退出码 `0`。
5. 生成 Setup 大小 `227,324,912 B`，SHA-256 `3780ec4355e4ab0b0bce6285c8fe4aa07289877af43e5bab333f3965a5c9395b`；旁置 AI/screen ZIP，并保存 `setup-acceptance-manifest.json` 与 `iscc-compile-v3.log`。
6. 未运行实际安装向导；`Get-AuthenticodeSignature` 为 `NotSigned`，不把它误写成正式发布门通过。前一日“找不到 ISCC.exe”的记录保留为历史事实，但当前状态已被本条更新。


## 当前修复轮 R01–R07（2026-10-07）

用户已明确授权实施 Core 统一 API、识屏恢复及 Core-only 卸载修复。**R01 进行中，R02–R07 待实施；此前“无待修代码”仅限原 T 轮，不再是当前状态。** 人工验收已暴露缺陷，Phase5A 不可正式关闭。原 T 结果/产物哈希保留作历史，不能用于修复后的验证。

已确认 red 诊断：AI 外部设置保存后运行时旧快照；自动识屏关闭时 unchanged apply_config 取消手动请求；当前生产 Worker ZIP 正常启动缺 pet.official_features（握手前 exit 1）；Core 卸载要求先清两包且被 staging 阻止。相关基线 81 passed 但未覆盖缺陷。

本轮边界：不读真实 Key、不截真实屏幕、不收费调用/系统安装卸载、不更改代理、不清未知 staging、不提交推送、不开子智能体。保留 69 修改+9新增的既有脏树。精确停点：计划已落盘，下一步写失败回归并运行 red，尚无产品修复。

详细合同见原收尾计划末尾 R 章节。目标候选 Core4.2.2/AI1.0.2/Screen1.0.1。真实 Provider/屏幕/新 Setup 用户门仍未执行。


## R 修复轮实施检查点（2026-10-07）

- R01：四项根因回归已先 red（R01-red3.log：4 failed），产品修复后新中央接口/四类窗口合并专项 17 passed / 7.68 s。迁移/设置/余额另先 red：5 failed / 3.64 s。原 T 轮记录不作当前通过证据。
- R02：已实现 Core api_ports/api_config、独立 OS 凭据引用、通用 owner/purpose 授权、CAS+journal、显式 ApiMigration；新增 Core 设置/异步退出门及最小文字 probe。尚需边界/故障测试及全量验证。
- R03：已接入四类窗口请求前解析与校验失败保留输入、中央余额用途解析、Core 常驻入口；DLC 原入口只读/跳转仍在收口。
- R04：Worker 闭合源码已补 neutral official_features；自动配置变更已按请求取消，不旋转共享 generation；生产冻结 HELLO/READY 尚未重建验证。
- R05：系统维护入口已去除逐包事务并保留 CoreRemovalPermit 身份及 scoped 集成清理；尚需新增失败矩阵与安装器实机用户门。
- 最近相关集成 48 passed / 1 failed / 32.77 s：剩余是旧完整 Core 的设置构造接了新 API 导致兼容 probe 失败，正在按 build-owned legacy 能力修复；不能称当前全绿。
- R06/R07：未执行本轮全量/重建/实机/性能/用户验收。候选版本还未 bump，旧交付物不得用于本轮验收。
- 未提交、未推送、未使用子智能体、未读真实凭据/截图/收费调用/实际系统卸载，未清理生成物。

精确下一步：完成 DLC API 编辑跳转及 Screen 用途订阅、文件解读授权；补中央设置/迁移/Worker/卸载矩阵回归，专项通过后执行全量、负载三遍和产物硬门。


## R 修复轮检查点：专项扩展与构建空间待确认（2026-10-07）

- 已通过相关集成：R02-R05-related-5.log，74 passed / 47.50 s。此前 legacy 完整 Core 的 API 兼容错误已修复；不是沿用官方 factory 特判，新冻结 Core 走通用授权端口。
- 新边界先 red：R02-R04-boundary-red2.log，8 failed / 19 passed / 13.14 s（参数类型、迁移编辑后来源不一致、缺少生产正常启动验收入口）。对应 green2：64 passed / 27.52 s。源码 Worker 使用真实 QProcess 与 OS 租约完成 HELLO/READY/自然退出，未发屏幕或网络请求；不能替代冻结 Worker 验收。
- 余额撤权在飞/排队迟到结果先 red：R03-balance-red.log，2 failed / 5.61 s；正在实施 GUI 与后台双重授权检查。经典设置读取旧 Key 先 red：1 failed / 5.94 s；已改只读中央绑定及跳转，待合并验证。
- R06 全量第 1 次仍在运行，已有失败，且运行期间源码仍在收口：只作诊断，不作最终全量 green。Ruff、最终全量、负载三遍、性能/实机报告尚未完成。
- 构建预算检查：阶段生成物 6,191,575,960 B / 50,483 文件，6 GiB 上限仅余约 239 MiB。已向用户请求删除 acceptance-night-20261006（2,090,299,981 B）与 delivery-candidate-200（767,659,787 B）两处旧生成物；尚未得到答复，未执行任何删除。源码/阶段记录/implementation-20261007 旧 Setup/delivery-set-293 保留。不得绕过预算重建。
- R04 冻结正常启动、R06 全重建和 R07 新 Setup 用户验收均未完成；旧 T 轮产物/通过记录仅为历史。Phase5A 未关闭，未提交推送，无子智能体、真实 Key/截图/付费调用/系统卸载/代理修改。

精确下一步：合并新增消费/撤权/异常测试并修复全量实际失败，完成格式/静态门与性能报告；重建需用户明确确认上述两个精确目录的删除。

## R06 复跑排查与最终源码再冻结

- full-final-2：631.93 s，0xC0000005，Worker 验证共享事件循环；另五项旧合同的失败已独立复现并更新，57 passed。
- full-final-3：640.48 s，0xC0000005，设置 app.exec；源码 621 个文件不变、约 80% 前无断言红灯。原始日志与 source receipt 全保留。
- Qt 最小诊断族 65 passed / 9.06 s，没有复现原生崩溃；原生回调根因仍不确定，不能写成已证实产品故障或已修根因。独立设置测试改为实际设置进程形状，Worker gate 同样使用独立验证进程，不模拟事件循环。
- API 表面 token：R03-api-theme-test-red.log 4 failed / 1.03 s；设置 scroll= settingsScroll、status=settingHint 后 Qt/API/独立设置/Worker 71 passed / 12.27 s。
- R06-ruff-freeze4.log：Ruff all passed、117 files already formatted、diff check 无输出。full4 与 core-final/c09/setup-final 重建开始；新结果待退出，历史 Core/Setup v1 不删除。

## R06 默认全量第三次原生崩溃与最终产物审计

- Windows 主机原始时间：full4 2026-10-08T01:46:21.797805+08:00 至 01:57:12.956379+08:00；651.15 s，exit 3221225477。主线程位于 WorkerSupervisor.wait_for_stopped_for_tests 的 QEventLoop 清理循环，无断言失败到约 79%；621 份 Python 源码没有变化。原生日志/源快照保留，不声明根因已定位。
- Core/Setup 最终重建成功；只读审计首次因自有脚本保留旧 core-current 路径失败，日志保留。修正审计脚本路径后 exit 0，root207/stage212/resources1000/bundle2103 全部匹配；没有修改产品源码。
- 新 Core 四 profile 实机冻结验证 exit 0；DLC 新版 startup receipt 均清除，菜单对应能力正确，全部自然退出 0。
- 启动全量分组验证：完整收集 4431 测试/262 文件，5 组，逐项 nodeid 哈希并集核对，无删选；默认单进程未通过门保留为工程限制。
<!-- R08_OVERWRITE_SETUP_20261008 -->
## 2026-10-08 R08 工作记录：Setup 覆盖安装残留

1. 读取用户本机 Core maintenance 日志末尾，确认 `code=2/reason=bundle_inventory`；只记录脱敏状态，不读取任何 Key。
2. 比较当前干净候选与现有安装的 frozen probe 文件数：`64` 对 `85`；确认覆盖复制未删除旧文件是直接根因。
3. 在 `tests/test_core_setup_template.py` 先写回归并运行：`1 failed`，保存 `R08-overwrite-red.log`。
4. 在 `packaging/core_webm.iss` 增加 `[InstallDelete] Type: filesandordirs; Name: "{app}\_internal\feature-probe"`。
5. 回归转绿：Setup 模板 `5 passed`；Ruff、format check、diff check 通过。
6. 用 Inno Setup 6.7.3 在 `.scratch` 隔离目录编译/运行最小安装器，结果 `compile_exit=0, run_exit=0, stale_exists=False, fresh_exists=True`。
7. 重新编译产品 Setup：`246,922,162 B`，SHA-256 `8f53f5916285299dfddb7e41b5747aec5e049502722d422110b9ada8fc6fcc9b`；官方 ZIP 预检通过。
8. 设置 `QT_QPA_PLATFORM=offscreen` 后全量 pytest：`4418 passed, 15 skipped, 14 warnings`；未运行用户实际 Setup/卸载。

本记录对应的证据均在 `E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\repair-20261008-setup-overwrite`；无提交、无推送、无真实数据清理。

<!-- R09_POLICY_UNINSTALL_20261009 -->
## 2026-10-09 R09 工作日志

1. 根据用户“已经退出桌宠”的反馈复核卸载失败边界；在隔离环境确认 Core/Worker/卸载器占用不是已证实根因，发现外部 profile bridge 引用可能被旧清理路径误锁定，先保留 red 证据。
2. 修正 `pet/agent_link.py` 的 Core-owned 引用判定与 `pet/core_maintenance.py` 的脱敏诊断日志；R09 卸载相关专项 `79 passed`。
3. 用真实冻结 Core 做 UIAutomation 验收时发现新的交付回归：本地 DLC 确认按钮禁用；从 PYZ 检查确认 `pet.feature_build_policy` 未生成 `ALLOW_LOCAL_PACKAGE_ACTIVATION`。
4. 在 `tests/test_build_feature_release.py` 增加失败断言，先得到 `KeyError` red；在 `scripts/build_feature_release.py` 生成策略中补入 `ALLOW_LOCAL_PACKAGE_ACTIVATION = True`，green `2 passed`。
5. 重建 Core/Setup：Core `17,304,043 B`，SHA-256 `34F1FE81E92E2F177D568F52E6919B75CCBD2E496F4FED7A1CB525E9D5207C3F`；Setup `246,781,460 B`，SHA-256 `7FB5C31657B962F9861745E046323E29DAF22077E314CF1E256352F269C5F98F`。
6. 隔离 computer-use/UIAutomation：`empty/ai/screen/both` 均通过，startup 约 `8916/3139/4414/4723ms`，四案自然退出 0；没有真实 API、屏幕或用户安装卸载。
7. 受影响专项 `107 passed in 63.54s`；全量 `4420 passed, 15 skipped, 15 warnings in 831.71s`；Ruff、format、diff check 通过。
8. 写入本次 HANDOFF/STATUS/PLAN/SUMMARY/WORKLOG/PR 报告；当前停在用户人工验收，未提交/未推送。


<!-- U_PORTABLE_SETUP_20261009_WORKLOG -->
## 2026-10-09 U01–U06 范围切换：Setup 项目目录安装与全目录卸载

用户明确要求把 Setup 改为项目目录自包含模式：每次显示路径页，新安装只进安全空目录，合法本产品目录可更新；卸载不再纠缠 DLC owner/factory、ledger 或 staging，而是清理项目目录程序与 `data\plugins`，个人 `data` 默认保留并可二次确认删除。已在 PLAN/STATUS/HANDOFF/SUMMARY 顶部登记新权威合同；R/T 记录保留为历史，不删除。

操作边界：不提交、不推送、不覆盖稳定版、不读取真实 Key/屏幕；只在工作区独立临时目录做构建和破坏性验证。下一步先补 U01 red 回归，再修改 Inno native gate、RuntimeLayout、构建 wrapper 和卸载流程。

## S04 补充（2026-10-10，实施前登记）

- 真实冻结 Core 已从项目内 data/feature-runtime 启动生产 Worker 并完成租约/READY；但退出码 62097，不算通过。日志显示启动前包校验耗时被心跳计时计入，尚未 READY 的新进程被误报 heartbeat timeout。补真实进程 red，心跳只在 READY 后启用，握手仍由独立限时监管；继续验证自然退出。
- 首次全量 4 failed / 4486 passed / 15 skipped / 15 warnings（800.37s）；4 项是旧 4.2.3/Screen1.0.2 版本断言，更新到本次合同后重跑，高负载未启动，不记通过。
- 用户追加：新候选核对 Setup 内 EXE 与快捷方式的鲸鱼娘图标；不清全局图标缓存，不修改真实安装目录。已抽取新 Core/Setup 内10帧与 assets/icon.ico 全相同，仍需最终交付核验。
- 用户追加：API 测试按钮旁显示测试中、成功/失败和实际 HTTP 状态码；网络/TLS/超时没有 HTTP 状态时用明确错误类型，原说明放旁边备注。只改反馈 UI/探针结果携带，不改保存、密钥、API 路由或 Setup 语义；先补 red，明暗720/1100实机检查后重建 Core/Setup。

实际效果：识屏启动不误耗心跳预算；测试连接可一眼看到结果码，不把测试当保存。真实服务最终仍待用户验收。


## 2026-10-10 16:40 MOD 可运行检查点同步

- Phase5A 当前最新产品变化是本地 MOD 管理中心与作者 v1；本地 M05 检查点已完成 final4 Core/Setup 构建和冻结生产 Worker/Settings 烟测。
- 自动化收口：full12 4578 collected / 4563 passed / 15 skipped；Ruff、diff check 通过。满 CPU highload12 的已知非阻塞失败保留在 `docs/PR-REPORT-MOD-CENTER-V1-2026-10-10.md` 和 `.scratch/mod-authoring-v1/STATUS.md`。
- 本轮只做显式文件本地提交，不推送；用户人工验收仍是新 Setup 安装/升级/卸载、真实 API/余额/识屏和不同 Windows 实机行为。


## 2026-10-10 16:57 本地提交完成

- 可运行检查点已在当前分支创建本地提交；未推送远程。提交 SHA 以 `git log -1` 为准。
- 97 个显式交付文件已纳入；构建目录、日志、缓存、旧候选和真实安装目录仍未纳入。
- 用户验收边界不变：真实 Setup 安装/升级/卸载、真实 Provider/余额/屏幕，以及高负载限制的后续复验仍单独区分。
