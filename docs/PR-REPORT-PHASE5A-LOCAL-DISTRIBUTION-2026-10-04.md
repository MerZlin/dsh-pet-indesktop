# Phase 5A / 5B-1 本地分发实施证据（尚未完成正式交付）

> 报告起始日期：2026-10-04 UTC；最新更新：2026-10-06（本机Asia/Shanghai）。历史观测保留，不把旧结果写成最新。基线：`e2687be0866db2915af13447fb49bb278ffdf202`；分支：`codex/phase3-worker`。
> 全部仍为 WIP，未提交、推送或发布，没有使用子智能体。此报告是当前实施证据，不是最终交付/正式发布报告。
> 关联：[批准设计](plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md)、[任务](../.scratch/phase5a-local-distribution/PLAN.md)、[准确停点](../.scratch/phase5a-local-distribution/HANDOFF.md)、[状态](../.scratch/phase5a-local-distribution/STATUS.md)。

## 一、核心特性与完成口径

统一小 Core 产品 `dsh-pet-core-webm`，固定两个独立官方 owner `official.ai-chat`/`official.screen-understanding`。当前已具备双包事务/manifest v2/host-only 合同、AI 实现物理迁移及真实 Qt 生命周期、RuntimeLayout/稳定凭据身份、显式导入及确认工具、离线签名/生产材料和原生 Setup/卸载前置门源码。2026-10-05已取得正式双包签名/原生LPAC、新Core02四组合实际启动确认及普通/便携/两DLC ZIP与Setup编译候选证据。全局注册拥有边界已有源码回归，当前Core03已重建并审计通过，最新稳定累计全量273通过；AI1.0.1正式签名与五产物总分发清单签名294及独立核验296已通过；仍需完整导入与请求、真实安装/更新/卸载/便携/人工/干净环境门及剩余性能验收。

- state.json、revision、journal、pending 执行禁令、实际 receipt 和内核租约仍是权威；UI不宣告加载成功。
- 不热替换已导入 Python host，不强退 Core/设置/外部进程，不删除个人数据，不降级为普通 subprocess 自检。
- 源码 green、生成夹具的 Windows 原生测试、用户人工确认和正式生产分别记录；旧 Phase4B 用户回执不当成新 Phase5 验收。

### 2026-10-05 当前累计证据

#### 当前停点：8GiB空间门、小Core与五产物签名通过，完整验收未完成（UTC 2026-10-05T14:39:49.608Z）

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

##### 当前实际效果与限制
空间阻挡已经解除，最新小Core、两种ZIP、正式双包及总分发签名均已产生并核验；这不是Phase5A交付完成。已验收旧桌宠和个人数据保持不动；自动化测试进程只允许实际菜单自然退出。尚无真实Setup安装、真实模型体验或干净环境通过记录，不需要用户提供聊天密码。

---

#### 历史停点：8GiB授权后Core03/ZIP/Setup生成，全量与满CPU三轮通过（UTC 2026-10-05T13:16:35.263Z）

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

##### 当前实际效果与限制
空间上调已让最新小Core及ZIP/Setup产出，而不是跳过安全门。全量、滿CPU三轮和空包/仅识屏真实启动通过；本地密码窗口只用于新AI签名，不影响已验收旧桌宠。最终交付及人工/新用户环境验收仍未完成。

---

#### 历史停点：最新全量与满 CPU 三遍通过，最终冻结重建仍受空间门阻塞（UTC 2026-10-05T04:49:44.602394+00:00）

- `codex/phase3-worker` / `e2687be`；原 WIP 全部保留，无提交、推送、发布或子智能体。
- 最新稳定累计 full234：4258 passed /14 skipped /14 warnings /861.35s（父端863.204s），退出0，pet/features/scripts/tests 的哈希复核无运行中源码修改。14 skip 未执行不计通过，warnings 为实际Qt弃用与重复ZIP负向夹具，不过滤。
- 最新真实高负载233：20个本轮自有 below-normal CPU进程，三遍分别227 passed /109.71、122.16、128.58s；每遍 CPU median/p95 100.0%/100.0%；父端114.500、127.578、133.625s。退出0且负载进程已回收。不是模拟满载，不修改系统设置。
- 229→230卸载窗公开Qt回归先红后绿：不再用scroll控件属性遮蔽QWidget.scroll；回执使用CoreRemovalEvidence可空类型，186 passed /53.93s。56受影响pet/scripts源以及231 AI host26源mypy通过；此前214/223是修复前历史，不代替最新门。
- 正式加密PKCS#8及公钥策略已成功创建，双包签名178完成，不再等待密码、不得重建密钥。可信新helper05为17,267,820B/63文件/176PYZ，摘要3087700e533095c4a7fc8cd1923baf463f9414108c134cace1426ab5a3ff5e6a；原生权限矩阵224与正式双包LPAC225通过。AI host-only不伪造Worker；screen完成HELLO→SHUTDOWN→优雅退出。沙箱网络负对照为真实WSAStartup10107且未connect，不写成已测防火墙connect拒绝。
- 217正式AI/screen目录/ZIP合计80预检+80未接受取消，无factory/Worker执行；218真实NTFS生成夹具同卷移动与同用户OS凭据恢复通过。最终冻结便携/跨卷/干净用户仍未验收；probe目前正式端到端各n=1，不满足n>=10。
- Core02已有真实四组合普通启动确认195/201，未借设置提交成功。但Core02落后于pet/agent_link.py、pet/core_maintenance.py、pet/model_access_tracker.py、pet/core_uninstall_ui.py四源，并携带旧helper04；必须重建Core03和后续ZIP/Setup，不将旧候选冒充最终交付。Worker02/正式两包材料不因这四处Core改动变化。
- 空间237：本轮拥有根5,871,902,944B（113953文件，28 reparse剪枝且不跟随），6GiB上限6,442,450,944B，余570,548,000B。Core03的2,147,483,648B预留不满足，差1,576,935,648B。213清理被工具策略拒绝，未删除且不重试绕过；不降低预留、不超6GiB，不清理旧Phase4B或真实数据。
- 219启动基准首个样本及220有界退出诊断失败，0有效启动样本；仅本轮隔离Core02 PID36460仍驻留，APPDATA为bench-frozen-core-219/APPDATA。不强退、不注入退出、不冒充旧常用桌宠。停止第三次猜测，待实际输入路径诊断或用户使用这个测试Core自己的退出菜单自然结束。
- 准确下一步：等待新清理白名单目标/影响确认与安全可执行条件，重新核对拥有/占用/边界，操作后重测空间；不能自动重试213或绕过工具拒绝。空间满足2GiB后重建Core03并嵌入helper05，重新冻结业务/分发；旧Core02的4处源不一致不得忽略。正式总清单签名、完整请求/升级/卸载/最终便携/真实Setup/人工与干净环境仍未完成。真实安装卸载、数据导入、密钥备份新目标须另行确认；Authenticode/SmartScreen/Inno许可未验收。

- 最新综合质量239/240已收齐：Ruff pet/features/scripts/tests通过（0.249s），151文件format通过（0.079s）；配置mypy26（4.702s）、受影响56（2.649s）、AI host26（0.785s）通过；文档129（61.411s）、构建/信任/注册/报告165 passed（5.94s）通过；tracked diff-check退出0。239首次ruff/format因隔离USERPROFILE使Python user-site模块不可见而失败；240使用已安装绝对路径原生ruff0.16.6通过，不重装依赖、不注入PYTHONPATH、不把239退出1涂成0。
- 241只读新清理提案共8项存在的旧生成物/1,633,900,888B，无删除命令/实际删除。不是重试213被拒绝的core-01目标；保留此前拒绝及所有保护项。必须独立核对拥有证据/占用，明确目标和影响获确认且工具允许后才可处理。空间门和最终Core03仍未解除，不因提出清理方案而计通过。

#### 当前实际效果与限制
正式两包和可信自检已有证据，最新源码累计与满载回归通过；仍未完成最终冻结交付。不要安装旧候选冒充最终成果，不宣布Phase5A或5B-1完成。密码已输入且已成功用于签名，本轮无需再提供秘密。


### 前序工程与冻结历史证据

#### 历史停点：注册拥有边界回归已通过，准备累计回归与 Core03（UTC 2026-10-05T03:34:55.460339+00:00）

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

### 2026-10-05 冻结与交付候选历史证据

#### 实测进展：新 Core 四组合启动与分发候选已生成（UTC 2026-10-05T03:08:38.470993+00:00）

- codex/phase3-worker / e2687be，原 WIP 保留；无提交、推送、发布或子智能体。正式双包签名178与原生 LPAC185已通过；不再次创建密钥或读取私钥内容，备份恢复仍待确认。
- Core02真实生产构建：455,763,782B /1,424文件 /2,259PYZ模块 /199.044s；实际 PYZ、原生依赖与可信 helper 摘要审计通过，没有 AI/识屏业务实现或源码/测试信任回退。helper probe-04固定摘要66b94b18f3b971879bb73c4948d2e62c80048bba99659e49d0d55da2146c808f；最终源代码一致性需再审计。
- 真实 UI 安装191：两包分别完成预检/用户界面确认/正式 LPAC/切换，退出3且等待启动确认，未冒报已可用。正常 Core195自行清除两包 pending（revision4），未打开设置；菜单有AI对话/看看屏幕/主动识屏，实际“退出”菜单自然退出0。192～194只是探针窗口过早查询/短暂UIA句柄失效，修正事件与句柄重取后195成功；不强杀。
- 冻结四组合：none/AI/screen（201）及both（195）均普通Core实际启动、对应贡献准确、自然退出0。none账本为空，不存在加载receipt，201日志的固定“BOTH/TWO_PACKAGE”标签不作为事实证据；以receipt states与菜单树为准。没有真实模型/截图/用户数据导入验收。
- 新档案构建公开seam：red196（18failed，接口缺失）→197/198（实际descriptor/stage API纠正）→green199（67passed/7.88s）；新增scripts/build_local_archives.py与测试，Core审计复用只读inspect_core_bundle，不覆盖既有artifact。流式写入、源码哈希复核、独占输出、体积预算；未知用户数据与非空代码锁不入ZIP。2脚本mypy/3format通过，最终全量/Ruff仍需累计重跑。
- archive200退出0：AI ZIP2,334,659B/0.332s；screen ZIP24,455,042B/4.049s；普通ZIP259,350,992B/27.117s；便携ZIP259,351,159B/27.685s。便携标记显式固定data，无用户数据。实际Setup202编译成功95.000s、222,165,149B；仅编译，未安装，Windows发布者认证/编译工具许可未验收。
- 空间203：5,169,418,473B，余1,273,032,471B，上限6,442,450,944B；12测试reparse剪枝且未跟随。Core186预算初次被pytest测试链接拒绝，改为生产拥有根的独立预算并扣除其他占用，未削弱路径安全。下一次复制/构建前估算；未清理任何旧验收目录。
- 准确下一步：补Agent全局hook/bridge新产品拥有边界与Core删除前收尾，后续累计回归；分发总清单签名、Setup更新/卸载实测、便携移动/导入、请求体验/人工/干净环境与性能门仍待执行。新真实安装/卸载、真实导入、密钥备份目标须另行展示确认。既有APPDATA事故及其保护根保留，不抹去历史。

#### 当前实际效果与限制
正式签名双包和五项交付候选已生成，真实冻结Core可自行确认两个功能包加载、四组合入口正确。但尚不是完整分发验收，不自动操作真实用户安装，也没有发布。

---

#### 历史停点：累计回归与新 helper 正式双包 LPAC 通过，最终 Core 重建受空间门阻塞（UTC 2026-10-05T04:18:01.321967+00:00）

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

## 二、修改文件说明

2026-10-06授权检查点，以本轮基线 `e2687be` 对比的最终 `git diff --cached --numstat` / `--name-status` 为准。明确白名单183文件（178产品/测试/文档+5任务Markdown），0二进制，无生成物、日志、密钥和个人数据。下文带日期的旧增量表保留为历史；此表为当前累计统计。

| 文件 | 状态 | 行数 | 改了什么 / 为什么 |
|---|---|---|---|
| `.scratch/phase5a-local-distribution/HANDOFF.md` | 新增 | +403 / −0 | 精确停点、当前验证、需确认密钥目标及事故/保护边界，供下一轮继续。 |
| `.scratch/phase5a-local-distribution/PLAN.md` | 新增 | +451 / −0 | 稳定任务编号、完成证据与仍未通过门，避免聊天里虚报阶段完成。 |
| `.scratch/phase5a-local-distribution/STATUS.md` | 新增 | +400 / −0 | 分别记录实现、自动化、实机、人工、提交/发布状态，不以历史通过替代当前门。 |
| `.scratch/phase5a-local-distribution/SUMMARY.md` | 新增 | +399 / −0 | 跨对话压缩关键路径、版本、限制与下一步，不新建第二套权威记录。 |
| `.scratch/phase5a-local-distribution/WORKLOG.md` | 新增 | +494 / −0 | 保留每次 red/green、失败根因和原生命令证据，含 red91 事故与更正。 |
| `LOG-INDEX.md` | 修改 | +8 / −0 | 索引本轮累计实施日志与事故更正，方便新对话追溯事实。 |
| `LOG.md` | 修改 | +108 / −0 | 追加本轮源码门与未完成正式分发边界，保留 Phase4B 历史和测试隔离事故。 |
| `README.md` | 修改 | +2 / −0 | 入口追加统一小 Core/双选装路线的实施状态、稳定全量证据及正式产物未交付提醒，保留 Phase4历史。 |
| `SPEC.md` | 修改 | +3 / −1 | 补已批准 Phase5A/5B-1 当前状态与尚未验收门，既有约束不被新状态覆盖。 |
| `docs/INDEX.md` | 修改 | +4 / −1 | 登记同组 Phase5 设计/任务与实施报告，阶段导航改为实施中而非已交付。 |
| `docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-04.md` | 新增 | +718 / −0 | 当前实施证据而非最终交付：逐文件行数、源码实测、原生权限阶段、事故、验证与未完成门，防正式锚/冻结产物被虚报。 |
| `docs/PROJECT-ENTRY.md` | 修改 | +5 / −1 | 入口追加统一小 Core/双选装路线的实施状态、稳定全量证据及正式产物未交付提醒，保留 Phase4历史。 |
| `docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md` | 新增 | +214 / −0 | 保存批准设计、执行/信任/便携/独立事务/导入/Setup合同与验收门；没有新正式产物不把合同当通过证明。 |
| `docs/plugin-phase-05-distribution/README.md` | 修改 | +4 / −0 | 路线图前置当前 Phase5A/5B-1 实施摘要与正式设计链接；其余5B仍独立，历史决策有日期不抹去。 |
| `docs/plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md` | 修改 | +2 / −0 | 路线图前置当前 Phase5A/5B-1 实施摘要与正式设计链接；其余5B仍独立，历史决策有日期不抹去。 |
| `features/ai_chat/__init__.py` | 新增 | +1 / −0 | 建立固定 AI host/业务命名空间并保持惰性导入，避免未安装/自检时启动 UI 或请求线程。 |
| `features/ai_chat/host/__init__.py` | 新增 | +1 / −0 | 建立固定 AI host/业务命名空间并保持惰性导入，避免未安装/自检时启动 UI 或请求线程。 |
| `features/ai_chat/host/chat/__init__.py` | 新增 | +13 / −0 | 建立固定 AI host/业务命名空间并保持惰性导入，避免未安装/自检时启动 UI 或请求线程。 |
| `features/ai_chat/host/chat/ai_settings_page.py` | 新增 | +734 / −0 | 把聊天配置页物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/crop_dialog.py` | 新增 | +181 / −0 | 把附件裁剪界面物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/external_turns.py` | 新增 | +82 / −0 | 把外部文字结果同步物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/geometry.py` | 新增 | +59 / −0 | 把聊天窗口几何物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/legacy_styles.qss` | 新增 | +297 / −0 | 把经典聊天样式物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。；245仅去掉空EOF，已签材料不变，最终需重新签包。 |
| `features/ai_chat/host/chat/legacy_widgets.py` | 新增 | +1108 / −0 | 把既有经典聊天窗口物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/models.py` | 新增 | +231 / −0 | 把聊天/Provider/会话数据模型物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/modern_styles.qss` | 新增 | +140 / −0 | 把现代聊天样式物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/pet_link.py` | 新增 | +25 / −0 | 把桌宠与聊天联动物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/prompt.py` | 新增 | +55 / −0 | 把AI 提示词策略物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/providers.py` | 新增 | +182 / −0 | 把Provider与流式请求协议物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/service.py` | 新增 | +253 / −0 | 把QThread 请求、取消和代际隔离物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/session_store.py` | 新增 | +784 / −0 | 把按角色/实例的会话原子写盘与异步排空物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/settings_dialog.py` | 新增 | +482 / −0 | 把聊天专属设置对话框物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/themes.py` | 新增 | +311 / −0 | 把聊天主题物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/utils.py` | 新增 | +58 / −0 | 把AI 辅助工具物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/chat/widgets.py` | 新增 | +2203 / −0 | 把现代聊天窗口物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/config.py` | 新增 | +114 / −0 | AI owner 专属配置策略、CAS 与凭据授权，Core 保留不透明数据而不解释 Provider。 |
| `features/ai_chat/host/contribution_settings.py` | 新增 | +97 / −0 | AI 专属设置由 owner 注册/撤销并保护草稿，生命周期变化不静态留 Core。 |
| `features/ai_chat/host/defaults.py` | 新增 | +131 / −0 | 包内承接完整聊天默认策略，避免 Core 内残留 AI 行为定义。 |
| `features/ai_chat/host/factory.py` | 新增 | +65 / −0 | 无 Qt/业务线程启动的惰性合法 FeatureDefinition，兼容 LPAC 安装自检。 |
| `features/ai_chat/host/file_interpret.py` | 新增 | +376 / −0 | 把文本/代码文件理解物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/island_chat.py` | 新增 | +117 / −0 | 把灵动岛 AI 接入物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/quick_chat.py` | 新增 | +471 / −0 | 把快捷对话物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `features/ai_chat/host/runtime.py` | 新增 | +210 / −0 | 真实聊天/快捷/灵动岛窗口及窄服务，QThread 取消、generation 隔离与会话排空，保留已导入 host 租约。 |
| `features/ai_chat/host/settings_file_interpret.py` | 新增 | +95 / −0 | 把文件理解专属设置物理交付至 AI owner，保持现有体验但允许无 AI Core 真正排除业务模块。 |
| `packaging/core_code_gate.iss.inc` | 新增 | +165 / −0 | 原生 Inno 验证依赖子树的链接/硬链接与文件共享锁，阻止占用期间替换或物理删除 Core。 |
| `packaging/core_removal_gate.iss.inc` | 新增 | +48 / −0 | 原生父端持固定 Shell APPDATA 的独占根级卸载门至删除结束，避免跨 Core 副本在 receipt 后重装 DLC。 |
| `packaging/core_webm.iss` | 新增 | +139 / −0 | 独立当前用户小 Core Setup，选装默认不选/离线旁置/不记旧组件/不自动关进程；失败可中止的 Core 卸载前置门。；245把英文原因间隔写成显式Pascal空格，不靠行尾空白。 |
| `pet/__main__.py` | 修改 | +65 / −1 | 在普通运行服务前分派新产品专用本地包意图、导入和卸载维护入口，互斥参数与真实父端证据拒绝源码回退。 |
| `pet/agent_link.py` | 修改 | +200 / −66 | 新产品hook绑定数据根ID、distinct脚本与有界读写锁；bridge只移除明确当前Core路径；未知或冲突保持原配置，保留旧产品路径。 |
| `pet/ai_bindings.py` | 新增 | +179 / −0 | Core-owned AI 窄服务路由和当前实例绑定；不包含 Provider/业务请求实现。 |
| `pet/app.py` | 修改 | +237 / −81 | 接入两个独立 owner 的实际启动加载确认、贡献路由及非阻塞退出；修复正常 Core 不能自行确认的 UX-M3，不让设置代替 Core。 |
| `pet/async_exit.py` | 新增 | +114 / −0 | 应用拥有的 Quit gate 非阻塞等待后台排空，避免 GUI wait/terminate 或窗口关闭丢失任务。 |
| `pet/autostart.py` | 修改 | +2 / −1 | 新小Core仅枚举自己的Run值，不清理旧产品自启动；保留旧产品兼容路径。 |
| `pet/balance.py` | 修改 | +3 / −3 | 移除对聊天 Provider 实现的依赖，维持账户用量领域在无 AI Core 中可用。 |
| `pet/balance_config.py` | 新增 | +104 / −0 | 拆出余额领域配置/凭据 CAS，与 AI 专属 Provider 策略分离。 |
| `pet/catalog.py` | 修改 | +6 / −0 | 先使用统一 RuntimeLayout 的内容根，保留明确旧构建路径，便携不能偷偷回 APPDATA。 |
| `pet/chat/__init__.py` | 修改 | +11 / −1 | 受限模块边界实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/ai_settings_page.py` | 修改 | +7 / −659 | 聊天配置页实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/crop_dialog.py` | 修改 | +7 / −177 | 附件裁剪界面实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/external_turns.py` | 修改 | +7 / −73 | 外部文字结果同步实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/geometry.py` | 修改 | +7 / −55 | 聊天窗口几何实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/legacy_widgets.py` | 修改 | +7 / −1088 | 既有经典聊天窗口实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/models.py` | 修改 | +29 / −232 | 聊天/Provider/会话数据模型实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/pet_link.py` | 修改 | +7 / −21 | 桌宠与聊天联动实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/prompt.py` | 修改 | +7 / −51 | AI 提示词策略实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/providers.py` | 修改 | +7 / −177 | Provider与流式请求协议实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/service.py` | 修改 | +10 / −173 | QThread 请求、取消和代际隔离实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/session_store.py` | 修改 | +7 / −632 | 按角色/实例的会话原子写盘与异步排空实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/settings_dialog.py` | 修改 | +7 / −478 | 聊天专属设置对话框实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/themes.py` | 修改 | +6 / −300 | 聊天主题实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/utils.py` | 修改 | +7 / −54 | AI 辅助工具实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/chat/widgets.py` | 修改 | +7 / −2185 | 现代聊天窗口实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/config.py` | 修改 | +72 / −114 | 新产品配置采用确定的布局、保留未安装 AI 的不透明设置数据；新产品禁止自动复制旧真实配置/会话。 |
| `pet/content/paths.py` | 修改 | +5 / −0 | 资源存储随 RuntimeLayout 数据根，避免便携数据和 DLC 分裂到旧产品目录。 |
| `pet/context_menus/legacy.py` | 修改 | +8 / −1 | 旧菜单构造用 owner 服务授权路由，不静态加载 AI 实现；保持旧构建可用。 |
| `pet/context_menus/registry.py` | 修改 | +6 / −0 | 按 AI owner 注册/撤销聊天与文件理解命令，卸载或停用不能残留可执行入口。 |
| `pet/core_code_gate.py` | 新增 | +84 / −0 | 在 normal Core/settings 运行期 pin 可执行依赖，给安装器更新/删除提供真实跨进程文件占用门。 |
| `pet/core_maintenance.py` | 新增 | +75 / −0 | 真实移除receipt后先检查当前Core bridge收尾再清理系统注册；失败保留恢复，不广泛删除其他安装联动。 |
| `pet/core_registration_cleanup.py` | 新增 | +74 / −0 | Core卸载最后一步只删除与当前可执行文件身份严格匹配的新产品Run注册；未知命令、占用/权限错误保持恢复，旧注册不被认领。 |
| `pet/core_uninstall.py` | 新增 | +198 / −0 | 协调两个独立真实卸载事务和最终代码清理 receipt，部分失败阻止 Core 删除且已接受包不复活。 |
| `pet/core_uninstall_ui.py` | 新增 | +169 / −0 | 真实异步卸载确认/恢复工具，GUI 不提交成功或同步等待后台操作。 |
| `pet/credentials.py` | 修改 | +26 / −7 | 按 data_root_id、实例及 owner 分区安全存储引用；同用户移动 NTFS 根不依赖绝对路径身份。 |
| `pet/feature_build_policy.py` | 修改 | +2 / −1 | 声明新产品正式信任、执行及能力合同；源码默认空锚/空 probe 摘要保持 fail-closed，不把测试 key 写入正式源策略。 |
| `pet/feature_distribution.py` | 修改 | +3 / −0 | 统一双包 verified resolver 与来源策略，Core 仅允许已知官方 ID，不扩大动态代码发现。 |
| `pet/feature_host_bindings.py` | 修改 | +61 / −1 | 把宿主设置/生命周期端口按 owner 绑定，使 AI 与屏幕理解分别拥有凭据、草稿和贡献。 |
| `pet/feature_install_state.py` | 修改 | +32 / −20 | 唯一 state.json 存储参数化到官方 owner，保留 SCREEN 历史账本兼容与 revision/CAS。 |
| `pet/feature_lifecycle.py` | 修改 | +7 / −2 | 生命周期请求绑定 owner，queued 撤销/排空而不跨线程访问 Qt 对象。 |
| `pet/feature_lifecycle_contract.py` | 修改 | +19 / −2 | 准备授权与租约 live-owner 证据按包隔离，操作一个包不能准备另一个包。 |
| `pet/feature_management.py` | 修改 | +137 / −16 | 双owner事务后台及Qt queued生命周期路由；拥有材料启动/恢复维护在线程中执行，清理警告与安装结果分开，不让GUI同步等待或读改后台Qt对象。 |
| `pet/feature_management_ui.py` | 修改 | +46 / −12 | 双包独立异步操作、不可变确认、恢复和占用展示；新增word-wrap/accessible清理警告，不以GC失败撤销已成功安装。 |
| `pet/feature_package_probe.py` | 修改 | +13 / −11 | 增加 v2 host-only 自检分支，AI Worker 明确 not_applicable，不伪造 HELLO 成功。 |
| `pet/feature_package_startup.py` | 修改 | +59 / −28 | 生产启动加载回执绑定 owner/版本/摘要/revision，短管理锁竞争有界重试，候选 pending 不开放执行。 |
| `pet/feature_package_transactions.py` | 修改 | +96 / −58 | 两包共用事务实现并独立日志/账本，完善卸载孤立文件证据与恢复边界，不假装跨包原子。 |
| `pet/feature_ports.py` | 修改 | +19 / −1 | 提供 Core 通用 UI/网络/安全存储端口与窄 chat.external-turn/v1 服务，业务实现留在 AI 包。 |
| `pet/feature_probe_adapter.py` | 修改 | +49 / −4 | probe前记录本次材料拥有意图，结束后持久化可信进程释放/profile恢复证据及回收通知；不把候选自报布尔值当安全授权。 |
| `pet/feature_probe_materials.py` | 新增 | +229 / −0 | 新增Qt无关的probe材料拥有账本、写前意图与有界GC；仅可信父端证明释放/恢复且边界合法时回收，不猜测未知目录或删除活进程材料。 |
| `pet/feature_probe_windows.py` | 修改 | +22 / −8 | 恢复清理用query+synchronize权限核验进程实际已退出；即使cleaned阶段仍拒绝活进程，保存父端profile/Job释放证明，不请求PROCESS_TERMINATE。 |
| `pet/feature_startup_contract.py` | 修改 | +18 / −2 | permit/receipt 按 owner 及真实租约绑定，管理界面不能提交加载成功。 |
| `pet/feature_version_lease.py` | 修改 | +7 / −3 | 将版本租约的已知官方身份参数化，host/settings/Worker 占用仍由内核锁证明。 |
| `pet/file_interpret.py` | 修改 | +10 / −341 | 文本/代码文件理解实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/frozen_runtime_paths.py` | 新增 | +37 / −0 | 新增仅 Windows frozen/同一 exe 的 _internal 原生路径别名；保留 PYZ 优先，拒绝 UNC/逃逸/不同目录，不添加源码路径。 |
| `pet/harness_launcher.py` | 修改 | +5 / −0 | 独立设置入口随确定的 RuntimeLayout 与官方 owner 解析，不借用源码或错误数据根。 |
| `pet/island_chat.py` | 修改 | +7 / −113 | 灵动岛 AI 接入实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/local_package_intents.py` | 新增 | +139 / −0 | 旁置包只传本地意图；Core-owned 服务逐包预检与不可变确认，缺包/单包失败不阻塞 Core。 |
| `pet/model_access_tracker.py` | 修改 | +10 / −6 | 类型明确接受现有SemanticEvent/AgentEvent，局部dict窄化消除mypy错误，不增加运行期导入或改变重试判定。 |
| `pet/modern_settings_dialog.py` | 修改 | +137 / −116 | 真实挂载双包管理、独立余额和显式旧数据导入；保持导航顺序，补搜索与 data-import 深链。 |
| `pet/official_features.py` | 修改 | +43 / −4 | 固定两个官方包、factory、execution_kind 和能力上限；AI 不获得截图能力。 |
| `pet/plugins/feature_host.py` | 修改 | +7 / −1 | 让业务 UI/服务依 owner 注册和撤销，支持请求排空后授权刷新但不热卸载 Python。 |
| `pet/plugins/feature_packages.py` | 修改 | +4 / −0 | 统一用途化已验证选择与双包描述，pending 执行禁令和 host-only 合同保持独立。 |
| `pet/plugins/package_binding.py` | 修改 | +7 / −1 | 绑定生命周期/加载租约至确定官方身份，避免 host/Worker/设置混包。 |
| `pet/plugins/package_trust.py` | 修改 | +116 / −21 | 验证签名保护的 format_version/key_id/execution_kind、撤销策略与 host-only worker=null，保留明确 v1 兼容而无自带公钥信任。 |
| `pet/quick_chat.py` | 修改 | +7 / −460 | 快捷对话实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/runtime_credential_import.py` | 新增 | +351 / −0 | 只迁移明确来源引用的秘密，经授权安全存储到安全存储；预检不枚举/读秘密，目标新凭据拒绝覆盖。 |
| `pet/runtime_data_import.py` | 新增 | +560 / −0 | 有界显式 JSON/会话导入、源/目标 CAS、pending 恢复与管理/数据锁分离；不收编旧可执行安装。 |
| `pet/runtime_data_import_entry.py` | 新增 | +106 / −0 | 封闭 --import-local-data 入口及固定自身 executable 的启动命令，导入工具只持 Core-removal 屏障不自锁数据。 |
| `pet/runtime_data_import_ui.py` | 新增 | +267 / −0 | 不可变映射/凭据授权确认、wrapped 长文案、未接受取消及安全重试；应用拥有工作线程与关闭后排空。 |
| `pet/runtime_layout.py` | 新增 | +331 / −0 | 服务初始化前选择新产品数据根/显式 NTFS 便携、稳定 UUID 与真实路径 IPC、根级共享/独占删除和导入屏障。 |
| `pet/runtime_resource_import.py` | 新增 | +88 / −0 | 显式导入仅接受闭合已验内容/角色资源及有效版本指针；媒体和总量限制、manifest哈希/兼容性/未登记文件拒绝，不收编旧可执行代码。 |
| `pet/settings_balance.py` | 新增 | +169 / −0 | 无 AI 时独立的掩码密钥与余额配置 UI，明确授权读取/修改并按 CAS 写入。 |
| `pet/settings_feature_lifecycle.py` | 新增 | +149 / −0 | 设置进程的 owner 草稿保护、贡献撤销、真实加载接线与独立租约，不让管理页触发 Worker。 |
| `pet/settings_file_interpret.py` | 修改 | +7 / −89 | 文件理解专属设置实现移至 AI 包；此文件仅明确旧内置构建门控的兼容别名，新小 Core 不以它回退业务实现。 |
| `pet/settings_pet_controls.py` | 修改 | +3 / −1 | 文件理解设置通过 AI owner 授权端口，未装 AI 不加载专属实现或误改模拟投喂行为。 |
| `pet/settings_widgets.py` | 修改 | +25 / −4 | 保持可选 owner 设置宿主/生命周期语义，给新管理与导入操作复用现有响应式组件。 |
| `pet/window_optional_services.py` | 修改 | +9 / −3 | 可选聊天/灵动岛接入统一发现和授权路由，无 AI 包不导入业务 UI。 |
| `pet/workers/screen_entry.py` | 修改 | +3 / −0 | 在探针或正常租约分派前统一处理自有冻结依赖目录；不跳过租约接管。 |
| `scripts/build_feature_probe_native.py` | 修改 | +117 / −0 | 固定上游 seam，显式扩展本地归档/PYZ路径并只给密封 Python3.11 设置三条已有路径；避免 LPAC 下 MAX_PATH/getpath 边界失败。 |
| `scripts/build_feature_release.py` | 新增 | +656 / −0 | 生产 helper/Worker/Core 独占快照构建、闭合依赖和真实 PYZ/PE 审计、来源 receipt/预算；没有正式 key 不构建假生产 Core。 |
| `scripts/build_local_archives.py` | 新增 | +165 / −0 | 受批准信任/真实Core审计约束的流式ZIP交付；源变化、未知数据、独占输出和空间预算均失败关闭，便携标记显式。 |
| `scripts/build_screen_worker.py` | 修改 | +1 / −0 | 将同一冻结路径工具纳入独立 Worker 构建源集合。 |
| `scripts/feature_probe_canary.py` | 修改 | +13 / −2 | 网络canary使用可信原生Winsock API，输出阶段/原因码/是否抵达connect；缺原生能力不能当沙箱成功，winreg惰性导入保持其他平台可收集。 |
| `scripts/feature_probe_entry.py` | 修改 | +21 / −1 | 原生冻结 probe 惰性合同检查接入 v2/host-only，受限公共 SDK 不导入 GUI/模型请求。 |
| `scripts/feature_probe_native_extension.c` | 修改 | +86 / −0 | 增加仅限生成IPv4/IPv6 loopback的有界原生Winsock探针；加载系统DLL、记录初始化/建socket/connect阶段并关闭socket/库，隔离失败无普通进程回退。 |
| `scripts/feature_release_cli.py` | 新增 | +257 / −0 | 离线密钥/签名/备份检查/分发验证入口，密码仅本地 masked Qt UI，拒绝把下载包公钥当可信源。 |
| `scripts/feature_release_materials.py` | 新增 | +267 / −0 | 按固定 owner 收集完整 DLC 源材料与 Core 通用依赖/排除清单，先安全快照再签名，不执行候选。 |
| `scripts/feature_release_signing.py` | 新增 | +198 / −0 | 成熟 Ed25519 加密 PKCS#8 私钥独占外仓创建、精确公钥批准/撤销与离线包签名验证；不覆盖既有密钥。 |
| `scripts/release_distribution.py` | 新增 | +219 / −0 | 分发清单/签名及验证产物和许可边界，验证依赖预先批准信任而非同包公钥自证。 |
| `scripts/validate_feature_probe_windows.py` | 修改 | +27 / −1 | 核对原生网络正负对照的API/阶段/错误码；真实WSAStartup拒绝与connect拒绝分别保留，不以Python模块初始化失败冒充网络隔离。 |
| `scripts/validate_phase5a_delivery.py` | 新增 | +370 / −0 | 新增自有PID/创建时间/exe绑定的真实UIA管理与正常Core验收；原生菜单退出，无强退、状态改写、factory旁路或桌面截图。 |
| `tests/_feature_startup_child.py` | 修改 | +66 / −4 | 真实子进程夹具增加 owner/启动证据接线，验证正常 Core 流程而非管理 UI 假回执。 |
| `tests/test_agent_registration_scope.py` | 新增 | +231 / −0 | 生成HOME/profile及原生junction验证新数据根hook、当前Core bridge拥有边界、未知配置保留与Core收尾失败。 |
| `tests/test_ai_delivery_boundaries.py` | 新增 | +122 / −0 | 以公开 seam/生成夹具验证 `test_ai_implementation_lives_in_the_feature_not_legacy_core_modules`, `test_ai_factory_contract_is_lazy_without_qt_or_business_imports`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_ai_host_contracts.py` | 新增 | +275 / −0 | 以公开 seam/生成夹具验证 `test_ai_settings_namespace_cas_and_no_plaintext_secret`, `test_ai_configuration_rejects_raw_secret_and_other_owner`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_ai_runtime_windows.py` | 新增 | +571 / −0 | 以公开 seam/生成夹具验证 `test_ai_actual_windows_use_owner_data_and_runtime_services`, `test_ai_display_ports_do_not_grant_core_config_or_installation_paths`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_async_ai_lifecycle.py` | 新增 | +375 / −0 | 以公开 seam/生成夹具验证 `test_shutdown_never_calls_qthread_wait_and_drains_asynchronously`, `test_same_request_id_cannot_accept_previous_generation_results`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_autostart.py` | 修改 | +10 / −0 | 公开Run枚举回归验证新产品不采用或删除旧安装的注册值，生成注册表边界不触碰真实启动项。 |
| `tests/test_build_feature_release.py` | 新增 | +314 / −0 | 以公开 seam/生成夹具验证 `test_core_has_fixed_normal_bootstrap_and_physical_owner_exclusion`, `test_embedded_policy_requires_independently_approved_identity`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_build_local_archives.py` | 新增 | +162 / −0 | 先红后绿验证DLC/普通/便携ZIP公开合同、源变化、预算与文件拥有边界，不代替真实安装。 |
| `tests/test_chat_service.py` | 修改 | +17 / −4 | 以公开 seam/生成夹具验证 `test_concurrent_send_keeps_both_workers_in_set_and_cancels_old`, `test_worker_finished_removes_from_workers_set`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_chat_shared_behavior.py` | 修改 | +1 / −1 | 以公开 seam/生成夹具验证 `test_short_title_custom_title_wins_and_strips`, `test_short_title_blank_custom_title_falls_through`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_chat_subsystem.py` | 修改 | +27 / −0 | 以公开 seam/生成夹具验证 `test_chat_follows_delayed_scroll_range_and_respects_manual_reading`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_core_balance_config.py` | 新增 | +104 / −0 | 以公开 seam/生成夹具验证 `test_balance_has_independent_secret_scope_and_never_adopts_chat_key`, `test_balance_move_preserves_root_identity_not_old_absolute_path`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_core_code_gate.py` | 新增 | +114 / −0 | 以公开 seam/生成夹具验证 `test_replacement_waits_for_all_runtime_code_handles`, `test_real_child_holds_code_until_natural_process_exit`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_core_maintenance_entry.py` | 新增 | +71 / −0 | 以公开 seam/生成夹具验证 `test_closed_maintenance_route_calls_only_trusted_removal_entry`, `test_maintenance_rejects_extra_routes_before_data_access`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_core_registration_cleanup.py` | 新增 | +112 / −0 | 覆盖当前Core严格匹配、另一Core保留、模糊命令/权限错误及真实包卸载证据前置，失败不得误报Core卸载完成。 |
| `tests/test_core_setup_gate.py` | 新增 | +219 / −0 | 以公开 seam/生成夹具验证 `test_native_installer_and_python_use_same_kernel_range`, `test_native_gate_rejects_hardlinked_lock_without_touching_target`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_core_setup_template.py` | 新增 | +87 / −0 | 以公开 seam/生成夹具验证 `test_new_product_never_restores_packages_or_closes_processes`, `test_install_and_uninstall_hold_real_barrier_before_code_changes`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_core_uninstall.py` | 新增 | +147 / −0 | 以公开 seam/生成夹具验证 `test_core_removal_requires_explicit_immutable_two_owner_confirmation`, `test_core_removal_cleans_both_real_packages_without_loading_candidates`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_core_uninstall_ui.py` | 新增 | +133 / −0 | 以公开 seam/生成夹具验证 `test_real_two_owner_removal_is_off_gui_and_requires_confirmation`, `test_window_close_during_write_does_not_abort_accepted_uninstall`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_desktop_pet_features.py` | 修改 | +20 / −1 | 以公开 seam/生成夹具验证 `test_product_copy_scanner_exempts_only_markdown_inline_git_refs`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_feature_management.py` | 修改 | +190 / −0 | 双包后台、真实排空/恢复、关闭后queued结果、probe材料维护与失败警告公开seam回归；不通过UI提交加载成功。 |
| `tests/test_feature_management_ui.py` | 修改 | +102 / −0 | 真实Qt事件循环覆盖双包确认/可访问性/长文案/关闭窗口迟到结果和维护警告，避免失效QObject访问。 |
| `tests/test_feature_package_transactions.py` | 修改 | +37 / −0 | 以公开 seam/生成夹具验证 `test_empty_ledger_does_not_hide_orphan_code_during_uninstall`, `test_orphan_created_during_uninstall_cannot_be_reported_as_complete`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_feature_probe_adapter.py` | 修改 | +3 / −1 | 适配器夹具显式绑定真实VerificationLimits，并回归父端材料账本与同卷快照/原生回执；不伪造隔离成功。 |
| `tests/test_feature_probe_build.py` | 修改 | +120 / −0 | 增加原生Winsock API正对照、实际初始化拒绝/连接拒绝分层及未抵达边界失败的测试；不以注入结果代替本机原生矩阵224。 |
| `tests/test_feature_probe_materials.py` | 新增 | +246 / −0 | 公开seam覆盖有界材料回收、进程活跃/证据冲突/链接/未知根拒绝、部分删除与重启恢复、重复GC幂等；先红后绿。 |
| `tests/test_feature_release_cli.py` | 新增 | +291 / −0 | 以公开 seam/生成夹具验证 `test_cli_cancel_does_not_create_key_or_public_policy`, `test_cli_argument_errors_do_not_echo_a_secret`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_feature_release_materials.py` | 新增 | +193 / −0 | 以公开 seam/生成夹具验证 `test_actual_ai_sources_become_complete_v2_host_only_payload_without_execution`, `test_screen_requires_its_own_worker_tree_and_never_fabricates_one`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_feature_release_signing.py` | 新增 | +103 / −0 | 以公开 seam/生成夹具验证 `test_encrypted_key_creation_is_exclusive_and_outside_repository`, `test_key_wrong_password_plaintext_and_backup_mismatch_are_safe_errors`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_frozen_runtime_paths.py` | 新增 | +61 / −0 | 新增只允许同一自有 local onedir 的长/扩展路径、幂等及非法布局失败回归。 |
| `tests/test_local_package_entry.py` | 新增 | +91 / −0 | 以公开 seam/生成夹具验证 `test_local_intent_holds_normal_barrier_then_layout_and_closed_gui`, `test_malformed_intent_stops_before_data_initialization`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_local_package_intents.py` | 新增 | +145 / −0 | 以公开 seam/生成夹具验证 `test_parse_intents_has_closed_owner_list_and_no_source_execution`, `test_setup_intent_only_preflights_real_signed_zip_without_enabling_or_execution`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_menu_layout.py` | 修改 | +27 / −0 | 以公开 seam/生成夹具验证 `test_settings_header_tracks_native_scrollbar_visibility`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_official_feature_contracts.py` | 新增 | +420 / −0 | 以公开 seam/生成夹具验证 `test_v2_host_only_is_verified_without_candidate_import`, `test_v2_screen_still_requires_real_worker`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_phase5a_delivery_acceptance.py` | 新增 | +77 / −0 | 新增环境封闭、PID身份、状态和菜单组合、路径及坐标边界等9项验收驱动合同。 |
| `tests/test_release_distribution.py` | 新增 | +98 / −0 | 以公开 seam/生成夹具验证 `test_public_policy_round_trip_rejects_invalid_fingerprint`, `test_distribution_round_trip_uses_external_trust_and_complete_artifact_inventory`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_remaining_owner_routes.py` | 新增 | +38 / −0 | 以公开 seam/生成夹具验证 `test_agent_cost_uses_core_balance_request_without_chat_facade`, `test_small_ai_theme_loads_owned_resource_not_core_assets`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_runtime_credential_import.py` | 新增 | +236 / −0 | 以公开 seam/生成夹具验证 `test_preflight_does_not_read_keyring_and_confirmation_binds_scopes`, `test_authorized_import_preserves_provider_and_transfers_only_bound_references`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_runtime_data_import.py` | 新增 | +454 / −0 | 以公开 seam/生成夹具验证 `test_import_preview_binds_one_source_without_adopting_code`, `test_import_preserves_ids_and_original_and_is_idempotent`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_runtime_data_import_entry.py` | 新增 | +157 / −0 | 以公开 seam/生成夹具验证 `test_closed_entry_holds_code_gate_before_import_only_bootstrap`, `test_extra_or_conflicting_args_rejected_before_user_data_io`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_runtime_data_import_ui.py` | 新增 | +296 / −0 | 以公开 seam/生成夹具验证 `test_real_preview_shows_source_mapping_and_writes_only_after_confirmation`, `test_secret_reference_transfer_needs_separate_visible_authorization`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_runtime_layout.py` | 新增 | +381 / −0 | 以公开 seam/生成夹具验证 `test_normal_layout_has_new_product_root_and_stable_identity`, `test_portable_identity_survives_move_but_ipc_path_does_not`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_settings_balance.py` | 新增 | +145 / −0 | 以公开 seam/生成夹具验证 `test_balance_widget_is_masked_explicit_and_never_reads_secrets_on_open`, `test_balance_cas_error_retains_draft_and_does_not_claim_saved`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |
| `tests/test_small_core_config.py` | 新增 | +82 / −0 | 以公开 seam/生成夹具验证 `test_small_core_does_not_supply_provider_or_file_policy_defaults`, `test_retained_ai_data_is_not_normalized_and_never_reads_legacy_credentials`；防双 owner、迁移/打包或异步退出回归（不代替正式用户验收）。 |

## 三、实现要点与失败复盘

- 文档285第一次按二级标题全文匹配报告四级当前标题失败，五份记录已先写入；随后只替换报告明确当前区块，未重复追加记录。日志索引原Phase5条目为列表不是预期表行，改为在实际已有四列表头插入一次新记录。均为文档整理错误，不改变产品源码或安全合同；后续文档门复核。

### 新生产自检材料回收与失败复盘（256～279）

- 新`pet/feature_probe_materials.py`以按owner维护锁和父端材料意图限制重试增长：先记录owned root再复制；进入deleting先持久化；删除仅在真实PID创建身份/profile/Job释放证据和完整无链接/硬链接遍历通过后进行。未知目录、活PID或证据冲突仅产生独立可诊断警告，不扩大路径，不改变安装状态。
- 256先红3项；258暴露descriptor字段id；259转绿39 passed /2 skipped。260真实Qt seam先红3项；262唯一新UI断言因显示专用零宽折行符失败，tooltip/accessibleDescription保留原原因码后修测试。264/265夹具错误回溯，266真正暴露两套snapshot超单包GC上限；有界GC聚合而执行verifier不放宽。267通过95 passed /2 skipped；真实自有进程自然退出回归不强退。
- 生产FeatureManagementRuntime以queued信号传递清理警告，QObject只由所属线程访问；启动与恢复/GC重试均可调用，关闭窗口后的迟到结果有回归。警告不伪装安装失败、不偷偷停用，并以长文本折行/无障碍原文展示。
- 271仅基准汇总脚本NameError，已独立验20个实际自检及回收样本；原退出1保留。277归档成功但Setup WinError2，是JavaScript生成Python脚本时反斜线解释成控制字符，非Inno或产品失败；279使用正斜线绝对路径只重试未创建的Setup，未重建/覆盖成功ZIP。


1. 双包管理独立 authority/结果/草稿/租约；AI 是 host-only，Worker结果 `not_applicable` 而非伪造成功。
2. AI 25个Python模块及两套QSS位于 `features/ai_chat/host`；Core 只保留通用承载/服务路由，余额拆为独立端口。真实窗口、QThread取消/迟到代际隔离、会话写入排空与非阻塞 Quit 有生成 Provider 回归。
3. probe-01 漏公共 SDK `pet.feature_ports` 导致实际两包 factory 失败：先补 red60/green61 公共材料回归，重建 probe-02后通过两包 LPAC；没有扩大 ACL/Win32k/Job 限制。
4. 导入 UI 长文案与取消重试：red126布局→127取消误应用→green128；管理/数据锁错误耦合 red129→green130；错误Config夹具red131不算产品红，修正后缺真实深链red132→green133。成功迁入的普通会话路径经真实 AI SessionStore验证。
5. 导入接受/恢复/清理使用 management→data-access；只清理未接受的 operation-owned 快照。凭据引用授权默认未勾选，预检不读/枚举秘密，引用映射绑来源UUID/确认摘要；OS安全存储迁移在明确接受后才做，不把秘密写进 journal、snapshot 或备份。
6. Core 替换/删除按 native文件锁、固定Shell数据根屏障和真实维护receipt；不能在其他Core副本重装DLC后拿旧receipt删除Core。原生父端持根独占门到删除结束；两包单独卸载，不承诺跨包回滚。

## 四、性能分析

### 2026-10-05 最新生产父端自检与Core03产物（270～279）

环境：Windows 11 build26100、Python3.11.1 /PySide6 6.11.1、20逻辑CPU；使用同组自有profile和生成材料，未读取真实凭据/桌面。命令：`python -X utf8 .scratch/phase5a-local-distribution/formal-probe-recovery-271.py`（20次），父端普通权限，可信helper05/正式历史两包；每次完成原生Job/profile释放后有界材料GC。汇总程序最后一行`process`未定义，原退出1保留；`benchmark-271-validation.json`独立验证20条协议/30原生parent receipt、owner材料账本与重复GC，样本没有重跑。未采集的RSS/IO不补填。

|路径|有效样本|median ms|p95 ms|10次回收B|
|---|---:|---:|---:|---:|
|AI host-only LPAC＋释放与GC|10|3552.794|3814.0633|198,911,947|
|屏幕host/Worker LPAC＋释放与GC|10|16583.77145|19648.1784|1,675,446,804|

**开销回答**：材料回收在启动或用户安全重试时按owner queued后台线程执行，不添加高频poll；每次新增专用内核锁、父端64KiB有界journal、PID/profile系统查询、证据内目录枚举与文件删除。Job/PID/profile仍由可信父端证明释放，无网络请求；没有持management/state锁进行复制、哈希或进程等待。2份host/Worker快照的GC聚合上限与执行verifier上限分别设置，执行验证未放宽。20次共释放1,874,358,751B，至多8条recent记录，完成attempts为空；不能据此证明长期无内存泄漏，空闲RSS/线程/IO和>=10状态改变路径仍待最终性能门。

|Core03/新分发候选|下载/文件B|展开B|时间/说明|
|---|---:|---:|---|
|onedir Core03|455,780,427|455,780,427|1,424文件/2,260实际PYZ；PyInstaller133.113s，构建服务180.321s，含预算扫描父端219.955s；n=1|
|普通ZIP277|259,368,713|455,780,427|36.0373s；SHA-256 `9a2db25a93f3b26789f92487cbde984a6db4dd221d9902a3f9011232db616927`；n=1|
|NTFS便携ZIP277|259,368,880|455,780,500|24.3316s；SHA-256 `e44bf6b7e6460aae74818fe4dd70a1b5f606a100040a640bb517449e3739f2a2`；n=1|
|正式屏幕DLC ZIP277|24,455,042|66,487,121|6.39794s；与历史正式包摘要相同，不冒充新重签|
|AI DLC1.0.1|未正式签名|未交付|ai-unsigned-02待本地密码，新目标ai-signed-02，不改已签1.0.0|
|Setup279|222,182,771B；119.579s，退出0|SHA256 `1dbbe87cbf21abebb81825d46c5632e4667a0d0f632e0b4069959cee7a6d7233`|首次277路径转义错误保留；279仅重试Setup编译，未执行安装，Authenticode未具备|

272构建前拥有根5,929,965,643B；授权8GiB后保留2GiB Core预留。每次归档/复制先重复不跟随reparse的总量计数；277另留300MiB给测试记录/签包。上述体积是最新候选而非发布；正式分发签名、实装、Authenticode、SmartScreen与干净环境门尚未完成。


### 2026-10-05 五产物平面候选、总签名与独立核验（293、294、296）

| 路径 / 边界 | 实测 / 样本与限制 |
|---|---|
| 293本地生成物独占复制 | Setup12.721215s、普通ZIP1.658948s、便携ZIP1.839032s、AI ZIP0.021480s、screen ZIP0.169525s；各n=1，前后SHA256及全组复核；这是本地复制，不是网络下载 |
| 294总分发签名并即时核验 | 7.809986s /n=1；可信父端启动本地masked输入窗口，使用既有加密正式密钥；没有网络、真实安装或密钥覆盖 |
| 296独立核验 | 1.249367s /n=1；独立进程重读五产物、清单和签名，用外部已核对公钥政策；不使用同包附带公钥 |
| 296拥有根空间测量 | no-follow扫描145.498539s /n=1，136,746常规文件、36链接/reparse剪枝；8,374,833,244B，8GiB内余215,101,348B（205.137MiB） |
| 普通/便携ZIP解压体积 | 未实际解压，只读ZIP清单：普通1,424成员 /455,780,427B；便携1,425成员 /455,780,500B，portable.json固定data子目录 |

以上新增路径只在构建/签名/核验操作触发，普通Core空闲并不运行这些扫描。实测成本是本地文件打开、顺序读取/写入、SHA256、Ed25519及新建验收子进程；未新增业务网络、模型线程或常驻轮询。复制临时输出与证据均留在本轮拥有根，没有修改正式安装目录。该切片未采集RSS/系统调用逐项计数或连续构建峰值，不作“可忽略”或median/p95结论；n=1不能代替批准的最终性能样本量。空间数字是296时刻保留生成物快照，之后只追加少量文本/证据，最终文本门另行记录。

### 历史普通JSON导入基准135（不是DLC安装性能）

Windows64 build26100，Python3.11.1、PySide6 6.11.1、psutil6.1.0；真实 E盘NTFS（生产RuntimeLayout调用卷类型查询），源码 warm进程，仅本轮生成配置 + 100条生成消息的会话（约75KiB）。命令：`python -X utf8 .scratch/phase5a-local-distribution/bench-import-135.py`；固定 APPDATA 指向自有测试根。样本包含实际哈希、原子写、内核锁和安全目录清理，不包含真实凭据，也不执行AI请求。

| 源码路径 | n | median ms | p95 ms（nearest-rank） |
|---|---:|---:|---:|
| preflight | 20 | 41.052 | 115.257 |
| cancel_preflight | 10 | 39.690 | 107.268 |
| apply | 10 | 168.159 | 382.651 |
| idempotent_retry | 10 | 18.466 | 34.408 |
| waiting_release | 10 | 30.498 | 41.097 |
| recover_pending | 10 | 79.154 | 121.167 |

测量进程起始RSS 28,037,120B，结束29,818,880B，增长1,781,760B；线程5→4、句柄178→182。累计I/O读计数312→1140、写0→415；读字节3,657,584→14,136,131、写0→7,583,935；最终夹具普通文件5,274,877B。包括fixture准备与Windows缓存/元数据I/O，不等于物理磁盘吞吐或泄漏证明。

- **稳态开销**：新冻结Core/管理页idle与旧版对照尚未测；不能以这些操作样本声称零新增稳态开销。
- **新增成本/频率**：上述路径只由用户预检、确认、取消/重试或恢复触发；不是识屏/动画逐帧执行。本轮导入预检 n=20、其他列各n=10，数字只代表当前JSON源码路径。
- **系统调用/网络/磁盘/线程**：导入新增管理/数据文件锁、hash/read、journal/原子rename/write及安全delete；UI新应用拥有的后台线程经queued回 GUI，完成后drain。此基准不启动业务线程或调用网络/凭据backend；没有抓包或全系统调用归因，不能从源码推断实机全系统零网络。
- **内存/缓存**：上述净增长1.699MiB，含warm模块/Windows开销；不是长期soak。句柄净+4需随最终冻结过程测量确认，不能先宣称绝无增长。
- **尚缺**：最终小Core启动n≥10、probe n≥10、各DLC状态变更n≥10、加载/卸载/GC、管理页idle及构建峰值；DLC预检217已补4×20次，候选体积已记录，但最新Core未重建，不把旧候选当最终产物。


### 2026-10-05 正式DLC安全预检与未接受取消217

命令：`python -X utf8 .scratch/phase5a-local-distribution/bench-formal-preflight-217.py`；Windows build26100、Python3.11.1、E盘NTFS、正式签名目录/ZIP、真实FeaturePackageTransactionService；与full214并发。每行20次；NoExecution守卫使factory或Worker调用即失败，所有未接受staging按自身operation拥有证据取消。不是冻结Core/已接受DLC事务或模型请求基准。

| 操作 | n | median ms | p95 ms | RSS增量median / p95 B | 读 / 写次数median | 读 / 写字节median |
|---|---:|---:|---:|---:|---:|---:|
| ai-directory-preflight | 20 | 1199.703 | 1367.992 | 67584 / 774144 | 495 / 41 | 10490184 / 2625613 |
| ai-directory-cancel | 20 | 152.312 | 213.452 | 4096 / 16384 | 2 / 1 | 1662 / 1680 |
| ai-zip-preflight | 20 | 576.555 | 990.689 | 219136 / 950272 | 192 / 41 | 9695572 / 2625635 |
| ai-zip-cancel | 20 | 148.838 | 338.502 | 6144 / 696320 | 2 / 1 | 1673 / 1691 |
| screen-directory-preflight | 20 | 8166.122 | 9295.272 | 354304 / 1458176 | 1736 / 173 | 266014635 / 66506812 |
| screen-directory-cancel | 20 | 401.601 | 479.346 | 0 / 684032 | 2 / 1 | 1710 / 1728 |
| screen-zip-preflight | 20 | 7256.816 | 9179.284 | 45056 / 1249280 | 796 / 173 | 139935378 / 66506852 |
| screen-zip-cancel | 20 | 377.701 | 447.396 | 0 / 0 | 2 / 1 | 1730 / 1748 |

80次预检+80次取消的进程RSS 30,367,744→37,105,664B（净+6,737,920B），线程5→3、句柄191→197；ZIP预检单次线程增量实测最大+2，不宣称零线程变化。累计进程读计数365→64,921、写0→8,660；读字节3,844,794→8,526,748,923、写0→2,765,442,128。上述是psutil进程I/O/端点差值，不是物理磁盘吞吐、完整系统调用追踪、峰值RSS或泄漏证明；GC在样本之间执行。此路径由用户操作触发，管理页idle/后台授权监控仍未测，不能以预检数据解释稳态开销。新增开销含安全复制/解压、重复签名哈希、管理/状态内核锁和journal写；未测全系统网络包，不能宣称所有网络成本为0。

新helper222实际build21.27s/父总49.323s，原生矩阵224总54.514s/权限4435.31ms（n=1）；正式双包225 AI 8.696s、screen66.641s（各n=1，含安全材料复制/清理；每个实际子探针仍30s预算）。AI的Worker不适用，不能把它补成握手通过。probe≥10及最终状态变更≥10的门尚未齐。

### 历史Core02分发候选体积（200/202；不是最终Core03）

| 候选 | ZIP/Setup B | 解压/构建B | 构建/归档s |
|---|---:|---:|---:|
| 普通Core ZIP | 259,350,992 | 455,763,782 | 27.117 |
| NTFS便携Core ZIP | 259,351,159 | 455,763,782加显式标记 | 27.685 |
| 正式AI DLC ZIP | 2,334,659 | 2,622,299（39文件；含manifest/signature） | 0.332 |
| 正式screen DLC ZIP | 24,455,042 | 66,503,402（119文件；含manifest/signature） | 4.049 |
| 当前用户Setup（仅编译） | 222,165,149 | 上述Core02 | 95.000 |

Core02真实生产构建199.044s；新helper05 17,267,820B/63文件/176PYZ模块，Worker02 66,339,336B/97文件。空间232观测5,713,826,179B，余728,624,765B，均是采样总占用而非连续峰值；最终Core2GiB预留不满足，213清理被工具策略拒绝、未删除且未绕过。启动219/220在首个样本退出菜单未找到，0个通过样本，自己的PID36460待自然退出；没有用失败运行填n=10或归因为产品bug。

## 五、实机运行记录

### 2026-10-05 Core03五产物、正式总分发与菜单驱动失败证据

五产物位于 `E:/AI/DSH/dsh-pet-indesktop/.scratch/phase5a-local-distribution/delivery-set-293`，合计767,710,064B。平面根仅包含五文件及distribution.json/sig，证据保存在其外。

| 类别 | 文件 | 字节 | SHA256 |
|---|---|---:|---|
| ai-dlc | `official.ai-chat.zip` | 2334658 | `c430a069916e50e429bc3fb4ddf8e7f4f25eeedcbe8c9d3a59a511a6c97fbb7c` |
| core-setup | `dsh-pet-core-webm-setup.exe` | 222182771 | `1dbbe87cbf21abebb81825d46c5632e4667a0d0f632e0b4069959cee7a6d7233` |
| core-zip | `dsh-pet-core-webm.zip` | 259368713 | `9a2db25a93f3b26789f92487cbde984a6db4dd221d9902a3f9011232db616927` |
| portable-zip | `dsh-pet-core-webm-portable.zip` | 259368880 | `e44bf6b7e6460aae74818fe4dd70a1b5f606a100040a640bb517449e3739f2a2` |
| screen-dlc | `official.screen-understanding.zip` | 24455042 | `e08fe291455feaf55f721d3927830dfef2b575afb81b2171e07520cb988c2b7a` |

执行命令使用本机 `E:/Program Files (x86)/Dev-Cpp/python.exe -X utf8`，驱动分别为本组 `delivery-set-293.py`、`sign-distribution-child-294.py` 和 `delivery-verify-296.py`；cwd为本仓库，APPDATA/HOME/TEMP绑定本轮拥有根，PYTHONPATH为空。293退出0、294签名子进程退出0、296退出0；公开回执分别为 `delivery-set-293-receipt.json`、`signing-launch-294/signed-receipt.json` 与 `delivery-verify-296-receipt.json`。密码只在用户本地遮罩框输入，不出现在以上命令、环境、日志或聊天。

294 UTC2026-10-05T13:58:26.426093+00:00正式签名完成，key_id为official-release-2026；296 UTC2026-10-05T14:05:41.579829+00:00独立核验通过。distribution.json SHA256为 `fc7157ead52bc73146be20e1662515e46719eb8fc795767e76cafba0ca46ec5e`，distribution.sig SHA256为 `030213e9668c91a0fdcc62e6251dfa002e1ed92470a58a264f5d38d0d26ce81a`。公钥来自先前核对的仓库外政策，不是下载包附带；签名通过不是Authenticode、SmartScreen、正式发布或实际Setup安装通过。

冻结菜单不足单独登记：287仅AI已在真实普通Core完成生产receipt（AI1.0.1、revision4、enabled、pending=null），但菜单/自然退出驱动失败，both没有跑到。290/291/292/295/297/298原失败全部保留；291曾提出的未核实Qt源码假设已撤回。295实际卡片标签为“隐藏桌宠”；297通过自有进程UIA实际按钮恢复可见，但脚本成功断言仍失败；298再次无自有像素命中，46次UIA视图为空，没有发送右键或退出、不强退。失败暂不能判为产品业务逻辑缺陷或已修复：下一步需检查该生成数据测试进程的真实渲染/点击边界，而不是追加坐标猜测。用户真实模型、旧个人数据以及其他桌宠窗口未拿作此矩阵的替代证据。

### 历史原生Windows probe-02阶段（2026-10-04；不是当前helper或正式安装）

- 实际本机重建 `native-01`、冻结 Worker `worker-01`、可信 helper `probe-02`。probe-02 bundle SHA256：`f0445bad4ef722c23f6c75038d103d57aa9f9bcdfd7356bc82d79894967b40f3`。审计固定PyInstaller6.20.0来源与原生依赖；旧冻结产物没有当作新生产构建。
- `python -m scripts.validate_feature_probe_windows <probe-02-bundle> <new-owned-fixture-root>` 原生 permissions正对照全部true，LPAC隔离负对照按预期，真实token/LPAC/Win32k/Job/句柄/输出/内存/超时/父退出/拥有profile恢复测试产生 `status: passed`。**网络负对照在socket初始化ImportError即拒绝，并未进入connect；不能据此说Winsock已连接再被内核拒绝。**正式冻结验收保留该阶段信息，若补原生connect canary也不能放松权限。
- 生成签名两包实际LPAC：screen输出 host_valid=true、worker_hello=true、worker_graceful_exit=true、isolation_enforced=true；AI host_valid=true、worker_hello=false、worker_graceful_exit=false、worker_status=not_applicable。父端总耗时SCREEN109.381s、AI38.844s（含复制/哈希/清理，不等于30秒子进程预算）；都只用内存生成集成测试key。
- Inno真实编译本轮自有小夹具，在InitializeSetup退出，没有安装文件、注册表或快捷方式；原生跨进程共享/独占锁、reparse/hardlink与固定根卸载屏障互验通过。没有把编译夹具算正式Setup安装验收。
- 新导入工具/真实设置布局、搜索、深链通过实际Qt事件循环生成数据测试；真实模型、个人配置迁移和正式用户体验**尚未验收**。当时正式私钥与公开政策尚未创建；现已创建并正式签包178及验证225，不能沿用当时的阻塞结论。

### 数据隔离事故与纠正（不得抹去）

red91入口回归没有阻止正常Core路径，且APPDATA未隔离，曾意外复制旧真实产品配置/会话约26KB到新产品APPDATA；真实local时间2026-10-05 02:41:32+08（UTC2026-10-04 18:41:32）。已中止，未算pass；只核对元数据，不能证明初始化未访问其他服务。原始数据和意外目标均保留，没有擅自删除或拿作验收。red99→green101明确禁止新产品自动旧数据导入，后续每个pytest子进程都绑定独立自有APPDATA；完整事故见WORKLOG。任何“本轮全程未触碰真实数据”的表述均已更正。

## 六、测试与验证

最新累计门为273/275/276，结果见下方当前表；下列134/136/139及234/233是注明版本的历史验证，不是最新源码/产物完成门。

| 门 | 实际命令/范围 | 结果 |
|---|---|---|
| 新导入/设置相关 | green133：真实设置/search/deeplink、导入/凭据/布局/管理相关 | 179 passed /62.46s |
| 稳定累积全量 | `python -X utf8 -m pytest -q --basetemp=.scratch/phase5a-local-distribution/pytest-full-134`，独立APPDATA134/offscreen；执行期间不改源码/测试 | 4171 passed /14 skipped /14 warnings /701.35s |
| Ruff/format | Ruff134与format134，139个本轮Python文件明确清单 | 全通过；139 already formatted |
| mypy | 配置56个受影响源码的mypy134明确清单 | Success；广义全仓旧债不据此说全仓无类型错误 |
| 高负载 | 20个本轮自有CPU进程（below-normal，无系统配置变更），真实Qt/IPC/子进程族三遍，独立APPDATA/pytest根 | 见下表，已回收所有负载进程 |
| Markdown链接 | `python -X utf8 scripts/check_docs.py` | 139：129文件通过（包含本报告、设计、记录及索引） |
| diff/报告纪律 | `git diff --check`；`pytest tests/test_pr_report_discipline.py tests/test_report_gates.py` | 139：101 passed/0.76s；diff-check退出0；随后仅记录本次结果/文档行数 |

| 满载遍数 | 输出 | pytest耗时 | CPU median / p95 |
|---|---|---:|---:|
| 1 | 178 passed | 87.67 s | 100.0% / 100.0% |
| 2 | 178 passed | 99.89 s | 100.0% / 100.0% |
| 3 | 178 passed | 92.87 s | 100.0% / 100.0% |

14个skip为平台或显式产物前提未满足，未运行不算失败也不算通过。14 warnings为既有Qt弃用及重复ZIP负向夹具；保留而非过滤。full108在执行中曾修改映射源码/测试，为混合版本1failed/4111passed结果，只留历史，已用稳定full134重跑，不据其宣称最终完成。

### 历史稳定累计门（2026-10-05：234、233、239/240）

| 门 | 最新证据 |
|---|---|
| 全量pytest | 234：4258 passed /14 skipped /14 warnings /861.35s，退出0；哈希复核pet/features/scripts/tests无运行期改动 |
| 真满载Qt/IPC/进程族 | 233：三遍各227 passed；109.71、122.16、128.58s；每遍CPU median/p95均100%，20个自有below-normal负载已回收 |
| Ruff及format | 240：ruff0.16.6全pet/features/scripts/tests通过0.249s；明确151文件format通过0.079s |
| mypy | 239配置26源4.702s、受影响56源2.649s、AI host26源0.785s；全部通过，不声称全仓旧债清零 |
| 文档/报告/构建 | 239：129文件链接通过61.411s，165 passed /5.94s，tracked diff-check退出0 |

239在隔离USERPROFILE后Python user-site中的ruff模块不可见，ruff/format最初失败且整次239退出1；240改用已安装原生绝对工具，无重装、路径注入或降门，结果通过。保留两次真实命令/退出，不以“综合成功”覆盖初次失败。最终Frozen Core/Setup/性能/用户门仍未完成；源码质量通过不等于Phase5A交付完成。

### 当前稳定累计门（2026-10-05：273、275、276）

| 门 | 实际结果 / 范围 |
|---|---|
| 全量pytest | 273：4274 passed /15 skipped /15 warnings /657.90s；独立APPDATA/HOME/临时根/offscreen，全部产品Python运行期哈希未变，退出0 |
| 真满载Qt/IPC/进程族 | 276：17族，三遍各251 passed /1 skipped；118.76、121.54、115.61s，CPU median/p95各100%/100%，20个自有负载回收，运行期源码未变 |
| Ruff/format | 275：全pet/features/scripts/tests通过0.792s，155个改动/新增Python format通过0.112s |
| mypy | 275：配置26源2.630s、受影响57源3.349s、AI host26源1.661s；全部通过，不声称清零全仓旧债 |
| 文档/报告/构建 | 275：129文档链接129.675s，165 passed /5.72s；tracked diff-check退出0；当前记录更新后另行文档复验 |

15个skip按实际平台/产物前提记录，不计通过；15warnings保留未过滤。Full273/276之后没有修改产品Python；后续文档/验收脚本不据此称全量覆盖未执行的Setup、真实模型或干净环境。


### 2026-10-05 最终文本与空间补记（301～306）

- 301原文档检查在240秒驱动预算超时，父端240.87s退出1；没有产生链接错误输出，不能算通过。实际源码先rglob遍历再过滤生成物目录，当前大WIP使遍历成本较高；只提高同一命令驱动预算至600s，302退出0：129文档链接202.207596s、101报告测试1.63s、diff-check0.361986s，新增文本空白/EOF、176文件覆盖及产品Python对273哈希一致性全部通过。
- 逐文件表有一次LOG映射使用旧闭包值（81而非93行），已改为显式传入新numstat映射并独立复核0差异；不借表生成修改产品源码。
- 305 UTC2026-10-05T14:36:32.632733+00:00再次外部公钥独立核验通过，1.963210s/n=1；143.112189s的no-follow空间快照为8,374,960,188B（7.799789GiB）/136,776文件、36链接/reparse剪枝，余214,974,404B（205.016MiB），E盘余211,506,638,848B；所有产物哈希与296相同。随后只补少量记录，未解压、构建、安装、删除或新增大产物。
- 304只读核对自有Core PID20664/创建时间/生成APPDATA/路径，目标窗口原生可见位为false，采集9个坐标的命中元数据，无截图、鼠标移动、消息、UI动作或终止；这是该时刻观察，不足以倒推298原因，更不证明自然退出通过。AI完整菜单/自然退出与both仍未完成。
- 收尾补记保持原Markdown链接目标；quality-306再次运行报告、diff、空白/文件覆盖/源码一致性，公开receipt留在签名根外。不会为文本补记重复已稳定的全量，也不将这些门冒充真实Setup或用户/干净环境验收。

## 七、已知限制与准确下一步

1. **正式信任与分发签名**：既有加密PKCS#8及核对公钥、正式screen1.0.0不变；最新AI1.0.1已取得281正式签名验证回执，总分发清单294签名与296外部预置信任独立验证通过。287仅AI自身真实加载已确认但菜单/自然退出驱动失败，完整onlyAI/both仍未通过；密钥备份恢复未执行，新目标需确认。
2. **生产源码一致性**：Core03/新的普通ZIP、便携ZIP及Setup编译已通过审计；空包/仅屏幕真实生产启动通过。不能以旧AI1.0.0与Core02四组合替代最终AI/双包矩阵。
3. **导入与数据**：JSON配置/会话、有效角色资源与托管附件有生成夹具测试；完整旧来源映射、外部附件失效提示、便携同用户凭据移动还需最终冻结验证。未导入真实数据；red91事故及保护边界保留在本报告。
4. **真实交付与性能**：真实Setup选包/缺包/损坏/离线/占用/更新/卸载、最终冻结请求/升级/回滚/重装/便携及干净环境未完整验收。启动>=10/每类状态操作>=10、RSS/IO/管理空闲数据不齐；不冒称性能门完成。
5. **安全与发布**：不需管理员/系统组件改动，不添加loopback豁免，不强退合法进程；Authenticode/SmartScreen和Inno许可未验收。离线旧Core不会知道未来撤销；其他平台、其余5B及正式发布不在本轮。

## 八、空间、授权、风险与回滚

- 当前授权上限8GiB（2026-10-05由用户追加），不是取消预算。296实测8,374,833,244B（7.799671GiB），余215,101,348B（205.137MiB）；所有新复制/构建仍先估算，末尾文本/门禁生成后另记录增量。2GiB只为重新构建Core的预留，不要求已完成产物永远保持2GiB空余。被拒绝的213/254未删除、不重试、不绕过；旧数据和旧人工构建始终保护。

- 历史空间138（2026-10-04；不是当前容量）：预算上限6GiB；高负载/基准后space138 no-follow计数1,645,640,384B（1.533GiB），132,033节点，剩余4,796,810,560B。负向链接/reparse节点剪枝且不访问目标；不得调用严格生产_owned_bytes扫描全部含负向链接的测试根并误认为包损坏。
- 仅操作本轮拥有的新输出和明确文件白名单；旧manual Core、真实profile/凭据及其他任务不清理。私钥、真实导入、真实安装/卸载另行目标确认；没有授权自动提交/推送/发布。
- 当前无Git提交，不用reset --hard。需撤回WIP时先保存当前用户改动，再按基线快照/逐文件补丁撤回本轮白名单；不得整目录覆盖或删除用户数据。正式提交获授权后逐项核对暂存及敏感文件，之后才有git revert提交回滚点。
- pending/部分删除、证据冲突保持禁用/恢复，不从目录猜安装；导入目标新编辑拒绝覆盖，Core删除门失败保留恢复能力。

## 九、当前实际可体验的效果与限制

最新Core03、普通/便携ZIP、screen正式ZIP和Setup编译已产出；空Core/仅识屏真实启动通过，AI1.0.1正式签名通过，onlyAI实际加载确认已完成，但其菜单/自然退出自动化失败，最终双包矩阵尚未通过。五产物已在delivery-set-293完成总清单签名294及独立核验296。**目前请不要安装旧候选冒充最终交付**。本次离线签名本地解锁已完成，密码不在聊天传输；真实安装/导入/卸载及人工、干净环境另行给出目标与步骤。

## 历史停点：正式密钥创建已核对（UTC 2026-10-05T01:14:39.201695+00:00）

- 用户本轮回复“已输入”，已完成之前授权的本地密码操作。可信CLI公开成功记录与正式公开策略一致，诊断日志0字节；私钥文件302B、创建UTC 2026-10-05T01:09:19.881520+00:00。仅检查私钥元数据，没有读取/展示私钥内容。
- key_id `official-release-2026`，公钥指纹 `dd18cbd51d2c2367e45efe7ec697a5e27e9b1654e54137f5f2734eec80f4d9ab`；公开策略限定两个官方owner及现有能力，未撤销。证据：`signing-launch-142/key-creation-receipt.json`。脱离式启动没有捕获退出码，不能补写退出0；可信CLI仅在两个文件成功写入后输出该成功记录。
- 正式加密密钥/公开政策位于仓库外 `E:/AI/DSH/release-signing/`；备份副本、解密/恢复检查、正式签包/冻结Core/分发验收尚未执行。创建授权不延伸到真实数据导入、安装卸载或发布。
- 下一步先补生产PYZ必须包含导入/维护真实入口的失败回归，以及LPAC网络canary必须实际抵达Winsock连接调用的证据，保留安全边界；随后继续剩余资源导入、新产品自启动清理和正式构建。
- Phase5A/5B-1仍未完整交付。既有full134/负载136为修改前源码历史；无提交/推送/发布/子智能体。

### 当前实际效果与限制
密码操作已完成，本轮不需要在聊天提供任何秘密；尚不能把新小Core视为正式交付。以下密码等待状态为当时的历史事实，不是当前状态。

---



## 历史停点：正式双包签名与原生自检已通过（UTC 2026-10-05T02:27:29.577861+00:00）

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



## 历史记录：2026-10-05 窗口合同与累计复跑补充（已收齐234/233）

扩大mypy228为1错误：卸载窗回执字段推断None。Qt接口229红1 failed；230只修CoreRemovalEvidence可空注解与scroll_area字段，原生QWidget.scroll仍可调用，真实清理回执身份与结果一致。相关186 passed/53.93s，56 pet/scripts源mypy及231整个AI host26源mypy通过；2文件Ruff/format通过。full234已通过4258 passed /14 skipped /14 warnings /861.35s，源码哈希无变化；满CPU233三遍各227 passed，109.71/122.16/128.58s，CPU median/p95均100%。214/223保留为前版本历史。窗口布局、数据保留和不可逆卸载合同未改。

实际效果：卸载确认界面的公开Qt方法不再被控件字段遮蔽；无新删除或用户数据操作，仍未完整交付Phase5A/5B-1。


## 2026-10-05 空间与新白名单提案241（未执行）

- 最新综合质量239/240已收齐：Ruff pet/features/scripts/tests通过（0.249s），151文件format通过（0.079s）；配置mypy26（4.702s）、受影响56（2.649s）、AI host26（0.785s）通过；文档129（61.411s）、构建/信任/注册/报告165 passed（5.94s）通过；tracked diff-check退出0。239首次ruff/format因隔离USERPROFILE使Python user-site模块不可见而失败；240使用已安装绝对路径原生ruff0.16.6通过，不重装依赖、不注入PYTHONPATH、不把239退出1涂成0。
- 241只读新清理提案共8项存在的旧生成物/1,633,900,888B，无删除命令/实际删除。不是重试213被拒绝的core-01目标；保留此前拒绝及所有保护项。必须独立核对拥有证据/占用，明确目标和影响获确认且工具允许后才可处理。空间门和最终Core03仍未解除，不因提出清理方案而计通过。

下次先完成安全可执行条件及占用/拥有核对，实际释放后重新过2GiB预算门。没有清理真实数据、旧人工目录或此前被拒绝的core-01，没有命令绕过。最终交付仍未完成。

## 2026-10-05 追加文本复验与准确停点（243～248，UTC 2026-10-05T05:22:53.605378+00:00）

## 历史停点：文本复验完成，当时等待限定空间清理确认（UTC 2026-10-05T05:22:53.605378+00:00）

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

## 历史证据与当时用户下一步（UTC 2026-10-05T05:31:29.892754+00:00）

- 最后文档复验250（UTC 2026-10-05T05:26:20.978759+00:00）：129文件链接通过67.059s，报告101 passed /0.70s；tracked diff-check退出0，所有新增文本空白问题0，Python哈希仍与full234一致。249已按实际四列表刷新172文件证据；248统计解析失败保留，不重复追加记录。
- 更晚空间251：本轮拥有根5,909,529,950B，余532,920,994B，29 reparse剪枝未跟随；尚不足2GiB。仅假设241八项全部安全释放才有2,166,821,882B，比预留多19,338,234B（约18.44MiB）；不是已清理结果，后续每次复制/构建前再测。252对枚举出的真实packages/两DLC ZIP复核SHA256与200一致，未修改签名材料。251第一次按错误假设名称查找为not_found，不当成校验通过；252以实际目录/名称修正。

### 实际使用效果与限制

本轮源码门及文本复验通过，但正式最终交付尚未完成；只提出八项旧生成物清理，未实际执行，不以空间投影冒报已释放。用户当前无需再次输入签名密码，若要继续构建须先确认同组PLAN明确范围。真实安装/导入/备份另行展示目标与影响，不强退测试Core或任何合法进程。

## 2026-10-06 夜间修复与暂停交接（历史检查点）

用户最新要求先总结交接，未开始任务暂停。本节更新当前状态，不覆盖上述2026-10-04/05历史证据。基线/分支仍为 `e2687be` / `codex/phase3-worker`；原WIP保留，无暂存、提交、推送、发布或子智能体。Phase5A/5B-1 **尚未完整验收**。

### 本轮修改文件说明

以下增删为当前相对 HEAD 的 `git diff --numstat`，未跟踪文件用总行数 +N/−0；包含此前Phase5A WIP，不冒称所有新增都发生于今晚。基线快照可证明的夜间增量：local intents +39/−20、release builder +1/−0、probe entry +4/−0、intents测试+46/−0、probe build测试+39/−0；其余原有 tracked 文件不在该白名单快照中，只报告 Git 增量。没有删除源码文件。

| 文件 | 相对 HEAD 增删 | 修改与原因 |
|---|---|---|
| `pet/local_package_intents.py` | +139 / −0 | 抽出真实 QDialog 公开 seam，并给双包确认卡增加可见 owner 标题；避免用户只看到同名操作而不知所属包。 |
| `pet/frozen_runtime_paths.py` | +37 / −0 | 新增仅 Windows frozen/同一 exe 的 _internal 原生路径别名；保留 PYZ 优先，拒绝 UNC/逃逸/不同目录，不添加源码路径。 |
| `pet/workers/screen_entry.py` | +3 / −0 | 在探针或正常租约分派前统一处理自有冻结依赖目录；不跳过租约接管。 |
| `scripts/build_feature_probe_native.py` | +117 / −0 | 固定上游 seam，显式扩展本地归档/PYZ路径并只给密封 Python3.11 设置三条已有路径；避免 LPAC 下 MAX_PATH/getpath 边界失败。 |
| `scripts/build_feature_release.py` | +656 / −0 | 把冻结路径工具纳入受摘要保护的生产 helper 源快照，避免依赖仓库。 |
| `scripts/build_screen_worker.py` | +1 / −0 | 将同一冻结路径工具纳入独立 Worker 构建源集合。 |
| `scripts/feature_probe_entry.py` | +21 / −1 | host 自检启动即激活同一密封依赖目录，仍先核实 sandbox 并重新完整验证包。 |
| `scripts/validate_phase5a_delivery.py` | +370 / −0 | 新增自有PID/创建时间/exe绑定的真实UIA管理与正常Core验收；原生菜单退出，无强退、状态改写、factory旁路或桌面截图。 |
| `tests/test_local_package_intents.py` | +145 / −0 | 增加实际 QDialog owner 可见性及720/1100、字体/长文案布局测试，先红后绿。 |
| `tests/test_frozen_runtime_paths.py` | +61 / −0 | 新增只允许同一自有 local onedir 的长/扩展路径、幂等及非法布局失败回归。 |
| `tests/test_feature_probe_build.py` | +120 / −0 | 增加固定上游归档/home/显式初始化路径 seam、Python版本/有界缓冲检查及错位失败关闭测试。 |
| `tests/test_phase5a_delivery_acceptance.py` | +77 / −0 | 新增环境封闭、PID身份、状态和菜单组合、路径及坐标边界等9项验收驱动合同。 |

文档继续使用同一设计与五份任务记录，不另建第二套Phase5A权威记录。以下同样按HEAD统计，含本轮前原WIP；未跟踪/被忽略的文档以整文件新增行数列示，不代表本夜新写全部内容。五份记录已在磁盘保存，但当前未暂存、提交；本报告不回填自身提交SHA。

<!-- NIGHT_HANDOFF_DOC_NUMSTAT_START -->
| 文档 | 相对HEAD增 / 删 | 内容与原因 |
|---|---:|---|
| `README.md` | +2 / −0 | 更新当前暂停状态与唯一证据入口，避免把旧产物误当最新版。 |
| `LOG.md` | +104 / −0 | 记录本夜修复、最新全量、限制及无发布事实。 |
| `LOG-INDEX.md` | +6 / −0 | 增加10月6日活动入口，保留历史日期。 |
| `docs/INDEX.md` | +4 / −1 | 更新既有报告条目，指明暂停与未验收门。 |
| `docs/PROJECT-ENTRY.md` | +5 / −1 | 新对话入口同步暂停，旧状态标为历史。 |
| `docs/plugin-phase-05-distribution/README.md` | +4 / −0 | 阶段入口同步最新事实和待验门，保留历史。 |
| `docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md` | +214 / −0 | 补权限、长路径实现证据及暂停边界，不改变原产品合同。 |
| `.scratch/phase5a-local-distribution/PLAN.md` | +434 / −0 | 标明已收齐全量与本次不执行的验收项。 |
| `.scratch/phase5a-local-distribution/HANDOFF.md` | +382 / −0 | 保存精确停点、风险、证据材料、下一次编辑位置与保护边界。 |
| `.scratch/phase5a-local-distribution/STATUS.md` | +379 / −0 | 区分构建、自动化、实机、用户与正式分发状态。 |
| `.scratch/phase5a-local-distribution/WORKLOG.md` | +486 / −0 | 保留红绿及失败调查，记录暂停后的既有结果收齐。 |
| `.scratch/phase5a-local-distribution/SUMMARY.md` | +378 / −0 | 压缩跨对话有效事实、用户偏好、剩余门与不自动续作。 |
| `docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-04.md` | +693 / −0 | 追加逐文件、实测性能、实机与风险交接，不另建报告。 |
<!-- NIGHT_HANDOFF_DOC_NUMSTAT_END -->

### 实机运行记录

环境：Windows x64 build26100，CPython3.11.1，PyInstaller6.20.0，生成数据根与真实用户数据隔离。签名自检候选为一次性内存 Ed25519 验证专用钥，私钥立即丢弃；**不是正式签名包**。正式私钥未读取或解锁。

1. `python -m pytest -q tests/test_local_package_intents.py tests/test_phase5a_delivery_acceptance.py` 等公开seam：确认owner标题红→绿；相关23通过、驱动9通过。仅AI Core03实际菜单/真实receipt/自然退出0通过；无参数正常Core完成pending确认，不靠普通设置替代。
2. 显式运行路径相关回归：新显式路径红4 failed /25 deselected /2.94s → 相关绿59 passed /3.96s，实际输出保存在 `acceptance-night-20261006/explicit-runtime-green.log`。2026-10-06推送前审查发现先前手工记录的命令包含不存在的 `tests/test_build_screen_worker.py`，已撤回该命令，不能拿它复现59项结果；原始输出没有完整argv，不补猜测。已知相关测试包括 `test_frozen_runtime_paths.py`、`test_feature_probe_build.py`、`test_feature_probe_adapter.py`，本轮新全量重新覆盖。旧失败均保留，不能把此前host成功当Worker成功。
3. `runpy.run_path('.../probe08-deep-check.py', run_name='__main__')`：原生父端启动真正LPAC，host/Worker在长、短路径各一次 exit0，Worker完成HELLO→SHUTDOWN→优雅退出，appcontainer/零capability/LPAC/Win32k/Job五项父端核验均true。保留拥有日志；无安全限制降级。
4. `python -m scripts.validate_feature_probe_windows <probe08bundle> <owned-output>`：完整权限与故障canary状态passed。授权材料只读，scratch可写；生成凭据/DPAPI/注册表/用户文件/ALL_APPLICATION_PACKAGES/桌面/子进程/敏感句柄等越界均拒绝；超时/输入阻塞/输出/内存/崩溃/父端退出、profile恢复幂等通过。IPv4/IPv6负对照Winsock初始化10107，正对照连接成功；未读取真实秘密或截图真实桌面。
5. 生产 builder 生成 native05、probe08、Worker04、Core04并审计。helper摘要 `2e039a80ae474aaa3b65ab08264d8c9d362f3ee3da1be8d12c75e26ba5a0f492`；Core04无AI/识屏实现与源码回退，正式信任策略不包含临时验证公钥。没有执行Core04完整冻结矩阵，未重签DLC或生成新版Setup/ZIP总清单。
6. 最新 full-01：4294 passed /15 skipped /14 warnings /796.84s，exit0，早于最终路径补丁。full-02 已收齐：**4298 passed /15 skipped /14 warnings /924.58s，exit0**；命令 `python -X utf8 -m pytest -q`（生成的 basetemp/cache 均在本轮拥有根），证据为 `acceptance-night-20261006/full-02.log` 与 `.exit`。已覆盖最终原生路径修复；运行启动后仅给验收驱动补了类型注解，该文件另经 mypy/9项回归通过，未重新运行整个受影响60文件 mypy。
7. quality-02：Ruff、162文件format、configured mypy26、AIhost mypy26、129文档、167报告构建、diff-check通过；affected mypy60初红7个错误均在验收驱动，显式dict/list注解修正后该文件mypy和9测试通过。整体60文件没有复跑，原总体失败保留。新源码高负载三遍未运行，历史3×251不充当本次通过。

所有 `...` 自有证据相对本轮拥有根 `E:/AI/DSH/dsh-pet-indesktop/.scratch/phase5a-local-distribution/acceptance-night-20261006`；可重现代码入口在仓库 scripts/tests，生成日志不入库。旧签名根 `delivery-set-293` 和既有个人profile未动。

### 性能分析

命令为上述真实UIA启动、真实LPAC driver与生产builder；不是mock/CI。小样本只记录实测，不满足最终统计门。

| 路径与样本 | 实测 | 解释 |
|---|---|---|
| Core03仅AI正常启动 n=1 | 4071.0195ms；RSS228,929,536B；30线程 | 边界为可见桌宠与选包pending清除；不是Core04性能或10次启动门 |
| LPAC长路径整次 n=1 | 33.0284254s；host3.5205619s，Worker1.9318360s | 整次含复制/摘要/ACL/profile/清理，不只进程执行 |
| LPAC短路径整次 n=1 | 26.7028530s；host3.4044217s，Worker1.9374264s | 共2个样本，不计算有统计意义的median/p95 |
| native05构建 n=1 | 21.7530973s | 固定上游源码与编译器，不在运行热路径 |
| helper08 / Worker04构建 n=1 | 40.1720964 /52.2213706s | 含构建服务校验；实际PyInstaller为18.96/30.535s |
| Core04构建 n=1 | 226.5833133s；实际PyInstaller171.67s | 单次构建，不代表用户启动速度 |
| Core04/helper08/Worker04解压大小 | 455,783,226 /17,270,083 /66,341,592B | 1,424/63/97文件；2,260/177/460 PYZ模块 |
| Phase5A保留生成物只读抽样 | 10,415,149,951B（9.699864GiB） | no-follow/43链接剪枝；本夜1.904173GiB；不是持续监测峰值，不是清理结果 |

稳态变化没有足够新旧对照，不声称“可忽略”或提速。可见owner标题增加2个QLabel；自检入口增加一次同一sealed native目录别名，原生引导新增 `Py_SetPath` 和DLL符号查找只在新子进程启动触发，不新增网络、常驻线程或轮询。安装自检仍有已有哈希/复制/ACL/profile/Job/文件IO；2个探针进程每个只启动一次，无额外子进程。RSS只有上述Core03样本；probe RSS/系统调用计数未采集，不用累计WinIO冒充物理磁盘成本。最终 n>=10启动/自检/状态操作、n>=20预检门及median/p95仍待执行。

### 验证结果、未执行项与风险

- 正常Worker04租约接管及业务启动未实测；probe握手不能替代该门。
- 显式 `Py_SetPath` 的分隔符特殊路径未覆盖；CPython3.11按分号拆分路径，后续必须补拒绝/封闭三路径回归，不能将未测路径写为安全通过。本次暂停后不继续加补丁；新版材料不是最终发布候选。
- Core04四组合、正常业务请求、升级回滚/卸载重装、最新高负载与完整性能未完成。
- 新正式 screen DLC需本地解锁重签；新版Core ZIP/便携/Setup及总分发签名未产生。原delivery-set-293签名没有失效，也不含本轮修复，不能作为最新修复交付。
- Setup只有旧编译/静态证据；真实安装/更新/Core卸载、用户真实模型、显式旧数据导入、密钥备份恢复、新用户/机器、Authenticode/SmartScreen/Inno许可及其他平台门未执行。
- 所有真实安装/数据/秘密目标仍需单独明确确认；此刻不要求用户输入密码。原213/254拒绝不绕过，旧Core/真实数据/密钥不清理。

交接封存文档验证（2026-10-06）：仅复核13份本次交接文档、322个本地文件链接及LOG索引锚点，全部通过（0.0842s）；报告纪律57 passed /0.67s；git diff --check exit0、暂存为空。不是新增Phase5A运行验收，也不覆盖此前受影响mypy60未复跑的事实。证据 `acceptance-night-20261006/handoff-doc-check.json`、`handoff-report-check.log`。

### 回滚与准确下一步

现在按用户要求暂停。恢复时先读同组HANDOFF/STATUS/PLAN，补正常Worker与特殊路径边界，再复跑质量/高负载和真实冻结矩阵，最后集中准备需要用户的签名/安装/模型/干净环境门。当前未获Git授权，不使用reset --hard、覆盖用户改动或强推；先比较明确文件的HEAD diff和白名单快照，仅撤回可证明本轮新增，不能整目录还原（快照不覆盖所有原有tracked文件）。

### 当前实际可体验的效果与限制

仅AI正常启动及自然退出、可辨识的双包确认标题、长路径冻结自检已得到本机证据；新修复还未打成新版正式交付包。Phase5A/5B-1仍未完整验收，原桌宠和个人数据保持不变。未开始的任务先不做，用户暂时无需人工操作；后续继续时再一次性提供具体目标与验收步骤。


## 2026-10-06 授权检查点推送与继续实施（当前状态）

本次用户授权先提交并推送已有本地内容，再继续剩余工作；不是正式发布，也不自动授权后续新源码推送。本地HEAD与本轮独立ls-remote均为 `e2687be0866db2915af13447fb49bb278ffdf202`；明确白名单183文件（178源/测试/文档、5份任务Markdown）及1,058,859B封存快照在本轮拥有的 `publication-20261006`。生成物、日志、个人数据与私钥不入库；唯一敏感模式命中是加密PKCS#8格式的字节常量判断，非私钥内容，未读取仓库外私钥。

### 本次验证与出版状态

- 新Ruff通过，0.4166993s；162文件format-check通过，0.0900946s。
- 配置mypy26 /4.1807584s、受影响mypy60 /4.7542317s、AI host mypy26 /1.6852577s全部通过。此前60文件未复跑的证据缺口已补齐。
- 链接检查剪枝后读取129份Markdown，broken=0；检查器1.182s /整次1.3517015s。报告与构建相关167 passed /6.59s /整次7.6211734s；`git diff --check`通过。
- 新全量：**4298 passed, 15 skipped, 15 warnings in 929.75s (0:15:29)**，整次931.253s，exit0，源SHA不变。命令见 `publication-20261006/full/result.json`；隔离环境、basetemp/cache均为本次拥有根。
- 21个受影响Qt/进程族真实CPU高负载连续三遍：第1遍 361 passed, 1 skipped, 1 warning in 332.06s (0:05:32)，wall 336.500s /CPU median 100.0% /p95 100.0%；第2遍 361 passed, 1 skipped, 1 warning in 260.70s (0:04:20)，wall 264.844s /CPU median 100.0% /p95 100.0%；第3遍 361 passed, 1 skipped, 1 warning in 259.37s (0:04:19)，wall 263.437s /CPU median 100.0% /p95 100.0%。环境Windows26100、Python3.11.1、20 logical CPU；每遍20个自有BELOW_NORMAL负载进程，源码冻结一致，全部自有负载回收。命令/样本在 `publication-20261006/highload/result.json`，不更改系统配置、不作用于合法Core进程。
- 183文件已逐项暂存并确认0二进制/无生成物、秘密和个人数据；暂未创建提交/推送。最终文档门后提交并独立核对ls-remote/fetch，不能用本地tracking ref替代远端证据。

### 续接顺序、实际效果与限制

先完成上述推送门并保存远端检查点，然后补 `Py_SetPath` 的分号路径失败关闭与正常冻结Worker租约/握手。随后推进最新版冻结Core矩阵、性能及分发材料。新版正式重签、真实安装/导入、模型体验与干净环境门仍留用户最后确认；旧正式产物未包含夜间修复，不能当新最终包。12GiB上限、旧拒绝清理目标、用户数据与既有验收目录保护不变。

## 2026-10-06 续接实施：推送检查点后的自动验收

### 修改文件说明

本次推送前检查点已经以 `70ff464f84793f6ea3a342079dcbfb2d991fcf4e` 推送到 `codex/phase3-worker`。推送后只在本地继续修改：

| 文件 | 改动 | 原因 | 当前状态 |
|---|---|---|---|
| `scripts/build_feature_probe_native.py` | +3 行 | 在显式 `Py_SetPath` 路径拼接前拒绝包含分号的拥有路径，避免 CPython 分隔符把输入拆成未授权路径。 | 本地未推送 |
| `tests/test_feature_probe_build.py` | +1 行 | 固定失败关闭合同，防止未来移除分号拒绝。 | 本地未推送 |
| `.scratch/phase5a-local-distribution/` 同组记录与本轮证据 | 追加当前结果 | 保留远端检查点、Worker真实入口、全量回归和未完成门的准确停点。 | 忽略目录，不入 Git |

推送检查点的 183 文件逐文件说明仍以本报告上一节及提交 `70ff464` 为准；本次新增源码 diff 当前为 `3/0` 与 `1/0`（新增/删除行）。

### 性能与验证数字

- `python -m pytest -q` 受影响专项：`267 passed, 2 skipped in 37.29s`。
- 显式 `PYTHONWARNINGS=default` 的全量 `python -X utf8 -m pytest -q`：`4298 passed, 15 skipped, 146 warnings in 712.39s`，exit `0`。146 条警告包含既有资源回收警告，不能与普通默认警告统计直接比较。
- 分号合同聚焦测试：修复前 `20 passed, 1 failed`；修复后 `20 passed in 0.72s`。
- 冻结 Worker04 真实子进程：`HELLO lease_claimed=true`、正常 `SHUTDOWN` exit `0`、无 token exit `77` 且 stdout 为空；父 reservation 关闭后 occupancy `free`、剩余 lease `0`。这是一次端到端验收，不是统计样本，不能代替 n>=10 性能门。
- 本地代码门：Ruff、两份修改文件 format、`scripts/build_feature_probe_native.py` mypy、24 项聚焦测试均通过。

### 实机运行记录

- 真实 Windows 冻结程序：`...\acceptance-night-20261006\builds\worker-04\dist\proactive-screen-worker\proactive-screen-worker.exe`。
- 真实运行环境：Windows x64，Worker 通过环境交接 token 与本地自有测试账本建立 lease；未读取真实凭据、截图、模型或用户 profile。
- 证据：`.scratch/phase5a-local-distribution/acceptance-night-20261006/normal-worker-acceptance-v3-result.json` 与 `postpush-20261006-results.json`。
- 推送证据：本地 HEAD、远端 `ls-remote` 和 fetched remote ref 均为 `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`，左右差异 `0 0`。

### 未通过、未执行与风险

- 新分号修复尚未重新编译到 Worker04/Core04，因此既有 Worker04 证据不能证明新 native 产物已含该修复。
- Core04 双包 `both` UI 矩阵历史运行因 `management_confirmation_timeout` 失败；没有绕过管理确认，也没有把 pending 状态改成成功。
- 正式私钥解锁、正式 DLC 重签、Setup/普通 ZIP/便携 ZIP重新生成、真实安装/更新/卸载、显式数据导入、干净环境、真实模型/截图及新账户体验仍待人工门。
- 当前生成量约 `9.728 GiB`，按 `2.5 GiB` 构建预留会超过当前 `12 GiB` 峰值合同；没有擅自清理旧验收构建或扩大范围。

### 准确下一步

先在本地记录修改后复跑文档链接、报告纪律和 `git diff --check`。若继续构建，应先明确新版产物预算并只使用本轮拥有根；随后重新构建、重做双包矩阵，再由用户执行私钥、安装、便携、迁移、模型和干净环境人工门。

## 2026-10-06 键盘语义菜单修复与最新全量质量门

### 修改文件说明

本节只记录上次推送检查点之后的本地 WIP 增量；不代表已提交或已推送：

| 文件 | `git diff --numstat`（新增/删除） | 改动与原因 |
|---|---:|---|
| `pet/window.py` | `11/0` | 处理 `QContextMenuEvent.Reason.Keyboard`，使用稳定身体锚点，解决 `WS_EX_NOACTIVATE` 冻结窗口收到 `WM_CONTEXTMENU(-1)` 后菜单不出现。 |
| `scripts/validate_phase5a_delivery.py` | `36/13` | 自有 UI 驱动改为身份核验后发送键盘语义上下文菜单，不激活窗口、不抢用户焦点；保留自有 UIA 和自然退出边界。 |
| `tests/test_phase5a_delivery_acceptance.py` | `64/14` | 固定无激活消息路由和产品稳定锚点回归，删除不再适用的屏幕坐标/激活假设。 |
| `pet/feature_management.py`、`packaging/phase4b_manual_entry.py`、构建脚本及其测试 | 其余见本报告前一节及当前 `git diff --numstat` | 保留此前 Phase 5A WIP，不在本节重复冒充为新推送内容；格式化只作用于本轮已修改文件。 |
| `.scratch/phase5a-local-distribution/*` | 记录追加 | 记录失败复盘、实机证据、当前停点和未执行门；不作为源码发布物。 |

### 性能与验证数字

- `core-11b` no-chat 冻结 Core 构建产物：`1,639,548,973` 字节；本次不是正式发布压缩包。
- screen v13 真实启动：`3763.6489 ms`、RSS `191,500,288` 字节、`34` 线程、读取 `847,910,997` 字节、写入 `22,018` 字节；自然退出 `0`，production Core 实际清除 pending。
- empty v14 真实启动：`2830.1063 ms`、RSS `203,771,904` 字节、`33` 线程、读取 `356,252,676` 字节、写入 `21,447` 字节；自然退出 `0`。
- 受影响专项 `66 passed / 10.64s`；Ruff、12 文件 format-check、`git diff --check` 均通过。
- 最新全量：`4300 passed, 15 skipped, 14 warnings in 671.83s (0:11:11)`，`QT_QPA_PLATFORM=offscreen`，测试源码不变，exit `0`。警告为既有 Qt/zip 夹具/弃用告警，没有把 warning 改写成 failure。

### 实机运行记录

- Windows x64 build `26100`，自有 acceptance roots：`frozen-screen-compact-20261006-v13` 与 `frozen-empty-compact-20261006-v14`。
- 两个验收均通过真实冻结 `dsh-pet-core-webm.exe`；screen 菜单实际包含 `看看屏幕`、`主动识屏`、`退出`，empty 菜单无识屏入口。
- v13/v14 后扫描无本轮拥有 `dsh-pet-core-webm.exe` 进程；此前 core-10 的两个遗留验收进程在路径核验后自然停止。未关闭合法 Core/设置进程，未读取真实凭据、截图或用户文件。

### 未通过、未执行与风险

- v11 的 `owned_menu_or_load_timeout` 与 v12 的 `owned_foreground_activation_refused` 保留为失败事实；它们用于证明旧验收假设不成立，不计为当前通过。
- 非 offscreen 的相关拖拽探索曾有 `190 passed / 6 failed / 4 warnings`，为显示边界夹紧断言问题；offscreen 最新全量已通过，本次没有修改该无关行为。
- 仍未完成正式私钥解锁、正式总分发签名、Setup/普通 ZIP/便携真实安装与更新卸载、旧数据导入、干净环境、真实模型/截图和人工体验；不能宣布 Phase 5A 正式分发完成。
- 当前全部新增修改保持本地未提交、未推送；`.scratch` 中的证据不是发布物。

### 准确下一步

自动门已收齐后，下一步应先由用户明确是否进入正式分发/人工门；若继续，先重新核对空间预算与待构建产物，正式私钥只能由用户在本地可信 masked UI 输入，不通过聊天、日志、命令行或环境变量传递。

### 质量门补充与未通过项

- `ruff check pet tests scripts` 通过；`ruff format --check pet tests scripts` 报告 `544 files already formatted`；`scripts/check_docs.py` 扫描 129 个 Markdown 文件通过；`tests/test_pr_report_discipline.py` 为 `57 passed`；`git diff --check` 通过。
- `python -m mypy pet` 仍失败：`439 errors in 46 files`。定向本轮模块检查只在 `pet/window.py` 既有位置报告 19 个错误，`feature_management.py` 与验收驱动无诊断，新增上下文菜单行无诊断。该既有类型债务未被本轮修改修复，也不应被省略。


## 2026-10-06 夜间自动实施补充：生产 screen probe 根因修复与最终冻结 Core 验收

### 修改文件说明

本次实际代码修复集中在 `pet/feature_management.py` 与 `tests/test_feature_management.py`：前者在 Windows 深路径验收场景通过 `SHGetKnownFolderPath(FOLDERID_LocalAppData)` 选择短的、按 data-root identity 隔离的 probe 运行根，后者增加 legacy PyInstaller worker 路径回归。此前同一 WIP 中的 `pet/window.py`、`scripts/validate_phase5a_delivery.py` 及其测试仍保留上下文菜单键盘语义修复，未被本次诊断 trace 污染；临时 trace 已完全恢复。具体行数以当前 `git diff --numstat` 为准，未创建提交。

### 根因、影响与修复验证

诊断冻结 Core `core-diag-screen-20261006-b` 的 screen v23 真实失败为 PyInstaller 无法加载 embedded PKG，stderr 指向深层验收根下的 `host/helper/dsh-feature-probe.exe`。临时 trace 显示管理动作、信号派发和交付均完成，但返回 `host_probe_failed`；因此没有误改 Qt 线程或管理事务，也确认 factory/Worker 没有在失败验证前运行。修复后使用系统 LocalAppData 短根，深路径屏障恢复。

### 性能与本机真实记录

- Core `core-05-final-20261006` 构建：`127.475s`；dist `455,783,648B`；EXE SHA256 `64C5686717906E1112D1AAE9BC1AEAD5CEBC509F5F8EB6CCD4D9842336201382`。
- AI 冻结 Core：启动 `3750.3321ms`，RSS `198,176,768B`，37 threads，exit 0；IO read/write `5690/300`，read/write bytes `350,049,858/23,744`，菜单含 `AI 对话`，状态 pending `null`。
- screen 冻结 Core：启动 `4368.526ms`，RSS `183,676,928B`，32 threads，exit 0；IO read/write `9716/325`，read/write bytes `892,156,616/36,362`，菜单含 `主动识屏`，状态 pending `null`。
- 两个验收均在 Windows build `26100`、Python `3.11.1` 的真实冻结程序上完成，真实 LPAC helper 完成 host（screen 另含 Worker）最小握手，生产加载 receipt 清除 pending，随后自然退出；结束后无活动 dsh Core/probe 进程。
- 全量 `python -m pytest -q`：`4301 passed, 15 skipped, 14 warnings in 745.31s (0:12:25)`，exit 0；`ruff check .` 通过；12 个受影响 Python 文件 format-check 通过；目标模块 mypy 通过；文档链接 `129 files scanned` 通过；`git diff --check` 通过。
- 仓库全量 `ruff format --check .` 仍报告 7 个历史 Markdown 文件未格式化；这些文件不在本次修改范围，未为满足门禁而改动历史文档。项目级 `mypy pet` 的既有 `439 errors / 46 files` 仍单列为历史债务。

### 交付与限制

这轮完成的是本机真实冻结 Core 的自动化/工程验证，不是正式发布。仍未执行真实 Setup 安装更新卸载、NTFS 便携移动、显式旧数据导入、干净 Windows/新用户/另一台机器、真实模型/截图/文件理解人工验收、Windows Authenticode/SmartScreen 或正式发布清单。未读取 `E:\\AI\\DSH\\release-signing\\feature-release-ed25519.pem`，未提交或推送本轮新增修改；`.scratch/phase5a-local-distribution` 中的验收产物按本轮拥有证据保留，未做广泛清理。

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


## 2026-10-06 追加：屏幕独立配置提示修正

### 修改文件说明

本次新增运行代码改动为 `features/screen_understanding/host/manual.py` 与 `features/screen_understanding/host/settings.py`；新增回归覆盖为 `tests/test_screen_manual_host.py` 与 `tests/test_screen_settings.py`。同时更新 Phase 5A 同组设计、计划、状态、工作日志、交接和摘要；v4 人工验收脚本与脱敏 fixture 位于 `.scratch/phase5a-local-distribution/manual-user-acceptance-20261006-v4-screen-ux/`。具体原因是区分“迁移/配置存在”和“屏幕凭据缺失”，避免把用户引导回已经完成的迁移。

本次源码/测试增量的逐文件行数以 `git diff --numstat --` 为准；现有 Phase 5A WIP 仍在工作树中，未用本次增量覆盖或清理。生成的 Core/Worker、ZIP 和日志不纳入提交。

### 性能分析

- Screen 相关专项测试：53 项，16.48s，Windows 本机，Python 3.11.1。
- no-chat human-acceptance Core：106.968918s 构建，459,275,741 bytes。
- chat human-acceptance Core：80.814419s 构建，461,518,673 bytes。
- Worker human-acceptance 构建：19.271s，66,619,041 bytes。
- 本次代码只改变状态文案和错误原因映射，不新增网络、凭据读取、线程、Worker 或 GUI 外部进程；真实屏幕请求仍由用户人工触发，尚未测量其网络/模型成本。

### 实机运行记录或报告

新包校验确认 ZIP 内含修正后的“补填视觉 API Key”“无需重复迁移”文案；PowerShell 安装脚本和 JSON fixture 解析通过。旧 v2 人工根中已确认迁移状态和独立配置事实，但本次重建后的 v4 Core 还没有替用户输入真实 screen Key 或发起真实 screen request。

全量回归最新结果为 `4298 passed, 14 skipped, 14 warnings, 6 failed`；6 个失败在 `tests/test_drag_move_coalescing.py` 单独复现，原因是窗口实际边界夹紧与测试目标坐标不一致。该无关测试族未被本次修复修改，日志保留在 `.scratch/phase5a-local-distribution/pytest-full-screen-ux-20261006.log` 与 `.scratch/phase5a-local-distribution/pytest-drag-recheck-20261006.log`。因此本追加不宣称全量质量门通过。

### 当前限制与下一步

用户必须在 v4 隔离 Core 的设置界面中通过掩码输入框录入 screen API Key，再完成一次真实识屏；凭据不经聊天、命令行或日志传递。该人工门通过后，仍与正式信任锚、Setup/便携/干净环境等 Phase 5A 门分开记录。


## 2026-10-06 最终复核：屏幕 UX 验收包证据

- 使用 Python `zipfile` 直接读取 4 个 screen 包 ZIP，均确认包含修正后的 `host/manual.py` 与 `host/settings.py`，并确认包内含“补填视觉 API Key”“无需重复迁移”文案；未依赖未安装的 `7z` 命令。
- `screen-config-fixture.json` 的原始字节无 UTF-8 BOM，JSON 合同断言通过；不会因 PowerShell 编码导致产品配置读取失败。
- v4 安装脚本 PowerShell AST 解析通过，fixture 只在配置不存在时复制；不会覆盖用户后来在隔离 UI 中保存的凭据引用。
- 受影响源码/测试范围的 `git diff --check` 通过。整个 WIP 工作树仍有历史任务记录中的尾随空白告警，未进行大范围格式化，避免改写既有证据。
- 尚未代替用户输入真实 screen Key 或发起真实 screen 请求；这仍是唯一需要人工执行的本次修复验收门。


## 2026-10-06 最终收尾：按用户需求重写 Phase5A 本地激活

### 结论

本次确认原实现把“Setup 选装”和“用户手动放置本地包”错误地合并成了发布者签名分发模型，属于过度设计。现已按用户口径重写为两条清晰路径：

1. **Setup 路径**：安装向导提供 `ai` / `screen` 可选任务；任务只传递安装意图，旁置包由本地文件提供，不负责下载、发布者认证或伪造成功回执。
2. **非 Setup 路径**：用户自行下载普通 ZIP，或准备功能包目录，在扩展管理页显式选择后导入/激活。当前不要求发布者公钥、私钥、`manifest.sig` 或签名验证。v2 manifest 的 `key_id`（若存在）只是兼容 schema 的非密码学标识，不参与信任判断。

取消发布者认证不等于取消安全边界。Core 继续强制固定 feature/factory 白名单、版本/API/能力兼容性、安全路径、文件数量/大小限制、manifest 文件清单 SHA-256、事务账本、版本租约及 Windows LPAC/Worker probe。

### 修改文件说明与 `git diff --numstat`

下面是本轮收尾时工作树的逐文件快照；`tracked` 的数字来自 `git diff --numstat`，`untracked` 是新增测试文件的实际行数。`docs/PR-REPORT...` 自身的最终 `git diff --numstat` 已在本节写入后回填为 `307/1`；本报告随后追加了 2026-10-07 Setup 编译证据，表格数字已同步。

| 文件 | 状态 | 新增 | 删除 | 改动与原因 |
|---|---|---:|---:|---|
| `.scratch/phase5a-local-distribution/HANDOFF.md` | tracked | 219 | 0 | 最终停点、保留证据、清理结果和用户剩余人工门。 |
| `.scratch/phase5a-local-distribution/PLAN.md` | tracked | 142 | 0 | 将产品口径重置后的计划项闭合，记录实现、验证和未完成门。 |
| `.scratch/phase5a-local-distribution/STATUS.md` | tracked | 192 | 0 | 更新各验收维度的 planned/implemented/automated/real-machine/user 状态。 |
| `.scratch/phase5a-local-distribution/SUMMARY.md` | tracked | 180 | 0 | 保存跨对话最终摘要、最终产物和下一步边界。 |
| `.scratch/phase5a-local-distribution/WORKLOG.md` | tracked | 224 | 0 | 记录 probe 根因、真实验收、质量门和安全清理过程。 |
| `docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-04.md` | tracked | 307 | 1 | 追加架构纠偏、逐文件 numstat、性能、实机证据、清理、限制和 2026-10-07 Setup 编译证据。 |
| `docs/PROJECT-ENTRY.md` | tracked | 5 | 1 | 把项目入口的当前合同和状态改为 Setup 选装/本地显式导入。 |
| `docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md` | tracked | 100 | 9 | 修订设计合同，区分非密码学 schema 字段与发布者签名。 |
| `docs/plugin-phase-05-distribution/README.md` | tracked | 5 | 3 | 同步 Phase5A README 的当前有效口径和状态。 |
| `features/screen_understanding/common/models.py` | tracked | 30 | 0 | 为无有效配置的 screen 首装提供无凭据默认模型。 |
| `features/screen_understanding/host/manual.py` | tracked | 6 | 1 | 把缺凭据提示改为用户自行配置，不再暗示自动迁移。 |
| `features/screen_understanding/host/migration.py` | tracked | 40 | 0 | 保留显式兼容迁移 API，并覆盖安全边界。 |
| `features/screen_understanding/host/settings.py` | tracked | 21 | 59 | 移除首装自动迁移/补齐 UI，使用独立默认配置。 |
| `packaging/core_webm.iss` | tracked | 2 | 2 | 把 Setup 任务说明改为旁置普通本地包的可选安装意图。 |
| `packaging/phase4a_validation_entry.py` | tracked | 7 | 7 | 让 validation-only 本地路径不依赖发布者公钥。 |
| `packaging/phase4b_manual_entry.py` | tracked | 7 | 0 | 同步手工入口的本地激活说明和兼容参数。 |
| `pet/feature_build_policy.py` | tracked | 6 | 3 | 将当前构建策略设为显式本地激活、无默认签名锚。 |
| `pet/feature_host_bindings.py` | tracked | 60 | 13 | 贯通本地包的 host 绑定和加载信任状态。 |
| `pet/feature_install_state.py` | tracked | 5 | 1 | 使用统一 descriptor acceptance seam，避免硬编码 official trust。 |
| `pet/feature_management.py` | tracked | 81 | 3 | 启用本地 verifier，并修复深路径 probe 运行根。 |
| `pet/feature_management_ui.py` | tracked | 6 | 6 | 更新 ZIP/目录选择、预检和本地功能名称。 |
| `pet/feature_package_probe.py` | tracked | 2 | 2 | 把本地 descriptor 传入 probe 前置检查。 |
| `pet/feature_package_transactions.py` | tracked | 5 | 5 | 让事务预检/提交使用统一本地信任判断。 |
| `pet/feature_probe_adapter.py` | tracked | 6 | 5 | 把 allow_local_packages 传给 headless probe policy。 |
| `pet/feature_probe_crypto.py` | tracked | 6 | 4 | 同步 probe crypto 文档和本地激活语义。 |
| `pet/feature_probe_windows.py` | tracked | 1 | 1 | 在 Windows probe 边界使用统一 descriptor acceptance。 |
| `pet/feature_version_lease.py` | tracked | 1 | 1 | 允许已验证 local_user descriptor 进入版本租约。 |
| `pet/local_package_intents.py` | tracked | 10 | 6 | 把确认对话改为结构/完整性预检而非签名认证。 |
| `pet/modern_settings_dialog.py` | tracked | 3 | 3 | 同步管理设置中的本地包用户文案。 |
| `pet/plugins/feature_packages.py` | tracked | 7 | 4 | 更新本地包 verifier 示例和兼容说明。 |
| `pet/plugins/package_trust.py` | tracked | 24 | 5 | 增加显式本地用户信任模式，保留 signed-only 兼容路径。 |
| `pet/window.py` | tracked | 11 | 0 | 保留真实 UI 验收所需的无激活键盘上下文菜单路由。 |
| `scripts/build_feature_management_delivery.py` | tracked | 32 | 19 | 构建普通 unsigned AI/screen 本地包，不生成签名。 |
| `scripts/build_feature_management_manual.py` | tracked | 63 | 15 | 生成 manual20 的普通 ZIP/目录和本地激活元数据。 |
| `scripts/build_feature_probe.py` | tracked | 30 | 15 | 排除会在 LPAC 中触发 WSAStartup 的 multiprocessing runtime hook。 |
| `scripts/build_feature_probe_native.py` | tracked | 3 | 0 | 同步 native probe 构建说明/本地 bundle 入口。 |
| `scripts/build_screen_delivery.py` | tracked | 160 | 42 | 移除主路径密钥/签名依赖，增加 unsigned AI 包和兼容 legacy 参数。 |
| `scripts/feature_probe_entry.py` | tracked | 16 | 3 | 校验并传递 local activation policy，保留历史 signed policy 兼容。 |
| `scripts/validate_phase5a_delivery.py` | tracked | 39 | 15 | 更新验收标题、包提示和无签名本地激活断言。 |
| `tests/test_feature_management.py` | tracked | 15 | 0 | 覆盖短 probe 根和本地策略回归。 |
| `tests/test_feature_management_build.py` | tracked | 36 | 1 | 验证 unsigned AI 构建、local_user descriptor 和清单。 |
| `tests/test_feature_management_ui.py` | tracked | 1 | 1 | 验证管理页本地 ZIP/目录文案。 |
| `tests/test_feature_manual_acceptance.py` | tracked | 35 | 0 | 更新 manual acceptance 的本地包合同。 |
| `tests/test_feature_probe_build.py` | tracked | 22 | 0 | 增加 direct import bootstrap 和 multiprocessing 排除回归。 |
| `tests/test_phase5a_delivery_acceptance.py` | tracked | 71 | 14 | 更新四路真实验收驱动的本地包和菜单预期。 |
| `tests/test_screen_delivery_build.py` | tracked | 66 | 1 | 验证 screen builder 无签名主路径和 legacy 兼容参数。 |
| `tests/test_screen_manual_host.py` | tracked | 13 | 0 | 覆盖 screen 独立缺凭据提示。 |
| `tests/test_screen_settings.py` | tracked | 32 | 23 | 覆盖无迁移首装默认配置和独立设置。 |
| `tests/test_phase5a_local_activation.py` | untracked | 58 | 0 | 新增无签名通过、篡改拒绝、signed-only 拒绝的回归。 |
| `tests/test_screen_ai_migration.py` | untracked | 101 | 0 | 新增显式迁移兼容 API 与不自动迁移的回归。 |

生成的 `.scratch/phase5a-local-distribution/cleanup-manifest-20261006.json` 被 `.gitignore` 忽略，不作为源码发布物；它保存了清理目标、边界和结果。

### 架构修正的关键实现

- `FeaturePackageVerifier(allow_local_packages=True)` 对用户显式选择的普通 ZIP/目录返回 `local_user`；事务、probe、版本租约、启动加载和卸载边界全部改用 `accepts_descriptor()`，避免某一个执行边界仍要求 `trusted_official`。
- Phase5A 主构建器不再生成私钥/公钥锚或 `manifest.sig`；历史 Ed25519 工具和显式 legacy 参数仅保留未来正式发布/兼容路线。
- Setup/管理 UI 文案明确区分“安装时选装”和“运行中本地导入”，不再把本地激活描述成官方签名认证。
- 发现并修复 headless probe 的真实启动阻断：PyInstaller `pyi_rth_multiprocessing.py` 在 LPAC 业务入口前导入 `socket` 并调用 `WSAStartup`，LPAC 预期返回 Winsock 10107，导致假性的 `host_probe_failed`。排除未使用的 `multiprocessing` runtime hook 后，probe10 的 AI host-only 验证通过。

### 构建、性能与实机证据

- manual20 构建耗时 `295.814 s`；AI 普通 ZIP `118,378 B`，SHA-256 `6912b007483af418a27e43218a3ed4f71d933d80014420453a24f3bb3bfa8d7a`；screen 普通 ZIP `24,455,393 B`，SHA-256 `e0695c68968244353458885f271be41c7c68f4dd21ee02361cf3c42d9ff8e6a7`；probe bundle manifest SHA-256 `915f87620c3a84219bf7310e0ff805ff3c25d0b05e2f0ba2d4b95c5eba9a3232`；`signature_required=false`。
- Windows 本机真实冻结 Core 的 v13 四路选择矩阵均通过：
  - `empty`：3,137.282 ms，RSS 185,180,160 B，33 threads，exit 0，无功能菜单；
  - `ai`：3,444.613 ms，RSS 205,598,720 B，33 threads，exit 0，`AI 对话` 出现，AI state active `1.0.1`；
  - `screen`：4,069.834 ms，RSS 211,054,592 B，33 threads，exit 0，`主动识屏`/`看看屏幕` 出现，screen state active `1.0.0`；
  - `both`：4,686.232 ms，RSS 193,515,520 B，31 threads，两个功能菜单和两个 active state 均出现。
- 四路均 `pending_transaction=null`、`production_core=true`、自然退出成功；验收根为 `.scratch/phase5a-local-distribution/manual-user-acceptance-20261006-v13-runtime-fix/`。v12 的 AI-only 复验也通过。
- 稳态路径没有新增网络请求；新本地激活路径新增的是本地 ZIP/目录扫描、SHA-256 清单、事务写入和一次 LPAC/Worker probe。实际样本中的启动、RSS、线程、读写次数和字节数已由 `frozen-results.json` 固化；真实 Provider/视觉请求未代替用户执行，因此不虚报模型/网络成本。

### 自动门与清理

- focused：`QT_QPA_PLATFORM=offscreen python -m pytest -q ...`，`144 passed in 47.28s`。
- 全量：`QT_QPA_PLATFORM=offscreen python -m pytest -q`，`4313 passed, 15 skipped, 14 warnings in 717.00s`，exit 0。
- `python -m ruff check pet scripts packaging features tests`：通过；`python -m ruff format --check pet scripts packaging features tests`：`606 files already formatted`；`git diff --check`：通过（仅 Git 的 LF→CRLF 提示，无 trailing whitespace/error）。
- 清理前先生成并复核边界清单；随后只在 `.scratch/phase5a-local-distribution` 内删除 247 个目标，共 221,340 个文件 / 19,215,410,032 B（约 17.90 GiB）。清理后阶段目录为 9,623,836,176 B（约 8.96 GiB）/ 53,299 文件；最终 manual20、probe10、worker08、native05、v8-v13 验收根和五份正式记录均存在，manifest 目标 247/247 均不存在。

### 尚需用户参与的验收

- 本机 `ISCC.exe` 不在 PATH，`C:\Program Files` 和 `C:\Program Files (x86)` 递归查找也未找到；因此只完成了 `packaging/core_webm.iss` 的静态/单测验证，**没有把真实 Inno Setup 向导视觉和安装行为宣称为已通过**。用户需要在安装了 Inno Setup 的 Windows 上确认 `ai` / `screen` 可选任务、旁置包导入、缺包行为和实际安装结果。
- 用户需要在自己的本地可信设置界面输入真实 AI/视觉 Provider 凭据并发起请求；Codex 不代填、不读取、不记录凭据。
- 正式发布签名、Authenticode/SmartScreen、另一台机器、干净用户、更新/卸载和真实模型/截图体验属于独立人工/发布门，不应重新变成当前 Phase5A 本地激活的密钥前置条件。

当前工作树未提交、未推送；本报告及五份 `.scratch` 记录均已更新为上述最终停点。

## 2026-10-07 追加：Setup 实际编译证据

### 结论

本机已找到可用的便携版 Inno Setup：`E:\tools\InnoSetup6\ISCC.exe`，编译器版本为 `6.7.3`，因此本轮不需要另行安装 Inno Setup。当前 Phase5A Setup 脚本仍是 `packaging/core_webm.iss`；它把 `ai` / `screen` 作为安装向导中的可选任务，选中后通过 Setup 旁置的 `packages` 目录调用普通本地包导入，不把公钥、私钥或 `manifest.sig` 设为本地激活前置条件。

### 实际编译命令

本轮使用 manual20 的最新 Core 内容。由于原始 Core 路径最长达到 `343` 字符，包含 `649` 条超过 Windows `260` 字符限制的路径，Inno Setup 直接读取时在压缩阶段失败；没有修改原始 Core，而是复制到较短的忽略目录后重试：

```powershell
E:\tools\InnoSetup6\ISCC.exe /V3 `
  /DCoreDir=E:\AI\DSH\dsh-pet-indesktop\.scratch\p5a-core-20261007 `
  /DCoreVersion=5.0.0 `
  /DCoreOutputDir=E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\setup-acceptance-20261007-shortpath `
  E:\AI\DSH\dsh-pet-indesktop\packaging\core_webm.iss
```

### 实际结果

- 编译结果：成功，`ISCC_EXIT=0`，耗时 `116.031 s`。
- Setup：`.scratch/phase5a-local-distribution/setup-acceptance-20261007-shortpath/dsh-pet-core-webm-setup.exe`。
- Setup 大小：`227,324,912 B`。
- Setup SHA-256：`3780ec4355e4ab0b0bce6285c8fe4aa07289877af43e5bab333f3965a5c9395b`。
- 完整编译日志：`.scratch/phase5a-local-distribution/setup-acceptance-20261007-shortpath/iscc-compile-v3.log`。
- Setup 同目录已放置旁置包：AI `118,378 B`，SHA-256 `6912b007483af418a27e43218a3ed4f71d933d80014420453a24f3bb3bfa8d7a`；screen `24,455,393 B`，SHA-256 `e0695c68968244353458885f271be41c7c68f4dd21ee02361cf3c42d9ff8e6a7`。
- 编译产物清单：`.scratch/phase5a-local-distribution/setup-acceptance-20261007-shortpath/setup-acceptance-manifest.json`。
- 旁置包意图：未勾选任务时不导入；勾选 `ai` / `screen` 时从 Setup 同级 `packages` 目录执行普通本地结构导入，`signature_required=false`。

### 仍未宣称通过的部分

本轮只完成了本机 Setup 编译和静态产物核对，`install_not_run=true`：没有在无人确认的情况下执行会写入 `%LOCALAPPDATA%`、创建快捷方式或注册卸载项的安装向导。`Get-AuthenticodeSignature` 返回 `NotSigned`，这与当前 Phase5A“不把正式发布签名作为本地激活前置条件”的合同一致；它不代表正式发布签名、SmartScreen 或 Authenticode 门已通过。仍需用户最后人工打开这个 Setup，确认向导中 `ai` / `screen` 可选任务、旁置包存在/缺失时的行为、安装后菜单与卸载体验。

本次 Setup 编译没有修改产品源码；新增的是 `.scratch` 下被忽略的短路径 staging、Setup、旁置包、编译日志和清单，以及本报告和五份交接记录的事实补记。此前“本机找不到 `ISCC.exe`”是 2026-10-06 停点时的历史观察，已被本节的 2026-10-07 编译证据取代；不再把“编译器缺失”列为当前阻塞项。


## 2026-10-07 追加：Phase5A 产品边界重写、实施计划与 `.scratch` 清理

### 本次结论

用户最终需求已收敛为两条路径：Setup 内只选装并自动启用官方 AI/Screen；Setup 外用户明确选择普通 ZIP/目录后，按 manifest 注册信息自动路由，未知 owner 只要满足本地包合同即可作为第三方 DLC。Phase5A 本地路径不要求公钥、私钥或 `manifest.sig`；用户主动选择意味着信任包内 Python，但这不是沙箱。结构、兼容性、路径、大小/数量、manifest SHA-256、事务、租约和启动确认仍保留。

### 本次文档产出

- `docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md`：详细根因、数据模型、验证器、事务/启动、UI router、Setup、Core 构建、测试和人工矩阵。
- `docs/PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md`：给下一对话的施工顺序、文件影响面、保护边界、保留产物和回执格式。
- 已同步 `docs/PROJECT-ENTRY.md`、`docs/plugin-phase-05-distribution/README.md`、`docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md`、`docs/plugin-phase-06-ecosystem/README.md`、`docs/INDEX.md`。
- 已更新 `.scratch/phase5a-local-distribution/PLAN.md`、`STATUS.md`、`HANDOFF.md`、`WORKLOG.md`、`SUMMARY.md`。

### `.scratch` 清理实测

清理范围只在 `E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution`，先根据文档/阶段记录排除被引用路径，再删除 38 个未引用的旧构建/诊断目录。实删释放 `4,905,969,506 B`、`9,404` 个文件；清理后该根保留 `43,894` 个文件、`4,970,108,783 B`（约 `4.629 GiB`）。

保留：阶段五份记录、`acceptance-night-20261006` 最终验收根、文档引用的历史候选/发布证据、`setup-acceptance-20261007-shortpath` 和相关旁置包。未触碰 `.scratch/phase4b-local-management`、其他阶段根、源码、真实用户数据和当前 Setup 产物。路径校验确认所有删除目标均位于 Phase5A 根内且不是重解析点，删除后保护路径仍存在。

### 当前未宣称通过

本次没有修改代码、没有重跑测试、没有重建 Core/Setup、没有执行真实 Setup 安装。下一对话必须按新计划先建立失败回归，再实现 registration/router/Setup 内嵌/Core GUI subsystem，最后用同一源码工作树重新验证；旧 manual20/v13/Setup 编译证据只能作为历史基线。真实 Provider 请求、屏幕截图识别和用户主观 Setup 体验仍由用户最后确认。

## 2026-10-07 追加：文档与清理收尾验证（本轮）

本节只记录本轮文档/清理工作，不把它误写成产品代码已通过：

- `git diff --check -- docs .scratch`：通过；仅有 Git 的 LF→CRLF 工作树提示，无 whitespace/error。
- `python -m pytest -q tests/test_pr_report_discipline.py`：**57 passed in 3.15s**。
- 13 份相关 Markdown 的 UTF-8、尾随空白和相对链接检查：文件全部存在、尾随空白 `0`、URL 解码后的断链 `0`。
- 清理动作的即时测量仍为：38 个旧目录、9,404 个文件、`4,905,969,506 B` 释放；当时根统计为 43,894 个文件、`4,970,108,783 B`。在随后补写五份阶段记录/保留目录发生变化后，末次只读重算为 43,901 个文件、`4,970,128,950 B`；没有据此追加删除，也没有触碰保护路径。
- 本轮未运行产品测试、Ruff、Core/Setup 重建、Setup 安装或真实 Provider 请求；这些门交给下一对话和用户人工验收。
