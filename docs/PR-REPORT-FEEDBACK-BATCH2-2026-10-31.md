# 用户反馈批次 2：聊天窗 / 语音 / 直播 / 设置搜索 / DSH / 人格 / 会话 / 自语

> **基线**：`3af70bd6`（反馈批次 1 安装包验收提交）
> **分支**：`feat/petcore-vpet-architecture`　**日期**：2026-10-31
> **范围**：60 个已跟踪文件（+3792 / −257）+ 9 个新文件
> **关联**：[`PR-REPORT-USER-FEEDBACK-BATCH-2026-10-30.md`](PR-REPORT-USER-FEEDBACK-BATCH-2026-10-30.md)

## 一、核心特性

本批把用户十项反馈落成可验收的行为：聊天输入区不再被撑高、拼音组合时 placeholder
消失；音乐识别可选输出设备并跟随系统默认；直播兼容新增「色键窗口」模式与 GDI
捕获自检；设置搜索能定位动画树/下拉项/折叠的 mod 页；DSH 审批卡有终态、单连接、
超时与断开恢复；语音输入在录音中实时预览且不覆盖手打内容；人格卡可新建/覆盖/
改名/恢复；会话侧栏按日期分节、可折叠、可归档/删除；AI 三项收进「AI 助手」二级
菜单；自言自语新增每日一句与连说。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 聊天输入区 | stretch 改到正确的 `ai_layout`；composer 固定自然高度；短会话底部对齐；preedit 隐藏 placeholder |
| 2 | 音频设备 | `music_audio_device` 跟随/手选输出端点；`asr_device` 麦克风下拉；端点列表异步读取 |
| 3 | 直播兼容 v2 | 关闭/透明顶层/色键三模式；色键不透明铺底；置顶用 `SetWindowPos` 不重建 HWND；捕获自检 |
| 4 | 设置搜索 | 树项/下拉项/列表项/折叠 mod 页/`searchKeys` 注册；只定位不改设置值 |
| 5 | DSH 审批 | 审批卡状态机、同 id 去重、单 SSE `DshHub`、`close_active`、通道 10 分钟超时 |
| 6 | ASR 实时 | `peek_wav()` + `partial` 信号；预览镜像不覆盖用户文本；sapi ≥1.5s |
| 7 | 人格卡 | 6 个预设扩写；用户目录覆盖/新建/复制/重命名/删除/恢复；runtime 按 mtime 缓存 |
| 8 | 会话分节 | 今天/昨天/本周/本月/更早；组头折叠；三点菜单归档/删除；已归档开关 |
| 9 | 菜单 | `ai_hub`「AI 助手」收 AI 对话/看看屏幕/主动识屏；migrate_p12 幂等 |
| 10 | 自语每日 | 默认 7 句候选 + 12 句每日池；日期确定性；连说复用 `say_sequence` |

**红线 / 不变量**

- `petcore/engine/` 仍零 Qt；mod 不 import 网络库；密钥不进 `config.json`。
- 直播模式仍是普通顶层 `Window`，清 `WS_EX_TOOLWINDOW/NOACTIVATE`；不改渲染栈。
- 设置搜索**不是设置修改**：下拉/列表只滚动定位，不改变当前值。
- 会话 `schema` 仍为 1；旧文件缺 `archived_at` = 未归档。
- 人格卡只写用户数据目录，包内预设只读；恢复内置只删覆盖文件。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/ui/chat/window.py` | +152 / −22 | stretch 改 `ai_layout`；composer 固定高度；消息顶部 spacer 底部对齐；侧栏组头/折叠/已归档/三点信号；DSH hub/partial 接线 |
| `petcore/ui/chat/widgets.py` | +114 / −5 | preedit 隐藏 placeholder；partial 预览镜像/清空；`SessionListRow` 三点菜单归档/删除 |
| `petcore/ui/chat/controller.py` | +92 / −2 | 归档/删除/回退会话；已归档列表过滤；DSH hub 创建；ASR partial 转发 |
| `petcore/ui/chat/quick.py` / `quick_controller.py` | +35 / −0 | 快速对话 partial 预览与最终文本；不覆盖手打内容 |
| `petcore/ui/chat/dsh_view.py` | +128 / −19 | 审批卡状态机、去重/上限、404/断线恢复、共享 hub 接线 |
| `petcore/ui/chat/dsh_controller.py` | +93 / −7 | `DshHub` 单连接扇出；generation guard；stop 时 close_active |
| `petcore/net/dsh.py` | +29 / −0 | 记录/关闭在飞 SSE 响应，停止立即解除阻塞 |
| `petcore/ui/chat/icons.py` / `styles.py` | +18 / −0 | more 图标；三点/已归档/archive 按钮 QSS |
| `petcore/chat/session_store.py` | +17 / −1 | `archived_at`、`list_sessions(include_archived=)`、`archive()` |
| `petcore/chat/history_buckets.py`（新） | 新文件 | 零 Qt 日期分桶（今天/昨天/本周/本月/更早） |
| `petcore/config.py` | +74 / −1 | 新增音频/直播/ASR 实时/自语每日键；色键归一化强制关系 |
| `petcore/menu_templates/petcore-default-v1.json` / `ui/menu_layout.py` | +108 / −6 | AI 助手子菜单；migrate_p12 幂等迁移；不在精简布局注入空 hub |
| `petcore/ui/pet_window.py` | +93 / −18 | 色键不透明窗/画底色；标题带 slot；exstyle 重放；置顶不重建 HWND；色键颜色 setter |
| `petcore/ui/bubble.py` | +37 / −4 | 色键气泡不透明铺底；`set_stream_mode(keycolor,key_color)` |
| `petcore/platform/win.py` | +49 / −0 | `window_exstyle` / `set_topmost` |
| `petcore/platform/capture_probe.py`（新） | 新文件 | PrintWindow/BitBlt 非黑占比 + exstyle 报告 |
| `petcore/ui/settings/base.py` | +195 / −39 | 树/下拉/列表搜索定位；`searchKeys` 注册；页内标签切换 |
| `petcore/ui/settings/page_extensions.py` | +2 / −0 | 搜索命中折叠组自动展开 |
| `petcore/ui/settings/page_voice.py` | +75 / −0 | 麦克风下拉；实时预览开关/间隔；ASR 键 snapshot/回滚 |
| `petcore/ui/settings/pages_core.py` | +150 / −16 | 直播三选一/色键颜色/捕获自检；ASR 音频键 snapshot/回滚 |
| `petcore/ui/asr_channel.py` | +50 / −1 | `partial` 信号与增量识别循环 |
| `petcore/platform/mic.py` | +19 / −0 | `peek_wav()` 录制中取 PCM |
| `examples/petcore-mods/media.music/host/audio_sessions.py` | +176 / −16 | 端点枚举/默认 id/按设备重建 manager/短缓存 |
| `examples/petcore-mods/media.music/host/runtime.py` | +42 / −1 | 跟随默认 5s/手选 30s 校验/回退 |
| `examples/petcore-mods/media.music/host/settings_page.py` | +123 / −1 | 音频输出设备下拉 + 刷新 + 诊断行 |
| `examples/petcore-mods/ai.chat/host/persona_presets.py` | +167 / −25 | `PersonaStore` 用户卡/覆盖/校验/原子写 |
| `examples/petcore-mods/ai.chat/host/settings_page.py` | +263 / −33 | 人格卡 UI 六按钮/名称栏/提示词保存/恢复/删除 |
| `examples/petcore-mods/ai.chat/host/runtime.py` | +31 / −1 | 合并用户卡并按 mtime 缓存 |
| `examples/petcore-mods/ai.chat/persona_presets/*.json` | +12 / −12 | 6 个预设扩写为多段 |
| `petcore/builtin/features/fun.self-talk/host/factory.py` | +231 / −3 | 每日句/连说/状态文件/设置页/搜索键 |
| `petcore/builtin/features/fun.self-talk/host/daily.py`（新） | 新文件 | 日期种子/切段/状态读写 |
| `integrations/dsh-pet-channel/index.js` | +28 / −1 | 同会话单 pending、10 分钟审批超时、ping 报超时配置 |
| `petcore/ui/app.py` | +21 / −2 | 直播 keycolor/颜色下发；兼容缺少 setter 的窗口 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_chat_ui.py` | +143 / −7 | composer 紧凑/底部对齐、preedit、partial 不覆盖、日期分节/折叠/归档三点 |
| `tests/test_petcore_settings_search.py`（新） | 新文件 | 树项展开/下拉不改值/页内标签/`searchKeys` |
| `tests/test_petcore_animation_policy.py` | +27 / −0 | 动画树项可搜可定位 |
| `tests/test_petcore_extensions_nav.py` | +42 / −0 | 搜索切到折叠的 mod 页 |
| `tests/test_petcore_dsh.py` | +108 / −0 | 审批终态/去重/失败恢复/共享单连接 |
| `tests/test_petcore_music_audio_device.py`（新） | 新文件 | 端点选择缓存键/手选回退/默认切换/采样参数 |
| `tests/test_petcore_music_mod.py` | +19 / −0 | 音频设备下拉 round-trip |
| `tests/test_petcore_asr.py` | +49 / −0 | `peek_wav` 不停止、partial→final |
| `tests/test_petcore_ai_persona.py` | +128 / −3 | 预设扩写、Store 覆盖/恢复/删除/校验、UI 新建/改名/删除、搜索 |
| `tests/test_petcore_sessions.py` | +29 / −0 | 归档 round-trip/旧文件兼容 |
| `tests/test_petcore_history_buckets.py`（新） | 新文件 | 日历边界/排序/空桶 |
| `tests/test_petcore_self_talk_daily.py`（新） | 新文件 | 同日稳定/跨天变化/状态/连说/设置页 |
| `tests/test_petcore_stream_mode.py` | +43 / −2 | 色键不透明/角落颜色/开关持久化/标题 slot |
| `tests/test_petcore_capture_probe.py`（新） | 新文件 | 非黑占比/平台门/配置归一化 |
| `tests/test_petcore_settings.py` | +21 / −0 | 直播三选一与取消回滚 |
| `tests/test_petcore_settings_voice.py` | +8 / −0 | 麦克风/实时/间隔写入与回滚 |
| `tests/test_petcore_menu_layout.py` / `ai_menu_registration.py` | +61 / −10 | p12 幂等/不注入空 hub/子菜单顺序/旧 id 隐藏 |

## 三、实现要点

- **聊天布局**：根因是 `_update_conversation_height()` 对 `chat_main_layout` 调
  `setStretchFactor`，而 scroll/composer 在 `ai_layout`——Qt 静默 no-op，剩余高度被
  `QFrame#composer-toolbar` 吸收。修成正确 layout + 固定 size policy；消息区顶部
  Expanding spacer 让短会话贴输入区，长会话时自动收缩。
- **placeholder**：Qt 只在文档为空时画 placeholder，preedit 不进文档；在
  `InputMethod` 事件里按 `preeditString()` 清/恢复，`FocusOut` 兜底。
- **音频**：COM 接口线程亲和；manager 缓存键是 `(线程, 设备 id)`，换设备立即重建。
  默认端点每 2 秒短缓存、runtime 每 5 秒比较；手选设备每 30 秒确认存在。
- **直播**：色键窗口用不透明 QWidget 整窗填色，OBS 加色度键；透明窗口仍保留。
  `setWindowFlags` 会重建 HWND，直播模式置顶改用 `SetWindowPos`；每次改旗标后
  重放 `apply_stream_window`，修掉 `WS_EX_NOACTIVATE` 复活。捕获自检只读，
  不写标题/正文。
- **DSH**：一个 `DshHub` 持有一条 SSE，多窗只订阅；审批卡提交后禁用，resolved
  置终态，失败/断线恢复；controller 用 generation 防止旧线程的帧污染新连接。
- **ASR**：录音线程在 stop 等待循环里每 N ms `peek_wav()` 调一次识别；同刻最多
  一个在飞；partial 只更新预览/空输入框，final 才 `insert_text`。
- **人格卡**：包内只读 + 用户目录同 id 覆盖；id 正则、名称/说明/提示词长度限制；
  原子写；runtime 用目录与文件 mtime 缓存，保存后下一轮生效。
- **会话**：`archived_at` 缺省 0；分桶零 Qt；组头与 currentRow 解耦，点击组头只
  折叠；归档/删除当前会话由 controller 回退，避免“消息写进已隐藏会话”。
- **菜单**：p12 只在已有 AI 项或已有 hub 时迁移，避免精简布局多出空 hub；resolve
  在子菜单无子项时自然丢弃。
- **自语**：日期种子用 `splitmix64(toordinal)`，不依赖 `hash()`；每日状态按 slot
  落盘，显示失败不标记已说；连说复用 `say_sequence` 的 `on_show` 逐句朗读。

## 四、性能分析

**方法**：Windows 本机，Python 3.12.10，Qt offscreen/真机；命令见下。

| 指标 | 实测 | 归属 |
|---|---|---|
| 聊天窗 1100×800：composer / toolbar / scroll | 125 / 34 / 575 px | 修复后稳态 |
| （修复前同尺寸实测） | 286 / 195 / 314 px | 旧布局缺陷 |
| 音频端点枚举（冷 / 暖） | **149.36ms** / **0.01ms** | 设置页打开/刷新，异步线程 |
| 默认端点 id（短缓存后） | **3.34ms/次**，仅跟随默认时每 5s | 采样线程，约 0.07% 单核 |
| 色键窗口捕获自检 | PrintWindow **1.0** / BitBlt **1.0**；exstyle=8、layered=false | 仅按钮触发 |
| 透明直播窗对照 | PrintWindow **0.2454** / BitBlt **0.0**；layered=true | 仅按钮触发 |
| DSH 连接数 | 两个视图共享 **1** 条 SSE（测试断言） | 稳态连接 |
| ASR partial | 800ms/次（sapi 钳 ≥1500ms），30s 录音最多约 37/20 次；同刻 1 个在飞 | 仅录音期间 |
| 侧栏分节/折叠 | 每次历史刷新 O(n) 重建，折叠只 `setHidden` | 与旧重建同阶 |

**结论**

1. **稳态开销**：聊天窗只多一次顶部 spacer 的布局计算；菜单/侧栏结构不变。
   音频默认端点多一次 3.34ms/5s 的 COM 调用；色键多一次整窗 `fillRect`（0.1–0.3ms/帧）。
2. **新增路径成本与触发频率**：设备枚举 149ms 只在打开设置/点刷新时发生（短命线程，
   GUI 不等）；捕获自检只在按钮；ASR partial 只在录音期间；每日句 O(1) 种子。
3. **新系统调用/网络/磁盘/线程**：新增短命 `music-audio-endpoints` 线程（设置页）、
   录音线程内的增量识别（不新增常驻线程）、DSH 从 N 条 SSE 降为 1 条；
   ASR partial 走本地 HTTP/短进程，未新增常驻网络连接；每日状态每天最多一次写盘。
4. **内存/缓存**：人格卡按 mtime 缓存一个元组；音频端点缓存 30s、默认 id 2s；
   DSH 历史缓存一份；会话侧栏不缓存额外数据。没有随运行时长增长的缓存。

## 五、实机运行记录

**环境**：Windows 10/11 本机，Python 3.12.10，`.venv`。

- `python -m petcore --check --strict` → `version=0.2.0`、`strict.problems=[]`、
  `animations=106`（输出片段）。日志：`starting petcore 0.2.0`，无 traceback。
- 色键真机：用临时数据根启动 `python -m petcore --root <tmp> --instance probe1031`，
  枚举到标题 `dsh-pet core #0`，`capture_probe.probe_window(hwnd)` 输出
  `{"printwindow":1.0,"bitblt":1.0,"exstyle":8,"toolwindow":false,"noactivate":false,"layered":false}`。
- 透明对照：同命令 `stream_capture_keycolor=false`，输出
  `{"printwindow":0.2454,"bitblt":0.0,"exstyle":524296,"layered":true}`。
- 音频真机：`list_endpoints()` 返回 1 个输出端点（Senary Audio），默认端点 id 与
  枚举一致；`MicRecorder().devices()` 返回 1 个输入设备。
- 聊天窗几何/拼音：offscreen 1100×800 用例断言 composer 125、toolbar 34、scroll 575；
  真实微软拼音的 preedit 行为由 `QInputMethodEvent` 用例覆盖，未在真机逐键录屏。
- DSH：本机 DSH 桌面端未运行，审批/404/重连/超时只经 fake HTTP/SSE；报告不声称真机通过。
- OBS：本机未安装 OBS/直播姬，未做真实抓屏；只验证窗口样式与 GDI 读回。
- 安装包：`scripts/build_petcore.ps1 -Clean -Installer -OutputRoot rel-feedback2-2026-10-31`
  → `dsh-pet-core-setup.exe` 305,200,979 B，SHA256
  `b2ae9d148423df8339f1853d6fc9189652185579bc3a0363afef943e472d1c67`；
  `smoke --check --strict` 输出 `menu_template=json characters=1 builtin_images=24 strict=True`；
  Inno `Successful compile (120.969 sec)`。onedir 实机冒烟：色键窗口
  `layered=false / printwindow=1.0 / bitblt=1.0 / exstyle=8`，`daily.py` 已随包。
  安装向导交互（目录页/扩展勾选/卸载器）未由自动化跑完，需用户按测试指南验收。

**未验证 / 风险**：DSH 真机双向、OBS 真实捕捉、真实耳机插拔、真实麦克风内容、
安装向导 GUI 交互；均已在交付说明中列明。

## 六、复审修正（同日，子代理只读审查）

独立复审（只读子代理）在测试全绿之外找到 2 个高、7 个中、5 个低问题；逐条修正并补测试：

| 级别 | 问题 | 修正 |
|---|---|---|
| 高 H1 | 设置页 `confirm_save` 先写 mode 后写 keycolor，normalize 把「关闭」翻回 true | 两行对调；新增「色键→关闭→确认保存」断言 |
| 高 H2 | 运行期开色键只 `setWindowFlags` 同 flags，Qt 不重建 HWND，`WS_EX_LAYERED` 仍在，BitBlt 仍黑 | `apply_stream_window(keycolor=True)` 清 layered；`set_stream_capture_mode` 显式 `windowHandle().destroy()`；气泡 show 后同步宿主样式。真机复测：色键 `layered=false / bitblt=1.0`，透明 `layered=true / bitblt=0.0` |
| 中 M1 | 手选设备拔掉后 30 秒里只有检查那一拍回退 | 新增 `_manual_audio_fallback` 保持到下次检查；测试断言连续调用都回退 |
| 中 M2 | partial 间隔在阻塞识别前算，慢引擎变成不间断识别；50ms 空转 | 改为「先等完整间隔→识别→再等」；无实时预览时一次 wait 到结束 |
| 中 M3 | 识别诊断打印手选设备名，实际 `sampling.collect` 采默认设备 | `audio=lambda: snapshot(device_id)` |
| 中 M4 | 历史回放清掉卡片但 `_approvals` 留 id，同 id 审批被当重复丢弃 | `_clear_rows()` 清 `_approvals`；新增回放后同 id 重建卡测试 |
| 中 M5 | 共享 hub 不向新视图回放已连接状态 | `acquire()` 回放 connected/最近失败原因；新增第二视图状态断言 |
| 中 M6 | 精简布局迁移把 AI 助手插到设置上方 | hub 插到最靠前 AI action 的原索引；新增顺序断言 |
| 中 M7 | 回答中归档/删除当前会话，迟到 token/done 写进新会话 | `_ChatState.stream_session_id` 守卫 + 切会话前取消；新增迟到 done 测试 |
| 低 | `_answer` 对已删除按钮无 RuntimeError 保护 | 包 try/except，答案仍发送 |
| 低 | 人格卡提示词改完直接整窗保存会丢；卡片写盘与取消语义不一致 | `confirm_save` 自动保存有差异的当前卡；hint 写明保存立即写盘 |
| 低 | 捕获自检在 GUI 线程做全窗读回 | 改 `QThreadPool` + `_CaptureProbeSignals`，结果回 GUI |
| 低 | 搜索每次按键遍历动画树 | 实测 `page_search_texts` 0.69ms/次（测试角色 8 类），保持现状；真实 106 动画仍为毫秒级，不做易失配的缓存 |

复审修正后的最终门禁：

- `QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_*.py -q` → **2614 passed, 1 skipped**。
- `ruff check pet/ tests/ petcore/ examples/petcore-mods/ai.chat examples/petcore-mods/media.music` → 通过。
- 真机色键切换复测：宠物/气泡 `layered=false`、`printwindow=1.0`、`bitblt=1.0`、`exstyle=0x8`（仅 TOPMOST）。
