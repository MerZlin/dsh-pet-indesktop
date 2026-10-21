# -*- coding: utf-8 -*-
"""架构红线断言（结构线纪律的机器化）。

三条红线，红了就是架构倒退，不许靠改测试放行：
1. 依赖方向：纯逻辑层（collision/physics）不依赖 Qt；
   decode_fanout 不反向依赖 window/webm_clip（钩子经 movie 属性注入）。
2. 私有面冻结：PetWindow 私有成员（win._xxx）只许 window.py 自身访问；
   app.py / agent_link.py / context_menus/ 再出现即为违规（S2 已清零，防回潮）。
3. window.py 行数预算：结构线拆到 4200 量级后只许降不许涨——
   新功能请先按 docs/WINDOW_PY_SPLIT_GUIDE.md 拆对应控制器，
   而不是继续往上帝类里塞。确实该涨时，预算上调必须在 PR 里说明理由。
"""
from __future__ import annotations

import re
from pathlib import Path

PET_DIR = Path(__file__).resolve().parents[1] / "pet"

# window.py 行数预算：合并上游 v4.1.0 后实测 4229，留 ~1.7% 余量。
# 拆分控制器时本预算应随之下调。
# 2026-09-04 上调到 4330（流畅度批次：刷新率自适应节拍、PreciseTimer、
# DPR 兜底轮询限频、perfstats 帧间隔看门狗；均有实测数据支撑）。
# 2026-09-05 上调到 4345（批11-B1：ffmpeg 圈边界定期回收——窗口层把
# ffmpeg_recycle_minutes 经 _push_recycle 推送给播放 clip（_switch /
# _fallback_playable_idle / 拖拽重启 / refresh_pet_settings 四处对齐），
# 复审 P1-2 要求运行期可刷新。注：此前的注释日期「2026-11」为笔误）。
# 2026-09-05 上调到 4352（批12：_switch 切走成功时清旧 clip 显示槽——
# A1 修复的窗口权威侧，+6 行含注释；clip 侧清槽 API 在 webm_clip.py）。
# 2026-09-05 上调到 4360（批12 复审 N1：_on_clip_finished 对弃播 clip
# 在结束标记消费点补清显示槽，+6 行含注释）。
# 2026-09-05 上调到 4367（频闪修复：Windows 穿透改原生 WS_EX_TRANSPARENT
# 样式位、不再 setWindowFlag 重建窗口 +4 行含注释；hideEvent 补 [VIS] 观测
# +1 行。详见 _plan/current/memory/REVIEW_flicker_glm53.md）。
# 2026-09-05 批5.3 合入：删 broker 首个 idle 延迟与轮询（净 -44 行），
# 频闪修复保留（+5），合并后实测 4261 行，预算按实测 +50 行余量收紧。
# 2026-09-06 黄金回旋 + 边缘探头：窗口仅保留薄钩子（绘制/mask/命中/拖拽/
# 点击/隐藏冻结/动画约束），控制器实现在 golden_spin.py / edge_probe.py /
# window_optional_services.py；window.py 净增约 47 行、实测 4333，
# 预算上调到 4340（+7 余量，理由见本注释）。
# 2026-09-07 上调到 4350（探头/回旋第四批微调：go_default_corner 在回到右下角前
# 取消激活中的边缘探头会话，防止宠物斜着出现在右下角；实测 4350）。
# 2026-09-08 上调到 4352：合入识屏自我识别 pet_name 传递（+4）与死键清理（-2），
# 实测 4352。window.py 分块拆分仍是待办，拆分前预算只随实测校准。
# 2026-09-08 上调到 4358：批 D 彩蛋（边缘探头被击飞时飞行中整帧旋转跟随速度方向）——
# window.py 仅保留薄钩子（_tick_throw_physics 调 throw_egg.update、_stop_physics 调
# throw_egg.end），控制器实现在 throw_egg.py；实测 4358。
# 2026-09-08 上调到 4369：批 G——change_scale 子肥鱼置位 user_customized（+4）、
# showEvent 启动即登记 runtime 标记（+7，含 try 兜底，修「退出子肥鱼」漏清未
# 拖动过的小肥鱼），实测 4369。
# 2026-09-11 上调到 4373：合入 origin/main（PR76 事件层合并同步）后实测 4373。
# 增量来自上游侧在 window.py 的气泡/告警触点调整（speech_bubble 拆出
# speech_bubble_text 后本文件仅保留薄调用）；window.py 分块拆分仍是待办，
# 拆分前预算只随实测校准，不靠压缩行宽硬塞。
# 2026-09-11 再上调到 4385：klxxya 的两处修复落地（#102 把「启动即同步可选服务」
# 的根因写在调用点注释里，+5 行；#101 在 _try_move 入口加探头会话位移闸门并补
# 注释，+7 行）。按维护者约定，klxxya 的修复可越过本红线：两处都是行为修复所必需
# 的守卫/注释，挪出 window.py 会切断控制流；拆分待办不变，预算只随实测校准。
# 2026-09-12 再上调到 4420：弹射卡顿修复批（实测 4411）——_on_anim_ended 飞行
# 循环/低速暖掷骰守卫、_enter_physics_mode 起飞预热与降速即时过渡、
# _stop_physics 落地回待机（实机卡顿定案：观测 26 次 >100ms 卡顿中 19 次为
# GUI 冷解码首帧）。全部是窗口生命周期内联守卫，拆控制器会切断与
# _switch/_pending_switch/回收推送的共享状态流；拆分待办不变。
# 2026-09-12 再上调到 4425：#109 探头/头槌体验三连修 + 气泡分页避头尾在同一批落地
# （window.py +13，实测 4425）。前者是探头旋转保持/软撞位移旁路/鱼头空中低速跟随的
# 内联守卫，与 #108 同属窗口生命周期共享状态，拆出去会切断 _enter_physics_mode 与
# 碰撞回写链；按维护者约定 klxxya 的修复可越过本红线，预算仍只随实测校准。
# 2026-09-12 再上调到 4429：#111 Windows 关机/注销 ffmpeg 0xc0000142 修复
# （window.py +4，实测 4429）——_pause_activity/_resume_activity 各加一句
# 「_closing 已置位则 return」守卫，防止会话结束后仍有路径触碰/复活 reader。
# 两处都是 3 行内联守卫，拆出去会切断 _pause_activity 与 _closing 共享状态流
# （更关键的是顺序语义：match_shutdown 必须**先**暂停再置 _closing，见
# pet/window_optional_services.py）；拆分待办不变，预算仍只随实测校准。
# 2026-09-13 再上调到 4478：肥鱼互撞卡顿修复批（window.py 净 +49，实测 4478）——
# _warm_landing_idles 从 GUI 线程同步 ffmpeg 首帧解码改为 daemon 线程预热
# （实测定案：碰撞风暴下每次撞飞堵 GUI ~100ms，看门狗连续抓 200ms+ 卡顿），
# 起飞/落地 pin-unpin 保护落地首帧不在飞行窗口被预热浪涌逐出（8MB 预算不动，
# 常驻内存零增长），增量行数几乎全是线程安全性/实机教训注释；这些守卫与
# _enter_physics_mode/_stop_physics 共享窗口状态流，拆控制器反而切断调用链，
# 按约定只校准预算。
# 2026-09-15 再上调到 4507：issue #98「点击桌宠导致全局复制粘贴失效」修复
# （window.py +29，实测 4507）——新增 _apply_windows_no_activate() 在 showEvent
# 置位 WS_EX_NOACTIVATE，使点击桌宠不再夺走前台/键盘焦点。原生样式操作本体放在
# pet/platform_win.py（+23，与 _set_windows_click_through 同处），window.py 侧只留
# 一个带完整实机取证说明的委托方法：它必须留在 PetWindow 上（showEvent 是唯一
# 可靠的"原生窗口已就绪/可能被重建"注入点，拆到独立模块反而会产生跨模块的
# 窗口生命周期耦合）。按约定只校准预算，不为达标压行。
# 2026-09-18 合并 #140 时按实测校准到 4605：#140 的碰撞稳定边界缓存（字段/切换复原/
# 素材替换作废/缩放清空）与 main 已含的 #137 Linux 贴边绘制补偿（虚拟位置 + 稳定
# 身体框）**叠加**后实测 4605——两边各自的预算都低于合并结果，是「红线是组合性质」
# 的又一实例（docs/PR-MERGE-LESSONS-2026-09-12.md 教训 2）。按文件约定只随实测校准，
# 不为达标压行/合并语句。
# 2026-09-19 上调到 4616：新增「气泡文字大小」（bubble_text_scale）注入——两处
# 配置读取（__init__ / _refresh_pet_settings 各 4 行：读配置 + getattr 守卫 +
# 调 set_text_scale）共 +11。getattr 守卫是必要的：测试替身（_BubbleStub 等）
# 不实现 set_text_scale，直接调用会把无关用例打红。缩放逻辑本身在
# pet/speech_bubble.py（该文件无行数预算）。按文件约定只随实测校准。
# 2026-09-20 上调到 4629：gap 池过滤双分类移动素材（+3：注释），修复
# _play_roll 移动分支被 gap 步触发的意外位移。实测 4629。
# 2026-09-20 上调到 4632：delete_when_idle 轮询定时器绑定 menu context（+3：
# 注释），窗口/菜单销毁后不再访问已删 C++ 对象。与 move-sync-facing 的 4629
# 叠加，组合实测 4632（docs/PR-MERGE-LESSONS-2026-09-12.md 教训 2 又一实例）。
# 2026-09-22 上调到 4635：崩溃消融回退（_rebuild_frame 零拷贝直取改回
# currentPixmap().toImage() 私有深拷贝，切断 窗口↔显示槽↔首帧缓存 别名面，
# Qt6Gui QRasterPaintEngine 三连崩排查，见 .scratch/single-overlay-window/
# HANDOFF.md 崩溃案），注释 +2 + currentPixmap→toImage 两行替代原一行直取（+3）。实测 4635。
# 2026-09-23 上调到 4638：rebase 合入上游 a7489ae（fix(self-talk): 点击自言自语
# 与周期气泡解耦 + 恢复点击侧设置可见，window.py +4/-1）。与本分支各修复无
# 组合冲突，叠加实测 4638（docs/PR-MERGE-LESSONS-2026-09-12.md 教训 2 常规校准）。
# 2026-09-23 上调到 4648：合入前评审修复——A1 冷 meta 闸门（_try_move 加
# duration 退化值守卫 + 注释，+9）与 A2 飞行加速按用户「播放速率」复合
# （表达式改写 + getattr 防御测试替身，+2）。净增为守卫与注释，未拆控制器
# （守卫必须贴着 _try_move 的建计划点才有效），按预算规则校准。实测 4648。
# 2026-09-23 上调到 4671：本批两个用户可见缺陷的窗口侧接线（+23/-0）——
# ① issue #186 多屏拖拽/抛掷：活动区域快照的取用与释放（__init__、拖拽起点、
#    _enter_physics_mode、_stop_physics、普通拖拽松手各一处；几何判定/钳制/抛掷
#    边界全在 pet/window_placement.py，物理 tick 里只读快照）；
# ② 托盘图标可用性：frame_ready 一次性信号（声明 + 首帧标志 + _rebuild_frame
#    里发一次；发射点用 getattr 兜底，兼容只挂载 _rebuild_frame 的假窗口）。
# 两处都必须贴着自己的控制流（拖拽/物理入口、帧构建尾部），拆控制器等于把同一条
# 时序切断；按预算规则校准到实测值，不为达标压行。实测 4671。
WINDOW_PY_LINE_BUDGET = 4671

# modern_settings_dialog.py 行数预算：按结构线拆分后实测 1857 行（拆分前 4811 行）。
# 主对话框 ModernSettingsDialog + 对话框装配/配置写回 + 为 pet/ 与 tests/ 保留的
# re-export 留守本文件；控件库 / 菜单布局编辑器 / AI 设置页 / 主题 QSS 已分别拆至
# settings_widgets / settings_menu_layout_editor / chat/ai_settings_page /
# settings_theme_qss。预算随实测校准（早期口径为「实测 + 50 行余量」，2026-09-17
# 起按实测值锁定，见下方逐次记录）；再往上帝类里塞新页面时只许降不涨。
# 2026-09-05 建立（perf/memory-footprint 拆分批）。
# 2026-09-06 上调到 1992：合入上游 main（PR73）带来动画预热开关等 +85 行
# （实测 1942），预算随实测校准。
# 2026-09-08 上调到 1996：新增「随桌宠启动 dsh 服务」开关行（+4，实测 1996）。
# 2026-09-08 上调到 2000：批 C 落种占位语义在保存路径加 user_customized 置位（+4，实测 2000）。
# 2026-09-08 上调到 2009：批 E 清除子肥鱼走 shell 已接线回调（+9，实测 2009）——
# _on_clear_spawned_pets 优先调 win.on_clear_spawned_pets（自带确认框与进程内
# 子窗前置于关闭），拿不到回调时回退原有确认+直接清理；按文件约定校准预算，
# 不为达标压缩行宽/合并语句。
# 2026-09-08 上调到 2018：批 G——「退出子肥鱼」按钮对子肥鱼禁用（+5）+
# _on_clear_spawned_pets 加 instance_id 双保险（+5，含注释折行），实测 2018。
# 2026-09-15 上调到 2270：语音报时设置页接入（页面实例化/SettingRow 收集/
# _write_config 写回/试听透传回调）与两开关、音色下拉改造，实测 2255；
# 按文件约定预算只随实测校准，不为达标压缩行宽/合并语句；拆分仍是待办。
# 2026-09-16 上调到 2300：合并上游展开式格式（当时整块 SettingsSection 从
# 紧凑单行改为 Black 风格展开，仅格式就 +100 行以上）叠加「音乐关联」分组
# （3 行 SettingRow）+「消费统计」一级分组，实测 2300；按文件约定预算只随
# 实测校准，不为达标压缩行宽/合并语句；拆分仍是待办。
# 本文件拆分仍是待办，拆分前预算只随实测校准。
# 2026-09-16 上调到 2300：#128「歌词/消费统计/音乐菜单」接入设置页，实测 2300。
# 2026-09-16 再上调到 2311：#127 节日提醒设置页（+11）与 #128 叠加后实测 2311——
# 这正是「红线是组合性质」：两个 PR 各自合并时 CI 都绿，合到一起才越线
# （见 docs/PR-MERGE-LESSONS-2026-09-12.md 教训 2）。按文件约定只随实测校准，
# 不为达标压缩行宽/合并语句；拆分仍是待办。
# 2026-09-17 上调到 2327：新增「语音」总域，并按主人定稿口径只收鱼开口说话
# （TTS）类设置——语音报时整组 12 行 + 节日提醒 12 行（原「自动化与联动」域，带
# speak/TTS 播报能力）；音效类回各自功能分组：点击音效 4 行回「互动 · 点击反馈」
# （click_ 前缀整组认领，与 HEAD 行为一致）、碰撞音效 2 行回「桌宠」碰撞组，Agent 提示音效
# （agent_sound_*）留在 Agent 联动折叠框内。实测 2327；按文件约定只随实测校准，
# 不为达标压缩行宽/合并语句；拆分仍是待办。
# 2026-09-17 上调到 2341：灵动岛图标下拉框新增「鱼本体头像（推荐）」项，原 10 个
# emoji 选项标签改中文（data 仍是 emoji）——设置页自己渲染 emoji 也会付同一笔
# DirectWrite 彩色字体栈税额（约 33MB）；标签逐项成对写，实测 2341。
# 2026-09-17 上调到 2371：设置页进程隔离（standalone）——__init__ 的 standalone
# 形参/属性、末尾接线 install_standalone_hooks、move_away_from_pet 的 runtime
# 避让分支、_on_voice_chime_preview 的本地试听分支、_write_config 注释共 +30；
# 试听/避让/节日演示的实现全在 pet/settings_standalone.py，本文件仍只做接线；
# 按文件约定预算只随实测校准，不为达标压缩行宽/合并语句；拆分仍是待办。
# 2026-09-19 上调到 2393：两批设置改动**组合**后的实测值——拖文件解读新增
# 「文件识别」域（对话框只做接线，实现全在 pet/settings_file_interpret.py）与灵动岛
# 「隐藏时对话气泡」开关（控件 +2、SettingRow +6、_write_config 回写 +1）各自
# 只按自己那批校准（2384 / 2380），合起来才是 2393：单个 PR 都不越线、只有两者
# 同时进才红——又一次「红线是组合性质」的实例（PR-MERGE-LESSONS 第 2 条），
# 故按文件约定只随实测校准，不为达标压缩行宽/合并语句；拆分仍是待办。
# 2026-09-19 上调到 2401：新增「气泡文字大小」设置行（bubble_text_scale，+8）——
# 3 行 SettingRow 展开式写法（6 行）+ _write_config 回写 1 行 + 布局编排认领 1 行；
# 控件本体落在 pet/settings_pet_controls.py（与既有的「配图大小」同处），
# 缩放实现全在 pet/speech_bubble*.py。按文件约定只随实测校准，不为达标压行。
# 2026-09-22 上调到 2419：点击台词朗读 / 台词自动预缓存 / 配图概率共 3 个 SettingRow
# 接入「互动」域已存在的两组（点击反馈 + 自言自语），逐项为 SettingRow 展开式写法、
# 随组显隐名单、归属 claim、_write_config 写回，实测 2419；控件本体落在
# pet/settings_pet_controls.py，朗读/预缓存实现全在 pet/self_talk_voice.py 与
# pet/window_alerts.py。同页同组连续加控件，拆出来只会把这组设置割成两半；
# 按文件约定只随实测校准，不为达标压行。设置页拆分仍是待办。
# 2026-09-22 上调到 2436：点击侧显隐解耦（+17）——点击自言自语不再依附周期气泡
# 总开关，因此点击侧另立一个 _update_click_self_talk_controls（含函数 docstring 与
# 两处接线），实测 2436。这是可用性修复（原实现让新开关在默认配置下整组隐藏），
# 不是新功能；分页/拆分后预算会随实测下调（见「互动」域分页改造）。按文件约定
# 只随实测校准，不为达标压行。
# 2026-09-22 上调到 2441：**组合越线**——#176（点击侧显隐解耦，2436）与
# #177（音乐播放器路径接线的 +5）各自合并时都在预算内，合到一起才越线，
# 又一次「红线是组合性质」的实例（docs/PR-MERGE-LESSONS-2026-09-12.md 教训 2）。
# 实测 2441；随后即由「互动」域分页 + 抽 pet/settings_interaction.py 大幅下调，
# 故这里只做一次性校准，不为达标压行。
# 2026-09-22 **下调到 2347**：兑现上一条的承诺——「互动」域整页搬进
# pet/settings_interaction.py（页内任务标签「点击与音效 / 自言自语」），
# 本文件净减 94 行（2441 → 2347）。这是本文件第一次**因拆分而下调**预算：
# 靠搬代码而不是压行宽解决预算，正是预算作为「绊线」的预期用法。
# 2026-09-23 上调到 2357：纯桌宠版岛隐藏死锁修复——新增
# _chat_feature_available() 构建变体判定助手（+12）并将「隐藏时对话气泡」
# SettingRow 改为按构建变体条件收录（净 +3，注释另计）：无 pet.chat 的
# 打包变体不再展示该死路开关（运行时回退在 pet/dynamic_island.py 的
# chat_available）。实测 2357；按文件约定只随实测校准，不为达标压行。
# 2026-09-23 上调到 2377：4.2c 后段 D12「一键退出子肥鱼」按拓扑分流（+20）——
# overlay 拓扑下子肥鱼是进程内 sprite，跨进程 taskkill 回退（child_pet_cleanup）
# 找不到目标、按钮静默失效，改为写指令文件由主进程消费；legacy 回退逐行不变。
# 拓扑判定/写指令/校验的实现全在零 Qt 的 pet/overlay_settings_command.py，
# 本文件只做一次分支与身份回填（slot_from_instance_id）。
# 2026-09-24 上调到 2391：合入上游更新页接线（更新页主体在 pet/update_settings.py）。
# 实测 2391；按文件约定只随实测校准，不为达标压行（拆分仍是待办）。
# 2026-09-27 上调到 2393：overlay 文案如实（省电模式 hint 改调 settings_pet_controls、
# 窗口级键「对所有桌宠生效」标注接线 +2 行）。
# 2026-10-06 上调到 2403：清空陪伴记忆按 clear() 返回值如实提示（失败弹告警而不是
# 一律「已清空」）+10 行；实测 2403，按文件约定只随实测校准（拆分仍是待办）。
# 2026-10-06 上调到 2408：SMTC 工单「视频被当歌曲」新增设置行「浏览器媒体会话
# 参与歌词」（行构造 1 行 + 保存回写 4 行，控件本体在 settings_pet_controls，
# 「音乐关联」域 claim 同步登记）。实测 2408；按文件约定只随实测校准，不为达标
# 压行（拆分仍是待办）。
# 2026-10-06 回调到 2407：该设置行改为 settings_music.browser_media_rows（受
# 平台条件约束、并入既有的 `] + settings_music.browser_media_rows(self),` 一行），
# 本文件净 −1 行（保存回写 4 行仍在）。实测 2407。
# 2026-10-06 仍为 2407（「恢复默认设置」入口，issue #220）：本文件只加接线——
# import 1 行 + 侧栏按钮构造 3 行 + `_close_after_external_write` 方法 7 行
# （关窗但跳过整表写回，flag 语义留在对话框内），确认框/重置实现全在
# pet/settings_reset_defaults.py。实测恰为 2407（= 当前预算，零余量）：本次不再
# 上调，按文件约定"只随实测校准"；若与其它在飞分支叠加越线，按先例带日期注释校准。
# 2026-10-07 上调到 2415（夜间整合分支：#220 恢复默认设置 +13、#219 静止放手下落
# +4、harness 自启 service 目标下拉项与宽度自适应净 +6，三分支同文件叠加）。
# 实测 2415；按文件约定只随实测校准（拆分仍是待办）。
# 2026-10-08 回调到 2423（#208+#209+#172 叠加后，#228 歌词批次把「浏览器媒体
# 会话参与歌词」的 4 行写回搬进 settings_music.save_source_settings 换成 1 行、
# 并入 foobar2000 歌词两行——净 −3）：免打扰 4 行 / 天气 2 行 / 网络信息 5 行，
# 实现分别在 pet/settings_quiet.py / settings_weather.py / settings_network.py /
# settings_music.py。实测 2423；按文件约定只随实测校准（拆分仍是待办）。
# 2026-10-08 再调到 2426（#172 对齐：菜单入口改运行时置灰——CONTEXT.md 的
# Runtime Disabled）：编排器状态列随开关显示「已停用」——`_sync_menu_action_states`
# +2 行（读开关）与开关接线 +1 行。实测 2426。
# 2026-10-07 上调到 2470（设置页信息架构第一刀，+44）：常规域「启动项（高级）」+
# 桌宠域「播放器路径与标题格式（高级）」两条高级组装配、桌面组件域把探测间隔拆进
# 「网络延迟检测（高级）」（含"全行被依赖隐藏的组整组收起"的 3 行）、_search_settings
# 补 `SettingsSection(advanced=True)` 自动展开分支与「命中行被前置开关隐藏」的说明
# 助手，另含 harness 自启目标与静止放手下落两处定稿文案（净行数含注释折行）。
# 2026-10-08 上调到 2495（设置页信息架构第二刀，+17）：桌宠域拆成两个页内标签
# （显示与音乐 / 动作与碰撞），两个 addTab 的装配骨架 + 任务口径注释；组定义行一字
# 未改（只是搬到各自标签的 page_content 里）。实测 2487。
# 2026-10-08 设置页第五刀（按区直找）批 1：恢复被域装配压平的旧页子分区（主动识屏
# 15 行拆 4 组、语音报时 / 节日提醒各 12 行拆 3 组；行键表 20 行）+「自动化与联动」
# 顶层改按下表顺序（删掉 15 行 insertWidget 倒插逻辑）+ page_content 支持已构造好的
# QWidget，实测 2495（= 第二刀的预算值，未上调）。
# 2026-10-08 上调到 2497（同刀批 2）：页内分区跳转条与组描述的接线——import 1 行 +
# 域装配末尾 `settings_page_guide.install(self)` 及其注释 2 行；主体（跳转条、流式
# 布局、SECTION_GUIDE 描述表）全在 pet/settings_page_guide.py，本文件只做接线。
# 2026-10-09 上调到 2501（bug 狩猎修复批，两批叠加）：即时音效开关的 reload 防陈旧
# 快照守卫（+7，B04）+ 子宠页读写路由的配置入口一行与 harness 置灰摘除（+3，F2）。
# 2026-10-14 上调到 2505（TTS provider 化）：语音报时与节日提醒不再按「一组行」收进
# 「语音」域（页面容器会散架、分组标题会丢），改由各自页面自建卡片交给本域——域装配
# 处多出一层容器、两次行序刷新与搜索折叠展开的接线（净 +4）。
# 2026-10-14 再上调到 2508：节日页的行从构造起就挂在页面自己的持有容器里（无父的
# SettingRow 一旦 setVisible 就是顶层窗口，会闪小窗），于是它们的行**也要**在域装配
# 前登记进 claimed，否则会掉进「待分类（开发期）」（+3）。
# 2026-10-16 上调到 2515（设置页外观打磨分批，本文件净 +14）：窗口图标两处接线（import
# +1、setWindowIcon 与注释 +3）、侧栏容器换成按字体算宽的 SettingsSidebarPane（-1，主体
# 在 pet/settings_widgets.py）与两处行距下调（配对注释 +5）、常规域的高级组装配（+1 与其
# 分层口径注释 +5）。控件本体、宽度计算与组描述分别落在 settings_widgets /
# settings_page_guide，本文件只留装配与接线。**同批第二轮把预算内余量用掉、回到 2515**：
# 「光标隐藏时自动穿透」从高级组移回「窗口与系统」、高级组只剩「直播捕获兼容」、默认最大
# 阅读宽度换成 CONTENT_READING_MAX_WIDTH（import +1）、开机自启的范围声明改由
# settings_pet_controls 的作用域 pass 拆成行内标注（-2），其余靠收掉几处重复注释对齐。
# 2026-10-16 上调到 2526（允许桌宠跨屏开关，本文件净 +11）：常规域「窗口与系统」
# 多一行（行装配 8 行 + claim 表加一个 id + 保存回写 2 行）。控件本体与两条说明
# 文案落在 settings_pet_controls（`CROSS_SCREEN_HINT` 与作用域登记），本文件只留
# 装配与接线；行内文案表仍是唯一来源。按文件约定预算只随实测校准，不为达标压缩
# 行宽/合并语句；拆分仍是待办。
# 2026-10-17 累计校准到 2535：main 上 PR267 落地后实测 2533（2526 + TTS provider 化的净 +7），
# 本支的每日一签接线再 +2，合并树实测正是 2535。抽签的页装配不在本文件——它作为「互动」
# 域第四个页内标签由 settings_interaction 挂载（与天气同口径），避免这个"上帝对话框"再涨。
# 仍按文件约定只随实测校准。
# 2026-10-18 上调到 2539（待办语音播报，本文件净 +4）：待办提醒分区多一行
# `SettingRow("todo_reminder_speak", ...)`（行装配 3 行 + 保存回写 1 行；claim 表那行
# 只是多一个 id，不增行）。控件本体在 settings_pet_controls，文案只有行内这一份来源。
# 仍按文件约定只随实测校准，不为达标压缩行宽/合并语句；拆分仍是待办。
# 2026-10-18 上调到 2561（自言自语内容项显隐解耦，本文件净 +22）：周期气泡的细项
# 显隐拆成「节奏项只跟周期开关」+「内容项（显示时间/候选内容/图片目录/配图概率）
# 跟周期或点击任一开启」——新增 `_update_self_talk_content_controls`（含 docstring）
# 与 `_update_self_talk_controls` / `_update_click_self_talk_controls` 两处调用的接线，
# 行数即这两个函数的净增。显隐规则必须贴着这两处开关的 toggled 接线（拆成外部函数
# 反而把"哪一档跟哪个开关"这条唯一契约切散），故按文件约定校准到实测值。
# 2026-10-18 上调到 2563（互动域新增「快捷键」页内标签，本文件净 +2：一行 import
# + 保存路径一行 `save_hotkey_settings`）。行的装配在 settings_hotkeys（新模块），
# 本文件只留接线——这是既定口径（settings_quiet/weather/divination 同形）。
# 2026-10-18 上调到 2580（Ctrl+F 聚焦搜索框 + 搜索索引纳入「更新」页作者行，本文件
# 净 +17：两条 import、`_focus_search_box` 槽与其接线、索引来源多一类候选）。
# 2026-10-19 上调到 2594（搜索框 ↑/↓ 逐条翻命中，本文件净 +14：`_search_settings`
# 改成 `advance: int` 步长并补 docstring、`eventFilter` 四键映射）。这一改只动键盘
# 交互，没有新页面/新控件组可拆，按「拆不动则校准到实测」处理。
# 2026-10-19 上调到 2600（同一处再 +6：`eventFilter` 加修饰键闸门——只拦裸键/小键盘键，
# 带 Ctrl/Alt/Shift 的 ↑↓ 透传，避免抢走输入法候选导航）。仍是键盘边界，同上处理。
# 2026-10-20 上调到 2606：新增「角色与素材」页。**页面本体拆到
# pet/character_packs_page.py**（按本文件预算注释的规矩：新页面不许在上帝类里堆代码），
# 这里只加 6 行接线（import + _add_page + 3 行注释）。实测 2606。
# 2026-10-21 上调到 2620：新增「功能」域（功能模块启用/停用卡片）。**页面本体拆到
# pet/settings_features.py**，本文件净 +14：`_features_page` 初始化与构造接线、
# `domain_content` 一行、`_write_config` 末尾统一 save 前的 staged 写回委托。
# 实测 2620。
# 2026-10-21 上调到 2664：期 3 模块门控（voice 语音闸 + music 音乐闸）叠加后实测
# 2664。**前一笔已达 2653（期 3a voice 提交未同步校准，预算从 2637 起就红了）**，
# 本次一并校准；差值来自「按激活挂出/跳键」的分支行（每组 3~5 行，无新增页面）。
# 按文件约定只随实测校准，不为达标压行（拆分仍是待办）。
MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET = 2721  # 2026-10-21：k3 终审 F2 BACKLOG 注释（deleteLater C++ 连坐）+5


def _read(name: str) -> str:
    return (PET_DIR / name).read_text(encoding="utf-8")


def test_pure_logic_modules_do_not_import_qt():
    # 节日提醒的纯逻辑/纯数据模块同样必须零 Qt（2026-09-16 加入，随功能一起
    # 把"纯逻辑层零 Qt"从约定升级为机器化守卫；festival_service/festival_settings
    # 不在本列——前者属服务层、后者属 UI 层，本就不受此约束）。
    # 2026-09-23 加入 overlay_settings_command：D12 指令通道的写/读/消费与拓扑门
    # 必须零 Qt——独立设置进程（pet/__main__.py --settings）禁止导入 pet.app/
    # overlay_shell，env 读取的唯一实现只能落在两侧都能 import 的轻模块里。
    # 2026-10-07 加入 net_monitor：网络信息（issue #172）的采样核心（计数器/
    # 延迟探测/文案）必须零 Qt——它跑在后台采样线程里，GUI 侧只读快照；
    # network_info.py 是弹窗的数据层（属地解析/连通性/文案），同样零 Qt。
    for name in (
        "collision.py", "physics.py",
        "festival/calendar.py", "festival/data.py", "festival/__init__.py",
        "festival/quotes_cn.py", "festival/quotes_west.py",
        "festival/quotes_west_movie.py", "festival/quotes_west_game.py",
        "festival/quotes_west_song.py", "overlay_settings_command.py",
        # 「回收内存」(#210)：过滤真值表与 ctypes 薄封装必须零 Qt——后台线程里
        # 跑的就是这一层，单测也在无 Qt 的替身上验（ctypes 全 mock）。
        "memory_trim.py",
        "net_monitor.py", "network_info.py",
        # 2026-10-07 加入 music_title：标题清洗/解析是取词链路的前处理，规则要能
        # 脱离 Qt 单独跑真值表（误伤防护的用例都是纯字符串）。
        "music/title.py",
        # 2026-10-07 加入 island_segments/sys_monitor（灵动岛段契约 P1）：段注册
        # 契约与 CPU/内存采样核心必须零 Qt——采样跑在后台线程，测宽靠调用方
        # 注入的 QFontMetrics（依赖注入，本模块不 import PySide6）。
        # 2026-10-07 加入 island_layout（P1 Slice D2 下沉）：段数据/落位/收缩流水线
        # 是纯几何，度量对象同样注入——收缩逐级触发/逆序恢复这些规则要能在没有
        # 窗口、没有真实字体、没有屏幕的替身上直接验，就必须零 Qt。
        "island/segments.py", "sys_monitor.py", "island/layout.py",
    ):
        src = _read(name)
        assert "PySide6" not in src, f"{name} 引入了 Qt 依赖，破坏纯函数层定位"


def test_decode_fanout_does_not_depend_on_window_or_player():
    src = _read("decode_fanout.py")
    for banned in ("pet.window", "pet.webm_clip", "from .window", "from .webm_clip",
                   "import window", "import webm_clip"):
        assert banned not in src, f"decode_fanout 反向依赖 {banned}，破坏单向依赖"


# TTS provider 层（pet/tts/）的机器化守卫（2026-10-17 加入）：provider 是可被第三方
# 照抄的模板（docs/ADDING-A-TTS-PROVIDER.md），两条红线写在 pet/tts/base.py 的模块
# 文档里——① 顶层不得 import Qt；② 可选/重量级依赖（edge_tts、aiohttp…）只能延迟到
# synth() 里 import。违反的代价是「每个用户白付这笔 import」或「无 Qt 环境测不了」，
# 所以这里把它守住：只扫模块顶层（不含函数体内的延迟 import）。
_PET_TTS_TOP_LEVEL_BANNED = (
    "PySide6", "PyQt",            # provider 是纯逻辑 + 一层 IO，不许碰 Qt
    "edge_tts", "aiohttp", "requests",  # 可选/重量级依赖：必须延迟到 synth() 内
)


def test_tts_providers_keep_top_level_imports_light():
    providers = sorted((PET_DIR / "tts").glob("*.py"))
    assert providers, "pet/tts/ 下应当有 provider 模块（别把守卫扫空了还不自知）"
    offenders: list[str] = []
    for path in providers:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.startswith(("import ", "from ")):
                continue  # 只扫顶层：函数体内的延迟 import 缩进了，不在此列
            if any(banned in line for banned in _PET_TTS_TOP_LEVEL_BANNED):
                offenders.append(f"pet/tts/{path.name}:{number}: {line.strip()}")
    assert not offenders, (
        "pet/tts/*.py 顶层引入了 Qt 或重量级/可选依赖（见 pet/tts/base.py 的写 provider 红线）：\n"
        + "\n".join(offenders)
    )


# 多进程多宠退役层（PHASE4_DESIGN.md §3 T6 清单，4.4a 停用 / 4.4b 删除）。
# 4.4a 机器化守卫：**overlay 新路径**（单合成窗渲染面）必须零 import 这些
# 模块——新架构的多宠碰撞/身份/生命周期全部在进程内 sprite 世界自足，
# 一旦回潮就是"新路径又骑回多进程 IPC/文件锁"的架构倒退。
# 4.4b 追加守卫：这些模块文件本身必须**不存在**（删除刀不许被静默回退），
# 且全 pet/ 树（不只新路径）不得再 import 它们。
RETIRED_MULTIPROCESS_MODULES = (
    "collision_ipc",
    "collision_codec",
    "collision_client",
    "collision_debug",
    "instance_launcher",
    "child_pet_cleanup",
)


def test_retired_multiprocess_layer_files_are_gone():
    """4.4b 机器化守卫：退役层文件必须不存在（防"删了又被合回来"）。"""
    present = [name for name in RETIRED_MULTIPROCESS_MODULES
               if (PET_DIR / f"{name}.py").exists()]
    assert not present, (
        "多进程多宠退役层文件回潮（T6 清单已删除）：\n" + "\n".join(present)
    )


def test_pet_tree_has_zero_retired_layer_imports():
    """4.4b 机器化守卫：整个 pet/ 树（含 legacy 路径）零 import 退役层模块。

    与上一条互补：文件不存在是"删干净"，本条约住"任何模块都不得再 import"
    （含函数级延迟 import 与 from pet.xxx 绝对写法）。
    """
    offenders = []
    for path in sorted(PET_DIR.rglob("*.py")):
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not (stripped.startswith("from ") or stripped.startswith("import ")):
                continue
            for retired in RETIRED_MULTIPROCESS_MODULES:
                if re.search(rf"\b{retired}\b", stripped):
                    offenders.append(f"{path.name}:{lineno}: {stripped}")
    assert not offenders, (
        "pet/ 内仍有 import 多进程多宠退役层（架构倒退）：\n"
        + "\n".join(offenders)
    )


# 死面 facade 退役（2026-10-09，bug 狩猎 F4 清理）：config_domains 是「只建不用」
# 的配置域 facade——五个 ``Config.*_config`` 方法全仓零产品调用方（只有它自己的
# 测试在用），留着只会让人按假象面改代码踩空。整层删除后按退役层先例守卫：
# 文件必须不存在，且全 pet/ 树不得再 import——防「死面被静默合回」。
RETIRED_DEAD_FACADE_MODULES = ("config_domains",)


def test_retired_dead_facade_files_are_gone():
    """死面 facade 机器化守卫：文件必须不存在（防"删了又被合回来"）。"""
    present = [name for name in RETIRED_DEAD_FACADE_MODULES
               if (PET_DIR / f"{name}.py").exists()]
    assert not present, (
        "死面 facade 文件回潮（2026-10-09 已整层删除）：\n" + "\n".join(present)
    )


def test_pet_tree_has_zero_dead_facade_imports():
    """死面 facade 机器化守卫：整个 pet/ 树零 import（含函数级延迟 import）。"""
    offenders = []
    for path in sorted(PET_DIR.rglob("*.py")):
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not (stripped.startswith("from ") or stripped.startswith("import ")):
                continue
            for retired in RETIRED_DEAD_FACADE_MODULES:
                if re.search(rf"\b{retired}\b", stripped):
                    offenders.append(f"{path.name}:{lineno}: {stripped}")
    assert not offenders, (
        "pet/ 内仍有 import 死面 facade（2026-10-09 已整层删除）：\n"
        + "\n".join(offenders)
    )


# 旧版右键菜单退役层（2026-10-08 全量退役，见
# docs/PR-REPORT-LEGACY-MENU-RETIREMENT-2026-10-08.md）。
# 守卫分三层，同多进程层先例：
#   ① 退役文件必须**不存在**（删除刀不许被静默回退）；
#   ② 整个 pet/ 树不得再 import 退役模块；
#   ③ 退役符号（旧版建造器 / 旧模板装载 / 模板切换）不得在任何一行重新出现——
#      包括函数体、字符串与注释：这些符号一旦回潮，就意味着"又有一棵菜单树按
#      模板分流"，正是本次删掉的东西。
# 退役配置键另有一条更精确的守卫（test_retired_menu_template_key_confined_to_migration）：
# 它必须还能被**迁移**引用一次（丢旧值），但不许再被任何消费方读/写。
RETIRED_LEGACY_MENU_PATHS = (
    "context_menus/legacy.py",
    "context_menus/menu_styles/legacy.py",
    "menu_templates/legacy.json",
    "menu_templates/modern.json",
)
RETIRED_LEGACY_MENU_MODULES = (
    "context_menus.legacy",
    "menu_styles.legacy",
)
RETIRED_LEGACY_MENU_SYMBOLS = (
    "build_legacy_menu",
    "apply_legacy_menu_style",
    "load_menu_template",
    "normalize_template_id",
    "add_template_switch",
)
#: 已退役的右键菜单模板键：只允许出现在 config.py 的迁移函数里。
RETIRED_MENU_TEMPLATE_KEY = "context_menu_template"
RETIRED_MENU_TEMPLATE_MIGRATION = "pet/config.py::_migrate_menu_template_config"


def test_retired_legacy_menu_files_are_gone():
    """退役的旧版菜单文件必须不存在（防"删了又被合回来"）。"""
    present = [rel for rel in RETIRED_LEGACY_MENU_PATHS
               if (PET_DIR / rel).exists()]
    assert not present, (
        "旧版右键菜单退役层文件回潮（已全量删除）：\n" + "\n".join(present)
    )


def _function_block(source: str, name: str) -> tuple[int, int, str]:
    """按缩进切出一个方法体（返回 (起始行号, 结束行号, 文本)）。"""
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"    def {name}"))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("    def ") or lines[i].startswith("    @"):
            end = i
            break
    return start, end, "\n".join(lines[start:end])


def test_retired_menu_template_key_confined_to_migration():
    """退役的模板键在 pet/ 里只许存活于迁移函数内（丢旧值的那一处）。

    这是"读入即弃"的机器判据：别处再出现这个键，就等于又有一个消费方读它
    （配置重新分叉）；迁移函数里出现则是必须的——旧存档得有个地方丢掉它。
    """
    config_src = (PET_DIR / "config.py").read_text(encoding="utf-8")
    _, _, block = _function_block(config_src, "_migrate_menu_template_config")
    assert RETIRED_MENU_TEMPLATE_KEY in block, (
        f"{RETIRED_MENU_TEMPLATE_MIGRATION} 必须仍丢弃这个键（旧存档的去向）")
    outside = [(no, line) for no, line in enumerate(config_src.splitlines(), 1)
               if RETIRED_MENU_TEMPLATE_KEY in line]
    block_start, block_end, _ = _function_block(config_src, "_migrate_menu_template_config")
    offenders = [
        f"pet/config.py:{no}: 退役键出现在迁移函数之外"
        for no, _line in outside
        if not (block_start + 1 <= no <= block_end)
    ]
    for path in sorted(PET_DIR.rglob("*.py")):
        if path.name == "config.py":
            continue
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(rf"\b{RETIRED_MENU_TEMPLATE_KEY}\b", line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "退役的右键菜单模板键回潮（只许在迁移函数里丢旧值）：\n"
        + "\n".join(offenders)
    )


def test_pet_tree_has_zero_retired_legacy_menu_references():
    """整个 pet/ 树零退役菜单符号：谁再把旧版建造器/模板装载/模板键写回来，
    这里就红——比"文件不存在"更严，挡住"换个文件名复活"与"注释里留老路"。"""
    offenders = []
    for path in sorted(PET_DIR.rglob("*.py")):
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            for retired in RETIRED_LEGACY_MENU_SYMBOLS:
                if re.search(rf"\b{retired}\b", line):
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
            stripped = line.strip()
            if not (stripped.startswith("from ") or stripped.startswith("import ")):
                continue
            for retired in RETIRED_LEGACY_MENU_MODULES:
                if re.search(rf"\b{re.escape(retired)}\b", stripped):
                    offenders.append(f"{path.name}:{lineno}: {stripped}")
    assert not offenders, (
        "pet/ 内仍有旧版右键菜单退役层引用（架构倒退）：\n"
        + "\n".join(offenders)
    )


def _overlay_new_path_modules() -> tuple[str, ...]:
    """新路径模块清单：overlay_* / sprite_* / pet_sprite / tick_* / island_bridge。

    按目录枚举（而不是硬编码文件名）——新路径新增模块自动纳入守卫，
    不会因为"忘了加名单"漏掉一次回潮。
    """
    patterns = ("overlay_*.py", "sprite_*.py", "tick_*.py")
    names = {"pet_sprite.py", "island/bridge.py"}
    for pattern in patterns:
        names.update(p.name for p in PET_DIR.glob(pattern))
    return tuple(sorted(names))


def test_overlay_new_path_has_zero_retired_layer_imports():
    """4.4a 停用刀：新路径零 import 多进程多宠退役层（T6）。"""
    offenders = []
    modules = _overlay_new_path_modules()
    assert "overlay_shell.py" in modules and "sprite_collision.py" in modules, (
        "新路径模块枚举失效（目录变动？），守卫清单必须非空"
    )
    for name in modules:
        for lineno, line in enumerate(_read(name).splitlines(), 1):
            stripped = line.strip()
            if not (stripped.startswith("from ") or stripped.startswith("import ")):
                continue  # 只查 import 语句，注释里的历史提及不算回潮
            for retired in RETIRED_MULTIPROCESS_MODULES:
                if re.search(rf"\b{retired}\b", stripped):
                    offenders.append(f"{name}:{lineno}: {stripped}")
    assert not offenders, (
        "overlay 新路径 import 了多进程多宠退役层模块（架构倒退）：\n"
        + "\n".join(offenders)
    )


def test_window_private_surface_frozen():
    """S2 收口成果：window 私有成员跨模块访问在以下文件中必须保持零命中。"""
    pattern = re.compile(r"(?:win|pet|window)\._[a-z]")
    offenders = []
    for rel in ("app.py", "agent_link/__init__.py"):
        for lineno, line in enumerate(_read(rel).splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    for path in sorted((PET_DIR / "context_menus").glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"context_menus/{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "window 私有面回潮：\n" + "\n".join(offenders)


# —— 行数预算的意图与使用约定（给所有贡献者，含 AI 驱动）——
# 为什么有预算：window.py / modern_settings_dialog.py 这类文件膨胀到几千行后，
# 人和 AI 都难读难改、定位问题成本高、合并冲突频发。预算只是倒逼拆分的
# 「绊线」，本身不是目的，更不是红线。触发时的正确动作按优先级：
#   1. 首选：把新内容拆到独立模块/控制器（window.py 见 docs/WINDOW_PY_SPLIT_GUIDE.md）；
#   2. 实在拆不动或强拆更伤可读性时：把预算常量校准到新实测值，注明日期+理由，
#      并在 PR 里说明（超一点没关系，说清楚就行）。
# 禁止的反向优化：靠压缩行宽/合并语句/删注释把行数硬塞回预算内——
# 那比超预算本身更伤维护性。宁可校准预算，不要压行。

def test_window_py_line_budget():
    lines = len(_read("window.py").splitlines())
    assert lines <= WINDOW_PY_LINE_BUDGET, (
        f"window.py 涨到 {lines} 行（预算 {WINDOW_PY_LINE_BUDGET}）。"
        "预算是防膨胀的绊线（文件太大则难读难改、合并冲突多、问题定位难），"
        "不是红线：新功能优先拆对应控制器（docs/WINDOW_PY_SPLIT_GUIDE.md）；"
        "拆不动可把预算校准到新实测值（带日期注释）并在 PR 说明理由。"
        "请勿为达标压缩行宽/合并语句——那是反向优化。"
    )


def test_modern_settings_dialog_py_line_budget():
    lines = len(_read("modern_settings_dialog.py").splitlines())
    assert lines <= MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET, (
        f"modern_settings_dialog.py 涨到 {lines} 行（预算 {MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET}）。"
        "预算是防膨胀的绊线（文件太大则难读难改、合并冲突多、问题定位难），"
        "不是红线：新页面/新控件组优先拆出（控件库/布局编辑器/AI 设置页/"
        "主题 QSS 已是先例）；拆不动可校准预算到新实测值（带日期注释）并说明理由。"
        "请勿为达标压缩行宽/合并语句。"
    )


def test_modern_settings_dialog_no_top_level_chat_import():
    """no-chat 打包变体 excludes=['pet.chat']：modern_settings_dialog 顶层若直接
    import pet.chat.*（如 ai_settings_page），产物运行时点「桌宠设置」会在模块
    导入期抛 ModuleNotFoundError，导致设置界面整体打不开（测试环境因 pet.chat
    齐全而全绿，属打包专属回归）。chat 依赖必须延迟到 include_ai 分支内的
    函数级 import，no-chat 时 include_ai=False 不触发。"""
    offenders = []
    for lineno, line in enumerate(_read("modern_settings_dialog.py").splitlines(), 1):
        if line[:1].isspace():
            continue  # 仅检查模块顶层（无缩进）import；函数内延迟 import 合法
        stripped = line.strip()
        if (stripped.startswith("from .chat")
                or stripped.startswith("from pet.chat")
                or stripped.startswith("import pet.chat")
                or stripped == "from . import chat"):
            offenders.append(f"modern_settings_dialog.py:{lineno}: {stripped}")
    assert not offenders, (
        "modern_settings_dialog 顶层 import pet.chat 回潮：no-chat 打包变体"
        "（excludes=['pet.chat']）运行时设置界面会打不开。chat 依赖须延迟到"
        " include_ai 分支内的函数级 import。\n" + "\n".join(offenders)
    )


def test_settings_widgets_orphan_cluster_guard():
    """孤儿簇（批6-7 拆分被上游合并静默回退的死文件）防再发：settings_widgets.py
    与 settings_styles*.qss 不允许「存在且零引用」态——要么已删除，要么被
    pet/ 内某模块 import/读取（read_text/QFile）。"""
    targets = (
        "settings_widgets.py",
        "settings_styles.qss",
        "settings_styles_dark.qss",
        "settings_styles_dark_browser.qss",
    )
    py_sources = [p.read_text(encoding="utf-8") for p in PET_DIR.rglob("*.py")]
    for name in targets:
        path = PET_DIR / name
        if not path.exists():
            continue  # 已删除：允许的终态
        needle = name[:-3] if name.endswith(".py") else name
        assert any(needle in src for src in py_sources), (
            f"{name} 存在但零引用——批6-7 孤儿簇回退态，须删除或恢复接线"
        )
