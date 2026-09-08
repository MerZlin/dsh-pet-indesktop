# 自 v4.1.0 发布以来的开发版变更汇总

> 范围：`v4.1.0`（tag `4eb8c37`，2026-08-29/30）→ 当前开发版 main `243e4cb`（2026-09-09，未发布）
> 统计：137 个提交，主要合入 PR：#65（perf/stage-1）、#64（设置重构）、#68（keyring 迁移）、#70（高刷屏流畅度）、#71（Linux Fcitx）、#72（待办提醒）、#73（门控/低内存）、#76（内存专项+单进程多窗）、#79（直播捕获/边缘交互/macOS Dock）、#80（识屏自我识别/Harness）、#82（dsh 启动静默化）、#85（子肥鱼生命周期+彩蛋），另有大量直推修复/重构批次。
>
> **口径说明**：本清单以「当前 main 实际生效」为准。期间有些功能实现后被回滚/取代（详见第六节），汇报时勿按提交记录误述为已上线。主要来源：137 个提交正文（git）、`docs/HANDOVER_2026-09.md`、`docs/SETTINGS-REDESIGN-IMPLEMENTATION-LOG.md`、README 开发版基线。

---

## 一、主线演进一览（合入顺序）

| 日期 | PR/提交 | 主题 |
|---|---|---|
| 09-01 | 直推批次 | perf/stage-1 早期（WebM reader 生命周期、闲置降帧、帧缓存、会话原子写…，见第三节） |
| 09-03 | #65 `3e86e41` | perf/stage-1：性能线（帧缓存/闲置节流/reader 受控）+ 结构线（window.py 拆分、模块收编）+ 修复批 |
| 09-03 | #64 `260c059` | 设置与菜单重构（settings redesign） |
| 09-04 | #68 `d542599` | API Key 明文自动迁移进 keyring |
| 09-04 | #70 `691f4a4` | 高刷新率流畅度优化（节拍跟随刷新率 + 精确定时器） |
| 09-04 | #71 `638cb6d` | Linux Fcitx 中文输入插件打包 |
| 09-04 | #72 `7468808` | 桌宠待办提醒（气泡/桌面通知 + 右键管理面板） |
| 09-05 | #73 `221dc1b` | 可选服务门控/灵动岛峰谷/动画预热低内存 + Phase3 进程插件化设计稿 |
| 09-06 | #76 `749bf5e` | **内存专项 + 单进程多窗 + 交互稳定性 + 代码健康**（最大批） |
| 09-08 | #79 `6bc9849` | 直播捕获气泡并入主窗/macOS Dock 隐藏/黄金回旋/边缘探头/素材+9 |
| 09-08 | #80 `8a8a8ae` | 识屏自我识别 + Harness 复用本机实例 + 开关残留清理 + `harness_autostart` |
| 09-09 | #82 `85beb1a` | dsh 启动链路静默化（隐藏探测窗口 + `--no-open` 落盘缓存） |
| 09-09 | #85 `243e4cb` | 子肥鱼生命周期修复（生成继承/一键退出）+ 边缘探头撞飞 + 彩蛋 |

---

## 二、用户可见新功能（含小功能）

### 2.1 多开与共享解码（#76 核心）

- **单进程多开（正式特性）**：设置 → 常规 → 多开 →「单进程多开（省内存）」开关（配置键 `experimental_single_process_spawn`，默认关，重启生效）。开启后「生小肥鱼」在本进程内创建第二个桌宠实例（独立 slot/配置/会话/素材库），不再拉起新进程。
  - 实测（README 口径）：单进程 3 窗共 1 进程约 **181–197MB**，3.5h 无单调上涨；多进程模式约 270MB/3 只。
- **同角色共享解码链**：进程内 `DecodeFanoutHub`（帧扇出）替代早前的 shm broker——同角色多窗只保留 **1 条 ffmpeg 解码链**，其余窗口“订阅”进食；首发窗发布、订阅窗进食、handover 扶正、消费侧看门狗。
- **进程级共享子系统 + 托盘聚合**（flag 开时）：Agent 联动/主动识屏/全屏探测上移到进程级共享，事件扇出到各可见窗（多窗一起跳舞语义保持）；托盘聚合为「单托盘 + 每窗子菜单（显示/隐藏、切换角色、退出这只）」；灵动岛单击聚合全部窗；DSH 插件授权弹窗只弹一次；主窗退出自动提升下一窗为主。
- **slot 配置作用域规则**（写入 README）：每窗独立项（形象/位置/聊天）存 `config-slot-N.json`；进程级共享项（托盘/共享解码/Agent 联动/待办）以主桌宠 `config.json` 为准。
- **新实例初始配置跟随主设置（slot 落种）**：新开小肥鱼首占某 slot 时用主配置落种（剔除位置/朝向等每窗状态键），已有存档一律不覆盖；可继承主大小或独立尺寸（`spawn_inherit_size`/`spawn_scale`）、继承灵动岛开关（`spawn_inherit_dynamic_island`）；设置里可「一键清除子肥鱼」。
- **省电模式**（原「闲置降帧」升级）：闲置降帧 + 停止后台动画预热，一处开关、即时生效（见 2.10 / 第三节）。
- **高级内存调节键**（README「内存调节（高级）」）：`first_frame_cache_max_mb`（首帧缓存预算，默认 8MB）、`predict_prewarm_lead_ms`（预测式首帧预热提前量，默认 350ms）、`ffmpeg_recycle_minutes`（ffmpeg 圈边界定期回收，默认 10min，0=关）。

### 2.2 边缘交互 / 点击玩法（#73 / #79 / #85）

- **黄金回旋**：右键菜单新增「黄金回旋」入口（legacy 与默认 modern 模板同加）；设置新增「点击触发黄金回旋」`golden_spin_on_click`（默认关：点击动画播完自动接回旋）与子开关 `golden_spin_direct`（默认关：开启后点击直接回旋并跳过 Q 弹/点击素材，再点 130ms 内收尾当前圈、立即开下一圈，逐圈加速 700ms→×0.82→200ms 下限）；无点击素材时也不再只 arm 不启动；修靠边/旋转时 paintEvent begin/end 失配报错。
- **边缘探头**：`edge_probe_enabled`——拖到屏幕左右边缘自动进入探头姿态；按当前 ±45° 旋转投影 bbox 计算露出量（常驻露出 0.55，点击拉直 0.82）；探头激活时点击不播放点击音效；托盘「回到右下角」命令会先取消探头姿态再移动。
- **彩蛋（被撞飞翻鱼头）**：探头激活被撞时头部跟随速度方向整帧旋转（`ThrowEggController`，`90+atan2(vy,vx)`），低速触碰边界/其它桌宠回正；恢复阈值按实机反馈调到 780px/s；撞飞落地停稳 5s 后若静止于边缘重新吸附（倒计时内拖拽作废）。
- **拖文件模拟吃掉（#73）**：桌宠支持把文件拖到身上“吃掉”，播放吃动画并记录统计（不真实删除文件）。
- **动画素材扩充（#79）**：同步上游 9 个动画（工作状态×6 + 碎碎念×3），本地动画总数 97→106；README 顶部加居中状态徽章。

### 2.3 子肥鱼生命周期与清理（#85，多轮实机事故驱动）

- **「退出子肥鱼」语义最终定为「只退出不删数据」**：不再删除 `config-slot-*`/`sessions-slot-*`/`todo_items-slot-*`，子肥鱼设置原样保留、下次生成恢复；菜单/按钮/确认框文案同步「退出子肥鱼」（去掉确认框与结果框，操作不删数据、结果写日志）。
- 清理实现细节（供测试观察）：运行时标记 v2（`pet-runtime-v2-*`，showEvent 即登记，未拖动过的新生鱼也能被枚举）；两段式杀法（先全部 taskkill 再统一确认）+ `CREATE_NO_WINDOW` 无弹窗；杀前用 exe 路径核验 pid 身份（防 pid 复用误杀）；`GetExitCodeProcess==STILL_ACTIVE` 判定真死活（修探活误判）；slot-0（主鱼）永不杀、入口只挂主鱼；单进程模式先按进程内登记表关窗再文件级清理，链式逐只关闭防 UI 冻结。

### 2.4 识屏 / 看看屏幕 / 灵动岛（#73 / #80）

- **识屏自我识别提示**：看看屏幕与主动识屏的视觉请求注入角色显示名（`pet_name`），模型知道画面角落的桌宠就是自己化身、以第一人称看待，不再说成“陌生程序”；只加 user 文本，不污染自定义 system prompt。
- **可选服务开关式加载门控**（#73 Phase1）：碰撞 IPC/待办/Agent 联动/主动识屏按配置懒创建，关闭即不装配不启动、不拉线程/定时器；PIL 延后到截图时导入。
- **灵动岛余额峰谷按系统时间刷新**：按北京时间档位直接显示「当前档位 → 下一档切换时间」，单发 QTimer 排到下一 9:00/12:00/14:00/18:00（或下周一 9:00）边界自动重排；峰谷颜色随档位生效。
- **开关残留审计清理**（#80）：删除无人读取/派生配置键（`click_sound_path` 默认值、`throw_max_speed` 派生键等）；预热开关并入省电模式语义，冷启动即省电时不再漏关预测式预热。

### 2.5 会话保存与 AI 对话（#65 线 R3 / #76）

- **会话原子追加**：`SessionStore.append_message/append_messages` 在全局 io 锁内“读→追加→提交”原子完成；modern/legacy/quick chat 三前端 + “看看屏幕”问答同步全部走原子路径——**生成回复期间其它前端写入不再被整会话保存覆盖**；不开聊天窗的识屏问答也会原子落盘；save 被拒在日志中以 warning 可观测。
- **会话异步写盘（进程内版）**：写盘入串行后台 worker，GUI 只做内存快照入队；读穿 pending（刚保存立即可见）；诚实 flush/close；关闭屏障防双 writer 写同一 tmp；`os.replace` 遇瞬时 WinError 5（Defender/索引器占用）有界退避重试。
- **Legacy 聊天时间本地化**：旧版聊天时间戳改为本地时区显示（与 Modern 一致）。
- 快速对话（QuickChat）：收到顶层 `WindowDeactivate` 自动关闭并复用原关闭路径停止在飞请求；修复 macOS 唤出后残留空白窗。
- 会话删除先 `_reset` 停打字机（防幻影消息写入新会话，modern+legacy）。

### 2.6 Agent 联动与 DSH（#73/#76/#80/#82/#64 等）

- **DSH 桥接富事件归约**：thinking（思考）/working（干活，带工具名）/attention（需确认）/error/idle 多态，多会话聚合优先级 attention>error>working>thinking>idle，子代理不抢状态；agent/status 作旧宿主回退（见过富事件后自动停用）。
- **opencode reason 分流**：`step-finish` reason=tool-calls（模型停笔等工具结果）不再误报完成——治好长跑 task 子代理/慢工具（长 bash/dev server）的**假完成音/气泡**与回注后的假开始音。
- **启动 Harness 复用本机已有实例**：探测顺序改为配置端口优先、其次官方默认 3080——用户已自己跑着 dsh web 时直接复用打开，不再重复拉起第二个实例（关联 issue #10 双开浏览器）；菜单点击启动新实例时冒泡提示「正在后台启动」（首次 npx 拉包可能几分钟）。
- **「随桌宠启动 dsh 服务」开关**：配置键 `harness_autostart`（默认关）——开机自启场景下主窗就绪即后台拉起 dsh web（只起服务、不开浏览器、不弹窗口）；设置 → 常规 → 应用启动新增开关，仅主桌宠可设置，slot 落种不继承。
- **dsh 启动链路静默化（#82）**：所有探测子进程隐藏窗口（`CREATE_NO_WINDOW`，去掉 `cmd /c start /b` 与 `DETACHED_PROCESS` 组合——实测会弹常驻终端）；`--no-open` 能力探测三级缓存（进程内 dict → 落盘 `harness_probe_cache.json`，按 `dsh --version` 匹配 → 慢探测），超时放宽到 30s——修复开机高负载下误判导致 dsh 自弹浏览器（issue #10 根因）。
- **macOS Node 解析**：新增桌面安全 Node 解析器（`pet/node_runtime.py`），Harness 与 Agent bridge 共用，增强 PATH 传播给 npm/pnpm——修复 Finder 双击启动绕过 Homebrew Node 发现（issue #67）。

### 2.7 待办提醒（#72）

- `TodoReminderService`：30s 轮询纯决策逻辑；到期/提前（`todo_reminder_lead_minutes`，默认提前 5min）提醒槽；10 分钟宽限；单次提醒项自动归档；错过宽限的提醒静默盖戳（醒来不轰炸）。
- 提醒形式：桌宠可见时气泡、否则桌面通知（受 `system_notifications_enabled` 总开关约束）。
- 右键菜单新增「待办」面板（CRUD、矢量图标）；设置「自动化与联动」域管理启用与提前量；数据存 `todo_items[-instance].json`（原子写）。

### 2.8 设置系统重构（#64 settings redesign，超大批）

- **七能力域侧栏**：常规（含 macOS Dock 设置）、桌宠、互动、菜单、桌面组件、AI 与对话、自动化与联动；菜单域内含三个同级任务用页内 Tab（菜单编排 / 快捷启动 / 外观）。
- **菜单编排（可配置右键菜单）**：左侧编辑、右侧**实时预览**。操作集：显隐勾选、移动到（子菜单/根）、新建子菜单、插入/删除**分割线**、更换**别名**（编辑器显示“别名（原名）”、运行时菜单只显别名）、图标覆盖（内置矢量 / none / 本地图片文件 PNG·JPG·WebP·BMP·GIF·TIFF ≤5MB，contain/cover 显示）、恢复默认名称/图标/布局、删除子菜单（二次确认，子项提升回根）。
  - 默认模板 `modern-default-v1.json` 内置显式分割线；配置缺失回默认、损坏/不支持的 schema 只保留「桌宠设置/退出」安全菜单；旧自定义树自动补入新默认 action（按最近模板兄弟锚点插入，不动现有顺序/显隐）。
  - 运行时能力判定：动作缺条件置灰 + tooltip；彩蛋关闭后节点保留原位显示「已停用」；无快捷应用时 quick_launch 置灰。
  - 配置键 `context_menu_layout`（默认 None=内置模板）；新版菜单唯一维护入口（旧版仅迁移期兼容）。
- **快捷启动**：设置内双行应用列表编辑（添加默认浏览器/选应用文件/移除）；右键菜单「快捷启动」子菜单无项时**恒出现**并显示禁用占位「尚未配置快捷项」。
- **外观/依赖显隐统一**：菜单颜色主题成为设置窗口显式主题来源（开关/下拉/chevron/弹层明暗沿祖先链读取 `settingsDark`，选主题即时整页生效）；ToggleSwitch 从属项统一「开显示/关隐藏」（灵动岛、彩蛋、碰撞、主动识屏、自言自语、半透明、Agent 音效等经审计）。
- **图片目录预览抽屉**：自言自语图片目录/彩蛋弹窗图片目录新增「预览」→ 右侧 3 列瀑布流缩略图（保留宽高比、512px 解码上限、文件名省略+全名 tooltip、空态文案、延迟解码）。
- **macOS 原生 Dock 右键菜单**：显示桌宠 / 桌宠设置 / AI 对话 / 退出（`setAsDockMenu`），鼠标穿透后仍有稳定恢复入口。
- **三态响应式**：1600 / 900 / 720px 断点（wide / medium / compact），窄宽隐藏「位置」列；125% 字体 + 极端中英文案矩阵逐页验收（`docs/SETTINGS-REDESIGN-UI-ACCEPTANCE.md`）。
- **结构调整**：`modern_settings_dialog.py` 4811→1857 行（控件库/菜单编辑器/AI 页/主题 QSS 拆出），后续 #76 再清理孤儿簇（净 -1300+ 行）。

### 2.9 菜单 / 小功能

- **气泡配图大小可调**：`self_talk_image_scale`（50–300%，默认 100）——设置 → 自言自语 →「配图大小」；标准气泡 220×140 目标框随缩放、呼吸气泡按 base_size；功能关闭时该行随从属行隐藏。（彩蛋弹窗大小键曾实现后被 revert，不生效）
- **彩蛋入口主题修复**：标题/提示文字继承菜单前景色、悬停取主题 `light_hover/dark_hover`（深色主题下不再不可读）。
- 托盘与多窗子菜单「回到右下角」；右键「切换角色」等既有入口保持。
- **keyring 明文迁移（#68）**：升级加载时把磁盘明文 API Key 自动迁入系统 keyring（含视觉 Key），keyring 不可用时回退内存明文，不覆盖已有 keyring 值。
- **点击音效**：设置写回仅在开关/音效包变化时预热；播放前显式 stop 修复后续无声；自定义音效包升级语义整理。
- icon.ico 随素材确定性重建（构建链）。

### 2.10 播放 / 省电（最终形态，见第三节实测）

- 见第三节「性能专项」：闲置降帧（`idle_low_fps_enabled` 默认关，设置页显示为省电模式）、素材预热力度 `media_prewarm`（full/balanced/minimal，默认 balanced）、`first_frame_cache_max_mb=8`、`predict_prewarm_lead_ms=350`、`ffmpeg_recycle_minutes=10`、拖拽 120Hz 合帧、非显示 clip 清显示槽等。

### 2.11 跨平台

- **Linux Fcitx 中文输入（#71）**：构建时按 PySide6 Qt 精确版本编译 Fcitx5 Qt6 输入法插件随包分发（内置 Qt 与系统插件 ABI 不兼容导致中文输入法失效），真实 Fcitx/Rime 探针验证；Linux 设置页不再创建 Windows 专属「光标隐藏穿透」开关。
- **macOS**：Dock 隐藏彻底生效并加恢复提示（issue #74，设置「显示 Dock 图标」`show_dock_icon` 关闭后真正隐藏 + 恢复路径提示气泡）；原生 Dock 快捷菜单；Finder 启动的 Node 解析。
- **Windows**：穿透切换改原生 `WS_EX_TRANSPARENT`（根治打字频闪，见第四节）；高刷屏精确定时器。

---

## 三、性能与内存专项（#65 / #73 / #76，实测口径）

> 早期若干方案在收口时被回滚/替换（见第六节），以下为**当前 main 生效**项：

1. **非显示 clip 清空显示槽**（修主进程内存慢涨主因）：实测每个播过的 clip 永久持帧 ≈1.76MB/段，97 段 ≈170MB；改为切走/弃播/硬停即清，15min 显示槽恒定 3.5MB（修前同口径 26MB 线性涨）。
2. **ffmpeg 常驻循环解码**：主 reader 进程内 `-stream_loop -1 -readrate`，消灭旧设计「每 10s 一圈杀进程重启」的 churn；`-threads 1`（实测每进程 -13MB）；圈边界仍交结束标记供上层调度，续播软停驻留 + ack 握手 re-arm。
3. **ffmpeg 圈边界定期回收**（`ffmpeg_recycle_minutes`，默认 10min）：长寿 ffmpeg 进程 47→64MB 的周期爬升在圈边界清零。
4. **帧转换链直接重建**：#76 移除帧缓存（无缓存重建实测 1.13ms/帧 @1x、2.37ms @2x，24fps 代价可忽略），转为首帧缓存字节预算（8MB LRU + pinned 交互核 ~5MB）。
5. **预测式首帧预热**：当前动画剩余 ≤350ms 提前后台解码下一段首帧进 LRU（`predict_prewarm_lead_ms`）；分布回归 20000 链钉死；首帧 LRU 不逐出高频交互链（点击定格 8→1~2 次/局）。
6. **闲置降帧**（`idle_low_fps_enabled` 默认关）：闲置 >30s 隔帧发布（24→12fps 观感、时长不变），交互/Agent 忙碌立即回满；解码背压让 ffmpeg 解码随降帧减半（台架实测解码 CPU -54.6%，5.2% vs 11.5%）。
7. **高刷屏流畅度（#70）**：物理/弹跳/拖拽节拍跟随主屏刷新率（165Hz→6ms、120Hz→8ms，≤90Hz 保持 16ms）；QTimer 改 PreciseTimer（Windows 粗定时器 16ms 在 15.6/31.2ms 抖动、8ms 被钳到 ~64Hz）；moveEvent DPR 兜底轮询限频 10Hz；新增 `PET_PERF_STATS` 观测模式（默认零开销）。
8. **拖拽合帧**：mouseMoveEvent 只记录最新目标、8ms 定时器消费（~120Hz），碰撞提交维持 20Hz；预热让路（拖拽/点击/菜单期间低优预热挂起）。
9. **碰撞预测限频**：反弹预测每 tick 圆链扫描限 33Hz、状态上报先限流再建状态——多实例碰撞活跃期 CPU 61%→43%。
10. **隐藏即停**：窗口隐藏后暂停动画解码/定时器；音乐检测 timer 按需启停。
11. **内存实测汇总（README 口径）**：三开热机 361–402MB/只 →（#76 后）多进程 ~270MB/3只、单进程多窗 181–197MB/3窗且 3.5h 无单调上涨。
12. **首帧/窗口命中**：Windows mask bounds 用 Qt C++ 路径（0.32ms/帧，Python 扫描 1.11ms 慢 3.5 倍被弃），与绘制逐像素一致。

---

## 四、Bug 修复清单（用户可感知）

1. **打字时桌宠频闪（Windows，根治）**：全屏判定排除工具窗口与截图覆盖层（PixPin/Snipaste 等，issue #62 相关）；穿透切换改原生 `WS_EX_TRANSPARENT`，不再触发 Qt flags 变更 → 原生窗口销毁重建（每次重建=消失一瞬）。
2. **WebMClip 僵尸 reader / ffmpeg 进程泄漏**：PySide6 destroyed 槽不触发自身 bound-method 的根因修复（无 receiver callable + cleanup 显式断开）；stop 主动 terminate + kill 兜底；Popen 生命周期由 reader 线程独占、GUI 零等待/有界 TerminateProcess。
3. **快速连点/切动画卡顿**：GUI 线程绝不再 join 退役 reader（曾每帧等 0.5s）。
4. **动画切换静默停滞**：start 失败显式降级 + 有限重试。
5. **POSIX 协调者被杀后选举死循环（issue #42）**：残留 Unix socket → 探测→removeServer 重试；`_accept_connection` bytesAvailable 兜底。
6. **dsh 开机自启弹浏览器/空终端窗（issue #10/#82）**：`--no-open` 探测落盘缓存 + 探测窗口隐藏。
7. **子肥鱼一堆生命周期**（见 2.3）：杀不掉（探活误判）、pid 复用误杀、控制台弹窗、旧 glob 清不到、覆盖用户存档（曾 SPAWN_FRESH 强制重播种顶掉设置）等全部修复。
8. **边缘探头被撞后会话悬空 / 拖到右下角带探头姿态**：撞击前 cancel 会话；`go_default_corner` 先取消探头。
9. **打字机/点击音效无声**：播放前显式 stop；预热时机收敛。
10. **碰撞/联动状态误报**：opencode tool-calls 假完成；DSH 富事件聚合优先级；set_policy 部分字典不再误判开关变更。
11. **会话数据**：生成期间整会话 save 覆盖其它前端写入；删除当前会话后幻影消息写入新会话；瞬时文件锁 WinError 5；Legacy 时间未本地化。
12. **macOS Dock 隐藏不彻底（issue #74）** + 恢复提示；Finder 启动找不到 Homebrew node（issue #67）。
13. **Linux 中文输入法不可用（#71）**；macOS 菜单深色下文字不可读（彩蛋、hover）。
14. **显示/缩放**：跨 DPI 屏/系统缩放变化画面不重建（Qt 信号驱动重建）；squash 期间命中 mask 重建限频（收势帧强制同步）；素材原地替换后旧帧残留。
15. **全屏隐藏误判**：工具窗口/输入法候选框不再视为全屏；截图覆盖层进程级排除。
16. 其它健壮性（三方盲审批次 17 项等）：畸形碰撞消息不抛异常、更新检查线程收口+重入防护、`>7 天 pet-*.log` 启动清理、SSE 空心跳行跳过、设置 Esc 关闭也落盘、图标解码 30s 超时逃生、菜单树释放 3s 总上限、`shiboken6.isValid` 守卫消 RuntimeWarning 等。

---

## 五、结构治理 / 工程化（开发者向，汇报可简述）

- `window.py` 4307 → ~3900 行（只许瘦不许胖，行数预算机制 + 拆分公约 `docs/WINDOW_PY_SPLIT_GUIDE.md`）；拆出 collision_client/platform_win/platform_mac/multi_window_shared/decode_fanout/predictive_prewarm/settings_widgets 等模块。
- 架构红线机器化（`tests/test_architecture.py`）：纯逻辑层不依赖 Qt、解码链单向依赖、`PetWindow` 私有面冻结、window.py/设置对话框行数预算；孤儿簇守卫（存在且零引用 = 红）。
- 配置键纪律：普通顶层键三处登记（默认值 + reload 白名单 + schema 快照）；特例键走迁移路径。
- PR 门禁 CI（`pr-test.yml`）：三平台 pytest offscreen + ruff（此前 PR 无门禁）；时序 flake 家族隔离（webm 生命周期族、rapid_start_stop、低优预热让路族）并按需一次重跑；CI 成本纪律写入 AGENTS.md。
- 死代码清理（-1300+ 行）：孤儿簇整删（settings_widgets 等 715 行文件、3 个 QSS）、零引用符号、旧 onefile 缓存清理脚本等。
- docs 体系：HANDOVER_2026-09、WINDOW_PY_SPLIT_GUIDE、SETTINGS 系列、PHASE3 调研稿、内存测量档案。

---

## 六、「实现过但当前不生效 / 被取代」说明（汇报防误述）

1. **shm 共享解码 broker**（`decode_broker_enabled`，Windows x64，09-02 上线）→ 09-06 #76 以**进程内 DecodeFanoutHub 取代**：`decode_broker.py` + 4 套测试删除（-3400 行），旧键读到即告警删除。当前多开共享解码 = 进程内帧扇出（见 2.1）。
2. **会话异步保存跨进程版**（B8 第一版：跨进程锁/墓碑/CAS）→ 回滚后以**进程内版**重启生效（见 2.5）。
3. **Agent 监视器移出 GUI 第一版**（B9 第一版）→ 回滚后以 worker+代次+outbox 第二版重启生效（见 2.6/4）。
4. **P3 首帧/缩略图 LRU**（`first_frame_cache.py` + 64MiB 预算）→ 整族回滚（首帧缓存按动画数自然封顶；后续 #76 另用 8MB 字节预算 + pinned 集实现）。
5. **B14 动画 bounds 预计算**（`bounds_precompute.py`）→ 回滚（收益每帧 1-2ms 不值得带未闭环风险；其中帧号 0-based 契约由后续提交独立落地）。
6. **彩蛋弹窗图片大小** `menu_easter_egg.image_scale` → 已 revert（需求目标为气泡配图 `self_talk_image_scale`）。
7. **`animation_prewarm_enabled` 独立键** → 并入省电模式语义（键删除，机制保留）。
8. 早期**预缩放成品帧缓存**（frame_cache，64MB 预算）→ #76 移除改每帧直接重建（实测代价可忽略）。

---

## 七、重点测试清单（按功能分组，含观察点）

1. **单进程多开**：设置开启 → 「生小肥鱼」两次 → 任务管理器确认 1 个进程；三窗同角色待机确认 **1 条 ffmpeg**；托盘子菜单逐窗「显示/隐藏/退出这只」；杀主窗后某子窗自动提升；3.5h 内存无单调上涨。
2. **子肥鱼继承/退出**：子肥鱼改大小/设置后生成第三只 → 继承主设置还是独立按存档；「退出子肥鱼」后数据（设置/会话/待办）保留、下次生成原样恢复；多开时杀子鱼进程无残留、无控制台弹窗。
3. **边缘探头/黄金回旋/彩蛋**：拖到屏幕边缘探头吸附（露出 0.55）、点击拉直；开 `golden_spin_direct` 后连点看逐圈加速；探头激活时被其它桌宠撞飞 → 头部随速度方向翻转、停稳 5s 后重新吸附；托盘「回到右下角」不带斜姿。
4. **闲置降帧/省电**：开省电模式停手 30s+ → CPU 回落、动画半帧率；动鼠标/ESC 立即回满；Agent 忙碌不降帧。
5. **看看屏幕自我识别**：画面里有桌宠形象时，回复应自称化身而非“陌生程序”。
6. **Harness/dsh**：已手动跑着 dsh（3080）时点菜单 → 复用不新起；勾 `harness_autostart` 重启 → 后台起服务不弹浏览器/窗口；开机高负载下不再误弹浏览器。
7. **待办提醒**：新建带提前量的待办 → 到期前 5min 提醒一次；错过宽限补盖戳不轰炸；关闭系统通知后走气泡。
8. **菜单编排**：右键 → 桌宠设置 → 菜单 → 编排：勾选显隐/拖入子菜单/新建子菜单后删除（子项回根）/插删分割线/别名（菜单里只显别名）/图标覆盖（内置、本地文件、>5MB 报错、contain/cover）；保存后真实右键菜单与预览一致；深浅主题切换即时生效。
9. **图片预览抽屉**：自言自语/彩蛋图片目录「预览」→ 3 列瀑布流、空态、tooltip。
10. **气泡配图大小**：`self_talk_image_scale` 50–300% 生效；关闭自言自语该行隐藏。
11. **会话并发**：回复生成中点气泡「看看屏幕」或另开前端 → 两端写入都不丢；不开聊天窗识屏问答重启后仍可回看。
12. **keyring 迁移**：用旧版磁盘明文 key 配置启动 → 升级后 key 在 keyring、聊天/视觉可用；卸载重装不静默丢 key。
13. **opencode/联动**：长跑 task（>800ms）tool-calls 阶段无假完成音；真正 stop 才一次。
14. **Fcitx（Linux 包）**：系统输入法切中文在桌宠/设置输入框可上屏。
15. **高刷屏**：165Hz 显示器拖拽/弹跳丝滑；切换 DPI 屏画面即时重建不模糊。
16. **性能回归观察点**：长时间循环播放 + 随机动作内存稳定；快速连点无卡顿；多窗共享解码 ffmpeg 进程数=1。

---

### 附录 A：新增/变化配置键速查（名称以当前 main 为准）

| 键 | 默认 | 含义/入口 |
|---|---|---|
| `experimental_single_process_spawn` | False | 单进程多开（设置→常规→多开，重启生效） |
| `decode_broker_enabled` | 已下线 | 读到旧值即告警删除 |
| `first_frame_cache_max_mb` | 8 | 首帧缓存预算 MB（4–64，README 高级内存调节） |
| `predict_prewarm_lead_ms` | 350 | 预测式首帧预热提前量 ms |
| `ffmpeg_recycle_minutes` | 10 | ffmpeg 圈边界回收间隔（0=关） |
| `idle_low_fps_enabled` / `idle_low_fps_threshold` | False / 30.0 | 闲置降帧（设置页=省电模式） |
| `media_prewarm` | balanced | 素材首帧预热力度 full/balanced/minimal |
| `golden_spin_on_click` | False | 点击动画播完自动接黄金回旋（设置→互动） |
| `golden_spin_direct` | False | 点击直连黄金回旋（跳过点击动画，逐圈加速） |
| `show_dock_icon`（macOS） | 开 | 设置「窗口与系统」区，关闭后 Dock 真隐藏 |
| `edge_probe_enabled` | False | 边缘探头 |
| `harness_autostart` | False | 随桌宠启动 dsh web（设置→常规→应用启动） |
| `todo_reminder_enabled` / `todo_reminder_lead_minutes` | True / 5 | 待办提醒总开关/提前分钟 |
| `self_talk_image_scale` | 100 | 气泡配图大小 %（50–300） |
| `context_menu_layout` | None | 菜单编排模板（默认 modern-default-v1） |
| `spawn_inherit_size` / `spawn_scale` / `spawn_inherit_dynamic_island` | True / – / False | 生小肥鱼继承设置 |
| `user_customized` | False | 子肥鱼是否自定义过（落种/刷新判定） |
| `system_notifications_enabled` | （上游） | 系统通知总开关（待办/更新等） |
| `music_sing_enabled` | False | 音乐检测自动唱歌（timer 按需启停） |

### 附录 B：关联 issue 映射

- issue #10（双开浏览器/启动静默化，#82）· #42（POSIX 选举死循环）· #62（直播捕获气泡并入主窗 + 打字频闪/#79/#76）· #67（macOS Finder Homebrew node）· #69（唱歌检测/副槽继承/频闪）· #74（macOS Dock 隐藏）。
