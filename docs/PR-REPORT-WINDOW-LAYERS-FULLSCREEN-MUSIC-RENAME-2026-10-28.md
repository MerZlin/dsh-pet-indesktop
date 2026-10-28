# 窗口层级修复 + 全屏自动隐藏 + 音乐三修 + 角色重命名（2026-10-28 第二批）

> **基线**：`4fbca8a3`（本分支起点；本批在其后第一批「联网/GIF/API/素材包」之上追加）
> **分支**：petcore 本地工作树　**日期**：2026-10-28
> **范围**：21 个文件（实现 12，其中新建 4；测试 5，其中新建 3；文档 4）
> **关联**：[`PETCORE-LOG.md`](PETCORE-LOG.md) L115–L118、
> [`SETTINGS-CHANGE-GATES.md`](SETTINGS-CHANGE-GATES.md)、
> [`PR-REPORT-AI-WEBSEARCH-GIF-API-DEFAULTS-2026-10-28.md`](PR-REPORT-AI-WEBSEARCH-GIF-API-DEFAULTS-2026-10-28.md)（同批第一批）

## 一、核心特性

四项彼此独立，但都落在「用户能直接看见」的层面：

| # | 能力 | 用户可见变化 |
|---|---|---|
| 1 | **窗口层级修复** | 「窗口置顶」关着时，冒泡不会再把鱼抬到别的应用前面；右键菜单开着时，冒泡不再压住菜单 |
| 2 | **全屏自动隐藏** | 全屏游戏/视频期间桌宠自动消失，退出全屏自动回来（常规页可关） |
| 3 | **音乐三修** | 唱歌动画被打断后自动续上；歌词播放期间不再被自语气泡打断；单曲循环每一遍都继续出词 |
| 4 | **角色重命名** | 右键「切换角色 → 重命名当前角色…」或常规页「角色」行的「重命名…」按钮，可以把角色叫成自己想要的名字 |

**红线 / 不变量**

- 气泡**仍然是桌宠的从属窗口**（owned window，parent = `PetWindow`），层级跟随宿主；没有改回独立顶层窗口，也没有改 `_window_flags()` 的置顶跟随语义。
- 菜单没有被「永久置顶」：`prepare_menu` 的 `topmost` 规则不变，修的是「气泡/桌宠在菜单会话期间不许再抬」。
- 自语气泡/点击台词/余额气泡/图片气泡/切页重排照常显示；展示会话只在有人显式持有时才拦截，且核心不认识「歌词」这类业务词。
- 全屏开关默认 True 且为全局键；关闭或平台不支持时**不建定时器**；全屏期间用户手动显隐以用户为准。
- 角色别名不写回目录层（`_characters` 保持目录原名），只在显示出口套用；不改菜单模板、不新增菜单项 id、不动 `coverage.py`。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/ui/bubble.py` | +160 / −5（文件 numstat 229/11，含第一批 GIF 改动） | 新增 Win32 z 序原语（`_window_above/_place_after/_is_above`，`SetWindowPos(NOACTIVATE|NOOWNERZORDER)`）与统一出口 `_present()`：菜单会话→沉到菜单下；置顶/直播→`raise_()`；非置顶→复位宿主上邻居后贴宿主下方。`_window_flags()`/parent/`WA_ShowWithoutActivating` 未改 |
| `petcore/ui/menu.py` | +65 / −7 | `menu_open()/active_menu()/menu_session()`（可重入、异常也恢复），`show_context_menu()` 用会话包住整段 `exec`；docstring 纠正「菜单需要 hint 才置顶」的误解 |
| `petcore/ui/pet_window.py` | +57 / −8 | `set_visible(True)` 的 `raise_()` 加菜单会话闸；新增 `rename_character()`；把不成立的层级注释改成实测口径 |
| `petcore/platform/fullscreen.py`（新建 329 行） | 新文件 | 零 Qt 全屏探测：2px 容差几何命中、按相交面积选窗口所在屏、QUNS 只认 3/4、自排除本进程/桌宠/桌面/任务栏/截图工具/`WS_EX_TOOLWINDOW`、永不抛异常、原因不含标题正文 |
| `petcore/ui/fullscreen_hide.py`（新建 222 行） | 新文件 | 进程内唯一 1Hz `QTimer` + 状态机（只隐藏当时可见的、只恢复自动隐藏的、用户手操优先、关闭即停表） |
| `petcore/ui/presentation_gate.py`（新建 149 行） | 新文件 | 进程级可重入展示闸门（RLock、owner 计数、45s 无续期自动释放） |
| `petcore/ui/rename_dialog.py`（新建 75 行） | 新文件 | 两个改名入口共用的 `prompt_alias()`/提示文案/截断说明 |
| `petcore/config.py` | +84 / −0 | `auto_hide_fullscreen`（默认 True）与 `character_aliases`（≤24 字符）两个全局键 + `_normalize` 清洗 + `character_alias/set_character_alias`；`SCHEMA_VERSION` 未动 |
| `petcore/contracts/host.py` | +39 / −0 | `PresentationPort` 增量面：`say/say_sequence(owner=)`、`show_image(force/owner)`、`begin_scene/end_scene/refresh_scene/animation_state` + `AnimationState` |
| `petcore/ui/app.py` | +154 / −6 | `_PresentationAdapter` 查闸门与四个新方法；`_display_spec()` + 别名相关方法；全屏 watcher 的启动/应用/停止；`refresh_character_names()` |
| `petcore/ui/menu_actions.py` | +5 / −0 | 「切换角色」子菜单末尾的「重命名当前角色…」（不新增菜单项 id） |
| `petcore/ui/settings/pages_core.py` | +188 / −7 | 「全屏时自动隐藏」行；「角色」行加「重命名…」按钮 + 别名 draft/回滚；`keywords()` 补别名 |
| `examples/petcore-mods/media.music/host/runtime.py` | +265 / −18 | 唱歌循环复核与 2^n 补播、展示会话持有/续期/释放、单曲循环三路「新一圈」判定（只重置行号、不重取词） |
| `petcore/builtin/features/fun.click-talk/host/factory.py` | +1 / −0 | 点击台词的配图传 `force=True`（用户动作，不许被歌词吞掉） |
| `petcore/builtin/features/fun.self-talk/host/factory.py` | +3 / −1 | 气泡被拒时**连朗读一起跳过**（看不见的话不该听得见） |
| `petcore/ui/reminders.py` | +3 / −1 | 定时提醒 `force=True`（用户设的提醒要压过歌词，拿不到气泡才退系统通知） |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_window_layers.py`（新建 374 行） | 新文件 | 非置顶不 raise、菜单会话抑制/恢复、置顶与直播行为不变、真机 z 序（offscreen 下 skip） |
| `tests/test_petcore_fullscreen_hide.py`（新建 492 行） | 新文件 | 几何/探测零 Qt/状态机 11 例/配置/设置行/真 CoreHost 接线 |
| `tests/test_petcore_presentation_gate.py`（新建 358 行） | 新文件 | 闸门单测 6 + 适配器接线 5 + mod×真实适配器端到端 2 |
| `tests/test_petcore_character_alias.py`（新建 438 行） | 新文件 | 配置 8 / 显示 6 / 菜单 5 / 设置页 6 |
| `tests/test_petcore_music_mod.py` | +367 / −3 | 唱歌续播与退避、锁定位不抢、切歌重新武装、单曲循环×3、会话×2 |

### 未改动（避免评审误判）

- `petcore/ui/island.py`：灵动岛胶囊上的角色名仍是建岛时取的（与切角色同一条既有边界），本批不修。
- `petcore/ui/tray.py`：托盘原生菜单由 Shell 弹出、不经 `show_context_menu`，不在展示/菜单会话内（已知边界，见 §六）。
- `petcore/ui/settings/coverage.py` 与 `petcore/menu_templates/*.json`：本批没有新增菜单项 id，无需登记与迁移。
- `packaging/petcore.iss`、`examples/petcore-mods/README.md`、`tool.ai-browser` 是工作树里既有的未提交改动（L110 产物），未触碰。

## 三、实现要点

1. **层级**：Windows 上「置顶关闭」仍然会被 `show()` 打破——owned window 的 `show()` 会把整条 owner 链抬到普通层最上面。所以修复不是「删掉 raise_」，而是 `_present()`：非置顶时先记住宿主的上邻居 `anchor`，`show()` 之后把宿主 `SetWindowPos` 回 `anchor`，气泡贴宿主下方；菜单会话期间两者都不抬，气泡沉到菜单下。`QWidget.stackUnder()` 对顶层窗口是空操作（实测），必须走 Win32。
2. **菜单会话**：`show_context_menu()` 用 `menu_session()` 包住 `exec`，计数可重入（嵌套菜单不会提前解锁），异常路径也恢复；桌宠 `set_visible(True)` 与气泡所有显示出口都先查 `menu_open()`。
3. **全屏**：探测与状态机分离——`platform/fullscreen.py` 只回答「前台是不是真全屏（+原因）」，`ui/fullscreen_hide.py` 负责「该藏谁、该恢复谁、用户手操优先」。判定「真全屏」= 覆盖整屏（2px 容差）+（无标题栏 或 置顶），QUNS 只认 3/4（3 = 演示模式，4 = 全屏应用）。
4. **展示会话**：核心只认 owner 字符串；`say()` 在非持有者且非 `force` 时返回 False 且不动当前气泡。持有者必须每拍 `refresh_scene` 续期，45s 没续期自动释放——这样 mod 崩在歌中间也不会让桌宠永久失声。
5. **唱歌续播**：租约（我请求过循环）与事实（循环还在我手上）分开记。`animation_state()` 报 `loop=False` 时只丢租约、不发 `release_loop()`（发了会误伤报时/别的循环）；补播走 2^n 退避，`_rearm_pending` 保证「永不永久放弃」，探头/击飞/拖拽报 `locked=True` 时不抢。
6. **单曲循环**：三路判「新一圈」——进度回退（≥5s 回退且新位置 ≤15s 且上一拍已过 0.75 曲长）、不上报进度时按估算曲长整轮回绕、尾奏后的暂停→播放边沿。三路都只重置行号与节流，`_lines`/`_fetch_key` 不动（60s 冷却与每曲 3 次不受影响）。
7. **角色别名**：`character_aliases` 是全局键；`CharacterSpec` 是 frozen，显示出口 `_display_spec()` 用 `dataclasses.replace` 套名字，目录层不被写回。两个入口共用 `prompt_alias()`：菜单入口立即落盘（显式用户动作），设置页入口走该页 dirty/快照语义。

## 四、性能分析

**方法（可复现）**：`PYTHONPATH=. .venv/Scripts/python .scratch/probe_batch2_perf.py`
（真实 Win32 窗口，不是 offscreen；探针把结果写到 `.scratch/probe_batch2_perf.json`）
环境：Windows 11 家庭版中文版 / Python 3.12.10 / PySide6 6.12.0 / 单屏 1920×1200 / 前台是
Kimi Code 窗口（非全屏）。

| 指标 | 实测：中位 / p95 | 归属 |
|---|---|---|
| `fullscreen.probe_fullscreen()` | 0.145 / 0.220 ms（n=20） | 新增：1 Hz，仅开关打开且需要时 |
| `menu.menu_session()` 进出 | 0.001 / 0.001 ms（n=2000） | 新增：每次弹菜单一次 |
| presentation gate 一轮（begin+allows+refresh+end） | 0.001 / 0.001 ms（n=2000） | 新增：每首歌每拍一次（1 Hz） |
| `SpeechBubble.show_text()`（置顶关闭，含宿主复位） | 0.093 / 0.283 ms（n=30） | 既有路径 + 新增 z 序复位 |
| `SpeechBubble.show_text()`（置顶，`raise_()` 路径） | 0.092 / 0.191 ms（n=30） | 既有行为基线 |
| `FullscreenHideController.poll()`（未命中） | 0.000 / 0.000 ms（n=200） | 新增：1 Hz |
| `FullscreenHideController.poll()`（命中、隐藏 2 只） | 0.000 / 0.001 ms（n=200） | 新增：只在状态切换那拍做实际工作 |

**结论（逐条）**

1. **稳态开销**：新增的三个热路径都在微秒级——菜单会话 1 µs、展示闸门 1 µs/拍、全屏状态机
   0 ms/拍；置顶关闭时 `show_text()` 的中位数与置顶路径相同（0.093 vs 0.092 ms），说明「记住
   宿主上邻居 + `SetWindowPos` 复位」在真实窗口上的成本落在噪声里（p95 0.283 ms 是首次布局
   的抖动，不是新增调用）。全屏探测 0.145 ms/次、1 Hz，占空比 ≈ 0.015%。
2. **新增路径的绝对成本与触发频率**：全屏探测只在**开关打开且平台支持**时按 1 Hz 跑，关闭或
   非 Windows 时定时器根本不建（`timer_active() == False`，探测次数 0）；展示闸门只在有 owner
   持有时才拦人，没人持有时是一次空字典比较；菜单会话只在弹出菜单的 `exec` 期间存在。
3. **有无新的系统调用 / 网络 / 磁盘 / 线程**：**没有新线程、没有网络、没有磁盘**。新增的系统
   调用只有两条 Win32：`probe_fullscreen()` 的 `GetForegroundWindow`/`DwmGetWindowAttribute`
   （以及 `SHQueryUserNotificationState`）与 z 序复位用的 `SetWindowPos`（只在气泡显示那一拍）。
   音乐端的「复核循环」读的是端口内存状态（`animation_state()`），不额外采样。
4. **内存与缓存有没有增长**：全屏控制器两张集合（自动隐藏的窗口 / 被用户覆盖的窗口），退出
   全屏即清空；展示闸门只有 owner 字符串 + 计数 + 时间戳；音乐端「新一圈」判定只重置行号，
   `_lines` 与取词缓存不增长（单曲循环不触发新的取词请求，测试里用 `len(port.requests)` 钉住）。

## 五、实机运行记录

本机真实环境（Windows 11 / Python 3.12.10 / PySide6 6.12.0），不是 CI。

### 5.1 层级修复（`GW_HWNDPREV` 实测，单屏，非 offscreen）

`.scratch/probe_before.txt`（修复前，`z` 下标越小越靠前）：

```
[A] always_on_top=False
  other hwnd=0x120700 topmost=False ... pet hwnd=0x330A9C topmost=False
  冒泡后: z(other)=57 z(pet)=55 z(bubble)=54
  foreground: 0x1A0AD0 -> 0x1A0AD0
  FAIL  桌宠仍在该应用窗口之下 :: z(other)=57 z(pet)=55
  FAIL  气泡仍在该应用窗口之下 :: z(other)=57 z(bubble)=54
[A2] 裸 show()：show 之后 z(other)=57 z(pet)=55 z(bubble)=54 → 只 show() 也把 owner 链抬上去
[C] always_on_top=True（默认）：z(menu)=12 z(pet)=11 z(bubble)=10 → 气泡压在菜单上
FAIL 4 项
```

`.scratch/probe_after.txt`（修复后，同一探针）：

```
[A] 冒泡后: z(other)=55 z(pet)=56 z(bubble)=57；foreground 0x1A0AD0 -> 0x1A0AD0（没抢前台）
[A2] 裸 show() 仍把 owner 链抬上去（证明复位是必要的，不是多余）
[C] 冒泡之后: z(menu)=10 z(pet)=11 z(bubble)=12；两者都没有 WS_EX_TOPMOST 丢失
==== 结论 ==== 全部通过
```

### 5.2 全屏自动隐藏（真开全屏窗口）

`.scratch/probe_fullscreen.py`（全屏窗在**独立进程**里开，探针按 pid 自排除自己）：

```
platform=win32 supported=True
foreground= {'process': 'Kimi Code.exe', 'pid': 21068, 'rect': (0, 0, 1920, 1128), ...}
baseline hit=False detail=未命中 cls=Chrome_WidgetWin_1 ... monitor=(0,0,1920,1200) busy=5
fullscreen detected=True after=0.20s
pets before: [True, True]
after enable: visible=[False, False] auto_hidden=[('slot', 0), ('slot', 1)]
after 3 extra polls: visible=[False, False]
after leave: visible=[True, True]
own fullscreen window: hit=False detail=前台是桌宠自身 python.exe（按 pid 自排除）
timer_active_after_disable=False
```

### 5.3 音乐三修

- 红→绿：mod 侧 8 个新用例改前 `8 failed, 39 passed`，改后 `48 passed`（根因：租约还在但
  循环已被核心清掉、歌词没有占用概念、单曲循环身份不变）。
- `scripts/music_probe.py --seconds 2` 本机真跑通：SMTC 无会话、枚举 9 个会话、220 ms。
- **本机没有真实播放器在放**（网易云/酷狗的真实 SMTC 行为、真实动画被打断、真有歌时的第二遍
  循环都未实机验证），证据只到单元层（假采样序列 + 假 presentation 端口的调用序列）。

### 5.4 角色重命名

- 红证据（`.scratch/alias_red.py`）→ `13 failed, 2 passed, 10 errors`；实现后 25 passed。
- 只做了 offscreen 下的真实 `CoreHost/PetManager/PetWindow/SettingsPage` 构造与点击
  （`QInputDialog.getText` 被 monkeypatch），**没有用真鼠标点菜单/设置按钮**。

### 5.5 门禁

```
$ .venv/Scripts/ruff check pet/ tests/ petcore/ examples/petcore-mods/
All checks passed!
$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest -q      # 最终代码（含收口的 show_image 闸门）
10819 passed, 19 skipped, 18 warnings in 1224.93s (0:20:24)
$ .venv/Scripts/python -m petcore --check
strict.problems == []（media.music / tool.ai-browser 菜单与设置页齐全）
```

> 说明：中途有一次全量跑到一半停住（CPU 只用了 53 s、约 55 min 无进展），停掉后带日志重跑，
> 同一份代码 20:24 跑完、10819 passed。本地没有留下那次卡住的定位结论——它没有复现，
> 重跑与最终一轮都正常，故不把它算作本轮改动引入的问题。

## 六、未验证项与风险

- **层级**：单屏 + 单 DPI 实测；跨屏、DWM 合成/独占全屏、150% DPI 下的 z 序未测。非 Windows 走「不 raise」分支，未在 macOS/Linux 真机验证。
- **托盘菜单**：`QSystemTrayIcon.setContextMenu` 由 Shell 原生弹出，不在 `menu_session()` 内；气泡理论上仍可能盖住托盘菜单。要修得在 `ui/tray.py` 里给 `aboutToShow/aboutToHide` 配对开会话，且要防「会话卡住导致气泡永不抬起」，本批没做。
- **全屏自动隐藏**：独占全屏 D3D 游戏、钉住/永在最前的 UWP 与 cloaked 窗口、真双屏、macOS/Linux 真机未实测；`SHQueryUserNotificationState` 的「命中」路径只有单测（本机基线只出现 busy=5）。灵动岛不随全屏隐藏（与主分支一致）。
- **音乐**：本机没有真实播放器在放，所以网易云/酷狗的真实 SMTC 行为、真实动画被打断、真有歌时的第二遍循环都未实机验证。不上报进度路径的每一圈有「尾奏长度」级漂移（估算曲长 = 末句 + 6s），连续多圈会比声音略早；尾奏里手动暂停再播会被当成新一圈。
- **角色重命名**：没在真桌面用鼠标点菜单/设置按钮（`QInputDialog.getText` 被 monkeypatch）；灵动岛名字不随改名刷新。
