# Setup 项目目录安装与卸载工程报告（2026-10-09）

> 更新：2026-10-09T21:18:00+08:00。**U01–U06 工程实现、自动化、重建和隔离实机验收完成；待用户确认真实功能体验**。本报告优先于历史 R 的“Core-only 保留 DLC”安装器合同。当前产物为人工验收候选，不是正式签名发行；Phase5A 待用户真实功能确认，不正式关闭。
>
> [设计](plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md) · [PLAN](../.scratch/phase5a-local-distribution/PLAN.md) · [STATUS](../.scratch/phase5a-local-distribution/STATUS.md) · [HANDOFF](../.scratch/phase5a-local-distribution/HANDOFF.md) · [WORKLOG](../.scratch/phase5a-local-distribution/WORKLOG.md) · [SUMMARY](../.scratch/phase5a-local-distribution/SUMMARY.md) · [项目入口](PROJECT-ENTRY.md)

## 一、范围与验收

### 1.1 已批准范围

Core + DLC + 配置采用项目内 portable 布局；Setup 每次显示路径页，已安装路径只作预填。新安装只接受非根、无重解析点的 NTFS 空目录，或符合产品 marker/文件身份边界的更新目录。旧未发布 APPDATA 组合布局不迁移。

```text
<项目目录>/
  dsh-pet-core-webm.exe
  _internal/
  portable.json
  data/
    .setup-project.json
    config.json
    plugins/official.ai-chat/
    plugins/official.screen-understanding/
```

卸载删除项目内程序、非 data 的其他内容和全部 `data/plugins`。个人 data 默认保留，用户选“是”并通过不可恢复确认才删除。项目外原始 ZIP/源目录不触碰。按用户追加要求，**关联／桥接／自启动清理是 best-effort，失败或异常不能阻止卸载**；因此不承诺每个旧集成引用都已移除。仍保留运行占用、路径边界和实际删除失败提示。

### 1.2 不在本轮承诺内

不迁移旧布局，不自动读取真实 Key，不调用收费 Provider、不抓取真实用户屏幕，不启用自动识屏、不改代理/VPN。受信任 Python DLC 不是沙箱；不新增官方 owner/factory 特判。手工验收 Core 禁用 OS 自启动注册，沿用既有候选策略，不当作正式发布的自启动/签名证明。

## 二、修改文件说明

基准分支 `codex/phase3-worker`，HEAD `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`。以下为本 U 范围涉及文件的 **工作树对 HEAD 累计** `git diff --numstat`；包含先前 T/R 和已有用户修改，不能全部归属本轮。未跟踪新增文件按总行数记 +N/0。无删除文件；无暂存、提交、推送。忽略目录只维护指定 Markdown，不广泛 add 原始日志/构建产物。

| 文件 | 状态及行数 | 改了什么、为什么 |
|---|---|---|
| `packaging/core_webm.iss` | 修改 +276 / −27 | 路径页总显示；Setup-owned marker/收据，程序与 data 分离、官方选装保留；卸载确认/两类数据选择/删除预检与失败中止；关联清理不再被描述成必须成功。 |
| `packaging/core_code_gate.iss.inc` | 修改 +218 / −23 | 原生 NTFS/根/重解析/空目录/已识别产品/保留 data 收据门；code 树跳过个人 data 扫描，允许无害锁文件重试。 |
| `packaging/core_removal_gate.iss.inc` | 修改 +2 / −2 | 数据 removal gate 对齐 portable 项目边界，不要求旧 APPDATA 地址。 |
| `packaging/phase4b_manual_entry.py` | 修改 +12 / −7 | 设置和普通 GUI 统一经过生产 _main 的代码租约与 portable 初始化；Worker/maintenance 仍走关闭入口。修复实机设置绕过占用锁。 |
| `pet/__main__.py` | 修改 +15 / −7 | 关闭的安装/卸载维护分派保持独立；_main 可显式传 enable_chat，兼容现有默认开启行为。 |
| `pet/runtime_layout.py` | 修改 +38 / −24 | 合法 portable removal 使用项目 data，根 marker 仍为唯一 portable 身份；避免无条件旧 APPDATA 判定。 |
| `pet/core_maintenance.py` | 修改 +244 / −50 | Core 卸载不执行 DLC 事务；验证 permit/当前身份后分别尽力清理 bridge 与自启动，false/exception 不阻塞；记录真实完成状态。 |
| `pet/agent_link.py` | 修改 +40 / −0 | 仅处理属于当前 Core 的 bridge 引用，已知无关 profile 不获取其锁，避免不必要的关联冲突。 |
| `scripts/build_core_webm_setup.py` | 新增 +93 / −0 | 构建 wrapper（工作树新增）：校验内嵌包输入、生成临时 canonical portable marker，传给 ISCC；不修改已审计 Core 目录。 |
| `tests/test_setup_project_layout_contract.py` | 新增 +249 / −0 | 新增公开合同回归：原生路径门/保留数据重装、portable runtime、marker、警告高度、取消时机和卸载前删除树预检。 |
| `tests/test_core_setup_gate.py` | 修改 +41 / −2 | 增加实际 reparse 目标门，权限不足时用本机真实 junction 兜底而不跳过产品验证。 |
| `tests/test_core_setup_template.py` | 修改 +52 / −13 | Setup 总显示路径、可选清理文案、真实 Inno 编译、构建 marker 与 data 分离合同。 |
| `tests/test_runtime_layout.py` | 修改 +18 / −10 | 替换拒绝一切 portable removal 的旧预期，验证项目 data/关闭维护入口。 |
| `tests/test_core_maintenance_entry.py` | 修改 +16 / −0 | 覆盖关闭的 maintenance 分派和有效 portable 入口；防止进入常规 GUI。 |
| `tests/test_core_registration_cleanup.py` | 修改 +66 / −9 | 验证可选关联 false、拒绝与异常不阻塞、清理互不遮蔽；保留身份/路径门。 |
| `tests/test_agent_registration_scope.py` | 修改 +53 / −7 | 覆盖无关或属于当前安装的 bridge/profile 清理范围，防止删到其他安装引用。 |
| `tests/test_feature_manual_acceptance.py` | 修改 +60 / −1 | 公开 seam 校验冻结设置/普通桌宠先持 code lease、发现项目 data 后才开 UI；Worker 与 maintenance 不中途走 GUI。 |
| `docs/PROJECT-ENTRY.md` | 修改 +7 / −3 | 入口切换为 U 当前产品合同，旧 R/T 状态归历史；链接当前产物/报告/人工边界。 |
| `docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md` | 修改 +168 / −15 | 追加 U 设计/验收、保留 data 收据、best-effort 关联与统一启动边界；不改写 R/T 历史数字。 |
| `docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md` | 新增 +304 / −0 | 补 U 修订优先级、交付和实际验收状态，保留原 T/R 收尾计划。 |
| `docs/PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md` | 新增 +162 / −0 | 补本次实际产物、旧布局不迁移、真实安装/卸载与人工剩余门。 |
| `docs/INDEX.md` | 修改 +11 / −5 | 登记本报告，确保新文档有入口。 |
| `.scratch/phase5a-local-distribution/PLAN.md` | 修改 +461 / −0 | 逐项维护 U01–U06 完成条件、状态和停止线。 |
| `.scratch/phase5a-local-distribution/STATUS.md` | 修改 +512 / −0 | 区分实现/自动化/实机/用户确认/Git 状态。 |
| `.scratch/phase5a-local-distribution/HANDOFF.md` | 修改 +554 / −0 | 准确停点、失败分类、新 Setup 地址、下一步和禁止重开边界。 |
| `.scratch/phase5a-local-distribution/WORKLOG.md` | 修改 +603 / −0 | 留存 red→green、实机发现与修复、构建/性能/最终验证过程证据。 |
| `.scratch/phase5a-local-distribution/SUMMARY.md` | 修改 +432 / −0 | 跨对话压缩当前用户决策、产物与人工门，避免恢复失效的 Core-only 保留 DLC 合同。 |
| `docs/PR-REPORT-SETUP-PROJECT-DIRECTORY-2026-10-09.md` | 新增 +224 / −0 | 新增本报告，包含逐文件、数字性能、真实安装/卸载、失败分类及人工步骤。 |

## 三、实现要点与风险处理

1. **Setup 拥有 portable 身份**：构建 wrapper 在输出目录旁生成临时 canonical marker并嵌入，不修改已审计 Core 输入。data 独立于程序复制；只安装勾选的内嵌 ZIP，不把未勾选解释为删除。
2. **目录校验**：在 native 门拒绝相对路径、盘符根、非 NTFS、junction/symlink/reparse 及陌生非空目录。程序 code barrier 不遍历整个个人 data；data 自身边界单独确认。
3. **保留数据重装**：卸载保留的 data 含 Setup-owned `.setup-project.json` 收据。仅根目录只剩已识别 data（和允许的锁记录）时可复用；不是接纳任意非空目录，也不是把收据当运行时 portable 身份。
4. **统一运行引导**：实机发现 manual entry 的设置路径绕过生产 bootstrap，导致 Config 去旧数据根、Core 占用锁缺失。修为经 `_main(enable_chat=...)` 先持代码租约/初始化项目 data，再建 Config/UI；Worker 与关闭的维护仍走各自早期分派。
5. **最小卸载**：确认完成后才获取两个 barrier、调用 closed maintenance。可选 bridge/autostart 清理独立 try/catch；仍验证 permit、当前路径/EXE。无 DLC 工厂/ledger/事务/恢复。先预检选中删除树，删除失败 Abort，不显示虚假成功；不能保证整个删除事务原子回滚。
6. **界面**：路径风险警告 `WordWrap` + 控件自身 `AdjustHeight()`，后续编辑框/浏览按钮随高度下移；最终窗口警告41px、两行完整不重叠。卸载以明确是/否对话框选择个人数据，默认“否”，并有不可恢复确认。
7. **边界说明**：删除 data 不额外枚举/删除系统安全存储中的外部凭据；本地 marker/常规文件校验不是数字签名认证。保留 data 时项目目录不会完全消失。

## 四、性能分析

### 4.1 环境、命令与样本

Windows `10.0.26100`、Python3.11.1 x64、20 logical CPUs；PySide6 6.11.1、PyInstaller6.20.0；本机 `E:/tools/InnoSetup6/ISCC.exe`。下列为真实本机执行，同时有 pytest/构建/桌面活动，不是隔离前后版本 benchmark。

| 路径 | 样本与实测 | 命令／证据 |
|---|---|---|
| native Setup code gate，data无文件 | 7批×100次，中位1.72ms/次，批均值1.41–2.82ms | `python -X utf8 .scratch/phase5a-local-distribution/probe_u06_performance.py`；真实 Inno gate fixture，不执行安装 |
| 同门，data含5000文件 | 7批×100次，中位1.72ms/次，批均值1.41–2.19ms | 同上，证明该路径不随个人文件扫描线性增加；不是所有目录规模上界 |
| Python Core code lease | n100；median2.3284ms /p95 4.6821ms /max19.5196ms | 同上；真实 filesystem / native lock |
| RuntimeLayout portable discover | n30；median7.52725ms /p95 14.3509ms /max16.4318ms | 同上；真实临时 portable 目录 |
| data lease | n100；median1.98015ms /p95 6.2459ms /max9.04ms | 同上 |
| 冻结设置空闲 | 40×0.5s；CPU进程中位1.5%，线程16→16；RSS156426240→156557312B（+128KiB）；读取+453552B、写入+0B | `probe_u06_delivery_idle.py`，`U06-delivery-idle.json`，自然退出0 |
| 生产 Worker 启动 | n1；HELLO1284.255ms、READY1293.574ms、总1337.895ms | `U06-production-worker-startup.json/startup.json`；无截图/网络请求，租约交接后自然退出0 |

### 4.2 最终 Setup 的真实成本

安装采样每约0.25s记录自有进程树 RSS（采样总耗时受负载影响）；卸载计时包括脚本点击对话框。均为每路径n1，不能据此推导机器间性能保证。

| 路径 | wall time | 进程树峰值 RSS | 样本／结果 |
|---|---|---|---|
| delivery-fresh-install | 54.922 s | 134.25 MiB | 163 RSS 样本，exit0 |
| delivery-update-no-dlc-selected | 37.234 s | 47.71 MiB | 104 RSS 样本，exit0 |
| delivery-reinstall-retained-data | 55.031 s | 131.98 MiB | 177 RSS 样本，exit0 |
| uninstall-keep | 4.953 s | 未单独采集 | 包含自动操作确认与成功对话框，exit0 |
| uninstall-delete | 5.984 s | 未单独采集 | 包含自动操作确认与成功对话框，exit0 |

Core 构建149.844s，bundle 468455087B（2137文件）；最终 Setup251766161B。包/构建哈希见第七节。

### 4.3 五项成本判断

- **稳态开销**：新引导的路径发现/租约发生在启动，不是逐帧、逐消息或网络请求前轮询；两类租约持到退出。上述20s真实设置样本无线程增长、无写入，不能称长期零增长或拿128KiB变化证明泄漏。
- **新增路径成本及频率**：Setup目录检查发生于选路径/准备安装；卸载树预检与删除只在用户确认卸载时发生。bootstrap每进程一次（样本给出单次成本）；不是后台周期任务。
- **系统调用/磁盘**：增加必要 marker读取、文件属性/NTFS查询与锁句柄；卸载选择删除的树要枚举并删除。个人 data 不因安装 code-tree检查而全部遍历。
- **网络/线程**：U变更不新增网络请求或持续后台线程；bridge清理可在现有实现内启动清理子进程，不是新增轮询。失败重试最多3次、两段等待0.05s/0.10s后尽力继续，不保证外部系统调用永不延迟。
- **内存**：目录扫描有界/同步；本报告只证明上述窗口的测量结果，没有旧版同条件RSS基线，不能声称净减少。大目录、慢盘、杀毒软件影响仍需用户环境观察。

### 4.4 构建空间核查

对本轮 U06 命名的构建/验收目录只读枚举34,900个文件，逻辑大小9,126,602,102B（约8.50GiB）；按 Windows 文件标识去重后相同，无硬链接复用。测量时 E 盘剩余约177.80GiB。此数包含多轮中间候选，不含全量pytest临时目录或更早R/T产物；没有开始时空间基线，**不能宣称本轮实际增量被控制在用户提到的约5GB之内**。证据 `U06-storage-footprint.json` / `U06-storage-unique.json`，另在项目scratch安装5,040,249B临时诊断工具；未再重建或擅自清理历史产物。旧中间候选可另行列明并在用户确认后回收，不混入本次交付。

## 五、实机运行记录

### 5.1 最终构建及安装命令

```powershell
python -m scripts.build_core_webm_setup `
  --core-dir _u06d/dist/dsh-pet-core-webm `
  --package-dir .scratch/phase5a-local-distribution/u06-final-20261009/packages `
  --core-output-dir .scratch/phase5a-local-distribution/u06-delivery-20261009/setup-verified `
  --core-version 4.2.2
```

ISCC exit0；真实最终安装目标为工作区自有 `_u06-project-delivery`（非用户安装目录）。由 `run_u06_delivery_acceptance.py install/rest` 启动真实 Setup `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=<目标> /TASKS=ai,screen /LOG=<自有日志>`；不执行结束页“运行桌宠”。卸载使用真实 `unins000.exe`，按自身PID枚举窗口、异步点击确认，等待自然退出，不 kill 用户进程。

### 5.2 结果矩阵（同一个最终 Setup）

| 顺序／功能 | 真实操作与实际结果 |
|---|---|
| 1 安装 Core+两DLC | exit0；canonical root marker和data收据逐字相同，插件在项目 `data/plugins/official.*` |
| 2 路径页重开 | 不传 `/DIR` 的可见向导仍出现，预填 `_u06-project-delivery`；41px完整风险文案，无控件覆盖；取消exit2且未开始安装 |
| 3 非法/合法目录 | `E:\`由Inno原生路径校验拒绝；陌生非空目录返回 `install_target_not_empty`，生成空目录可前进到选装页，再取消且仍空 |
| 4 冻结设置与占用 | 启动已装EXE `--settings`，项目data生成config；可见窗口5.578s。未关闭时卸载返回 `core_in_use`，Core哈希和DLC保留，设置自然退出0 |
| 5 junction边界 | 在自有plugins放指向自有外部哨兵目录的junction；卸载返回 `uninstall_reparse_or_hardlink`，Core与外部文件字节未改；只删除该测试link后继续 |
| 6 三种取消 | 第一风险确认、不可恢复确认、Inno最终确认分别取消；逐文件SHA集合与操作前一致；uninstaller取消exit1，helper正确判定已取消 |
| 7 原地更新 | `/TASKS=`，Core更新exit0；原config字节、已有DLC manifest哈希、个人哨兵保留 |
| 8 默认保留卸载 | 注入生成的无效staging/ledger哨兵，不执行它们；卸载exit0，只剩data，程序/根测试重要文件/plugins已删，个人哨兵及收据保留 |
| 9 保留数据重装 | 同目录再安装两DLC exit0，配置哈希及个人哨兵复用，无旧APPDATA迁移 |
| 10 删除data卸载 | 选择删除并二次确认，exit0；项目目录不存在或已空，外部ZIP/源目录哨兵保持原字节；输出 `REAL_SETUP_MATRIX_PASSED` |

外部源ZIP/源目录是**生成的保留边界哨兵**，没有实际导入/执行第三方包；通用包安装行为另由 transaction 测试覆盖。本矩阵不虚称完成了真实第三方业务执行。UI图像仅用自有 HWND 的 `PrintWindow` 渲染；最初被其他窗口遮挡的桌面裁图不作证据，已替换。

原始结果：`U06-delivery-machine-acceptance.json`、`U06-delivery-wizard-paths.json`、`U06-delivery-real-occupancy.json`、`U06-delivery-junction-preservation.json`；截图 `U06-delivery-wizard-directory.png`、`U06-delivery-real-settings.png`，均在阶段生成目录。可复现验收步骤见第八节，不要求把原始日志纳入Git。

### 5.3 明确没有自动通过的门

真实模型/余额响应取决于用户的 Provider、Key、模型、账户状态，执行会发送用户数据且可能收费；本轮没有读取这些凭据或发请求。正常 Worker探针记录 `screen_requests=0`、`network_requests=0`，只能证明产物启动/握手/自然退出，不证明具体屏幕内容识别。用户安装目录/个人重要文件未由脚本操作。因此保留用户确认，不以沉默代替未测结论。

## 六、测试与验证

### 6.1 red→green 与历史失败分类

| 证据 | 结果 | 分类／当前意义 |
|---|---|---|
| U01 初始回归 | 4 failed /5 passed /8 errors | 修改产品前的路径/portable/卸载合同红灯，不是最终门 |
| U06 followup red → green | 4 failed →22 passed /1 skipped（91.79s） | 补齐锁文件重试、相对路径、保留data重装与文案；后以junction实测消除权限skip |
| bootstrap/UI red → green | 4 failed →62 passed（33.94s） | 实机发现验收入口绕过生产bootstrap的既有缺陷；回归先红后修 |
| 删除预检 red → green | 1 failed →17 passed（33.23s） | 补齐删除前data边界检查和后置删除失败Abort |
| 可选集成清理专项 | 21 passed（30.80s） | false/拒绝/异常不阻塞，同时保留身份边界 |
| junction专项 | 1 passed（10.06s） | 实际Windows junction，不以mock/权限skip替代 |
| 中间全量#1 | 4437 passed /16 skipped /14 warnings，770.30s | 旧bootstrap中间快照，不能作为最终结果 |
| 中间全量#2 | 2 failed /4439 passed /15 skipped /15 warnings，939.11s | 期间源码变化（source_unchanged=false），包含错误Inno全局API与旧静态断言；不遮蔽失败、不计最终通过 |
| 最终全量 | 4442 passed, 15 skipped, 14 warnings in 1594.26s (0:26:34) | 最终输入快照与source_unchanged校验，不用中间日志替代 |
| 3轮满CPU相关族 | 第1轮 242 passed, 1 skipped, 1 warning in 156.61s (0:02:36)，CPU中位99.9%；第2轮 242 passed, 1 skipped, 1 warning in 157.20s (0:02:37)，CPU中位99.95%；第3轮 242 passed, 1 skipped, 1 warning in 155.64s (0:02:35)，CPU中位98.9% | 13族、20个自有逐核worker，Event停止/自然join，无强杀 |
| Ruff／format／diff | Ruff exit0；122个Python format exit0；diff exit0 | 当前源只读检查；CRLF提示不等于检查失败 |

报告纪律专项61 passed；11份相关Markdown、316个相对链接无断链/尾随空白。最终收据 `U06-final-closeout.json` 将最终634个输入、三轮负载、实机结果、全部交付哈希、文档与空暂存区再次交叉核对；不是把中间结果合并成最终绿灯。

最终15个跳过已按原收集顺序定位节点并单独 `-rs` 复核（`U06-final-skip-reasons.log`，15 skipped /1.89s）：4项Windows符号链接权限、5项POSIX/macOS/Cocoa/tzset平台限定、2项真实前台窗口、1项真实截图、1项可选GIF派生素材、1项需显式native helper bundle、1项真实声卡发声。未删测试/降低断言。已另做真实junction与Worker/helper审计，但**不把junction说成文件symlink用例通过，也不把bundle审计冒充被跳过的原生隔离用例**。14条pytest warning为既有Qt弃用与构造重复ZIP测试提示，不是本轮卸载关联报错。

首次 label 修复用了不存在的全局 `AdjustLabelHeight`，属于**本轮新引入且已修**的编译失败；正确控件方法已真实编译/实机确认。旧r3 helper漏识别“已顺利”成功文案而超时属于测试工具问题，不能把那次helper标为通过；最终helper已修并重跑完整矩阵。本轮不删除或改低断言来掩盖这些失败。

### 6.2 最终验证命令

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m ruff check pet packaging scripts tests
# format --check 对 git diff 与指定源目录中未跟踪的改动 .py，共122文件
python -X utf8 -m pytest -q --basetemp=.scratch/u6vfull
# 用独立 APPDATA/LOCALAPPDATA，完整记录634个源码/ISS输入前后SHA
python -X utf8 .scratch/phase5a-local-distribution/run_u06_final_highload.py
git diff --check
python -m pytest -q tests/test_pr_report_discipline.py
```

高负载族：core_setup_gate、setup_project_layout_contract、core_setup_template、core_code_gate、runtime_layout、agent_registration_scope、core_maintenance_entry、core_registration_cleanup、feature_package_transactions、feature_version_lease、feature_manual_acceptance、local_package_entry、runtime_data_import_entry。每轮先等待worker Event就绪，再运行真实pytest，结束Event停止全部worker并确认无残留。完整输入/样本/退出码保留于最终 receipt/highload JSON。

## 七、交付清单与校验

| 产物 | 仓库内路径 | 字节 | SHA-256 |
|---|---|---:|---|
| Core 4.2.2 | `_u06d/dist/dsh-pet-core-webm/dsh-pet-core-webm.exe` | 17,299,681 | `03d717f97e5ff2030131ff52b5fcf3d59c891a18bd3818d9e5698160ee56c86d` |
| Setup 4.2.2 | `.scratch/phase5a-local-distribution/u06-delivery-20261009/setup-verified/dsh-pet-core-webm-setup.exe` | 251,766,161 | `7cecfde7854fa70cbfa98641457b2ade8c8a547887254ff609204ad1815f6e22` |
| 生产 Worker | `.scratch/phase5a-local-distribution/u06-worker-20261009/dist/proactive-screen-worker/proactive-screen-worker.exe` | 4,863,029 | `e9881d341dcc25128a62ac667b431ba6c4120ce636f4210e455c3a79dcf3416e` |
| AI DLC 1.0.2 | `.scratch/phase5a-local-distribution/u06-final-20261009/packages/official.ai-chat.zip` | 121,663 | `e610ceeb2b531ca6c8eec59348dfbfaf88227f2f4850cf79c0ad10558841cf86` |
| Screen DLC 1.0.1 | `.scratch/phase5a-local-distribution/u06-final-20261009/packages/official.screen-understanding.zip` | 24,458,727 | `a351d479eeb5332eb822207a48ef17a54804dcecf70f8e33fa7837ecf2f9c2ca` |
| helper bundle manifest（复用） | `.scratch/phase5a-local-distribution/acceptance-night-20261006/builds/probe-10-runtime-fix-20261006/dist/dsh-feature-probe/bundle.json` | 9,552 | `915f87620c3a84219bf7310e0ff805ff3c25d0b05e2f0ba2d4b95c5eba9a3232` |

只使用 `setup-verified` 这份Setup；不要用旧r422、r3、u06-final Setup或无verified的中间setup验收。Core输入核对223根源码、228stage源码（5个生成策略文件）、1032资源、2137 bundle文件，PE GUI subsystem=2。AI ZIP30项、Screen ZIP119项，均要求Core `>=4.2.2,<6.0.0`；Screen ZIP中的Worker字节与本次生产构建相等。内嵌ZIP输入由builder验证，最终安装实际执行两包导入。

生产 Worker当前闭合源码/必备PYZ和native模块审计通过；正常启动 HELLO/READY/租约/自然退出通过，无Core内回退。helper固定manifest哈希与bundle完整性复核，**本U轮复用既有helper，不声称重新编译**。`audit_u06_delivery.py`核对当前输入与产物，不仅比文件名或版本号。

构建和测试生成物不提交，不覆盖稳定产物；未清理未知staging/用户源包。当前未签名验收候选不能据此宣称SmartScreen信誉或正式发布完成。

## 八、用户人工验收（按顺序合并操作）

1. **准备与安装一次**：自然退出旧桌宠/设置，备份需要的数据。旧未发布布局不迁移；用第七节新Setup选择一个新的空NTFS项目目录（非盘符根），确认完整风险提示并勾AI/识屏。检查项目内有portable.json和data/plugins的两个官方目录。
2. **一次确认API相关功能**：打开聊天窗口，再到Core“AI与对话→API服务”配置/保存真实服务和Key，按文字、视觉、余额用途明确授权。回到原聊天窗口发送；无需重启。测试余额支持的协议与明确不支持提示，错误Key修正后可重试。连接测试不等于保存，可能产生少量费用。
3. **同次确认手动识屏**：自动识屏保持关闭、白名单留空，在用户同意发送的测试屏幕上点“看看屏幕”；应返回识别结果，不再出现缺依赖Worker停止。普通设置刷新不误取消它。真实截图/API由用户决定，本报告未自动代发。
4. **更新一次**：自然退出程序，重开同Setup；路径页仍显示，预填已有项目位置。此次不勾DLC进行更新，之前配置及两个DLC应仍在。
5. **默认保留卸载→同目录重装**：先在项目根放一个无价值的测试文件，data放另一个哨兵，项目外保留源ZIP/目录。卸载警告应明确提示检查重要文件；个人数据选“否”。程序、根测试文件与data/plugins应删除，个人哨兵/配置/收据及外部源包保留。用同Setup装回原路径，保留数据应被识别。
6. **删除个人数据卸载**：确认只有测试数据后再卸载，选“是”并通过不可恢复确认；项目目录应清空/删除，项目外源ZIP/目录不变。目录里若有重要文件必须先移出，不用于本步骤。

工程已自动覆盖安装/卸载与UI门，用户可重点确认自己桌面下文案/交互和真实API/余额/识屏；反馈时提供版本、步骤和错误文本，不提供Key。用户确认后才能更新Phase5A关闭状态。

## 九、实际使用效果与限制

以后安装时始终能确认目录，官方/外部已安装DLC都随项目目录管理；更新不会因未勾DLC删功能。卸载不再纠缠DLC ledger/staging，也不再因关联清理失败拒绝删Core；个人数据是否保留由用户明确选择。运行中的桌宠仍必须自然退出，重解析/占用/真实文件删除失败仍可能中止；默认保留data时不会声称目录完全消失。旧未发布布局不迁移，系统安全存储不随data删除自动清空。本轮无提交推送，Phase5A尚待用户真实功能确认。
