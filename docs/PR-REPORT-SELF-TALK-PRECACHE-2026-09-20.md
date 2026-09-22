# 台词本地语音预缓存（self_talk_voice_precache_enabled）变更记录

> 结论先说：点击自言自语与「点击动画台词绑定」里的台词，现在可以在**保存设置后 / 每次启动时自动在后台**合成为本机语音文件。命中缓存时点击**零延迟出声，且断网也能说**。开关**默认关闭**——本机没装本地语音服务是常态，不开就不发探测请求、不联网、不占线程。

> **移植落地说明（2026-09-22）**：本文写于原作者基线 `1b81fea`（2026-09-18）。落到本仓库
> `main`（`56f8ad2`）时上游已漂移，本文里的两处数字属于**原基线**，本仓库的对应值如下：
> 行数预算不再是「2319 → 2330」，而是与本批「配图概率」一起合并校准为 **2401 → 2419**
> （见 `tests/test_architecture.py` 的注释链）；全量表里的 passed 数按本仓库基线重跑为准
> （本仓库的落地复验数字见文末「九、落地复验（本仓库 main，2026-09-22）」）。
> 正文保留原始记录不改，避免篡改作者的实机证据。仓库外工具路径（`F:\dsh\tts\…`）是
> **原作者本机**路径，这些脚本按交付约定**不随本次 PR 入库**。
> 同批另一份：`docs/PR-REPORT-SELF-TALK-IMAGE-CHANCE-2026-09-20.md`；本功能复用的音频
> 通道来源见 `docs/PR-REPORT-VOICE-CHIME-2026-09-15.md`。

---

## 一、功能亮点

1. **点击秒出声、断网也在**：命中 `<配置目录>/self_talk_voice/<md5(文本)[:16]>.wav` 时直接播本地文件，不再等在线合成（原先每句要走一次 edge-tts）。
2. **自动补齐，不用记脚本**：保存设置后、以及每次启动后 9 秒各触发一次；只补**没有可用音频**的句子，已有的不重合成（幂等）。
3. **点击动画绑定也算数**：绑定对话框不走设置页保存信号，所以它的改动由**启动时那一次**兜住。
4. **失败安静但不隐形**：本机服务不在线时整轮跳过并落一条日志；单句失败不影响其余句子；一次只跑一轮（单飞，不排队、不叠线程）。
5. **与报时/节日共用一条音频通道**：不叠音；通道忙时这一次回落在线合成，不打断正在播的语音。
6. **数据归位正确**：语音文件放**配置目录**而不是仓库 assets——它是用户数据，换资源包/升级不该丢；删掉即等效于关闭。

## 二、使用方式

1. **前置**：本机 CosyVoice 服务在跑（`http://127.0.0.1:9880`，`GET /health` 返回 `ok`）。环境变量 `DSH_PET_TTS_URL` 可覆盖地址。
2. **打开开关**：设置 → **互动** 域 → **点击反馈** 组 → **「台词自动预缓存」**（默认关）→ 关闭设置页即保存，并立刻在后台开始合成。
3. **写绑定词**：设置 → 点击反馈 → **「编辑点击动画绑定」**写词 → 关掉设置页；若对话框是直接关的，改动会在**下次启动**被补上。
4. **看进度**：配置目录下 `pet-<pid>.log`：
   - `已开始后台预缓存台词语音（设置保存后 / 启动时）`
   - `台词语音预缓存完成：[声线] 台词 -> 文件名`
   - 服务不在线：`台词语音预缓存跳过：本地 TTS 服务不可用`
5. **点击时怎么分辨本地/在线**：日志里 `点击自言自语：播放预缓存台词 xxx.wav（本地，不走网络）` 就是本地；否则回落在线合成。
6. **手动全量重做（可选，仓库外工具）**：`python F:\dsh\tts\build_self_talk_voice.py [--force] [--dry-run] [--audition]`，产出 `manifest.json` 清单。

## 三、注意事项

1. **默认关闭**，且**不联网**：关着的时候保存设置/启动都不会有任何网络动作（测试纪律同样要求：本功能的测试不碰网络，全部用假服务）。
2. **需要本机语音服务**；没装就保持关闭，点击仍照旧走在线合成（edge-tts），不会因为开关开着而变哑。
3. **合成慢**（每句 20~60 秒，服务带 ASR 回读校验、不满意会重抽）：所以只在后台做，**绝不阻塞点击与保存**。
4. **0 字节残file按"没有"处理**：合成写入被打断（强杀/超时）会留下 0 字节文件，`is_file()` 判定会把它当已缓存，结果点击播**静音**且日志无线索。模块与播放侧都改成"非空才算命中"（见第五节）。
5. **反复触发不排队**：同一时刻只跑一轮；正在跑时新的触发直接返回 False。
6. **本轮未验证**：macOS / Linux 真实 GUI；配置页的视觉与放大字体表现（原因见 4.3）。

## 四、设置变更记录（`docs/SETTINGS-CHANGE-GATES.md` 对照）

### 4.1 准入（6 条）

| 准入条目 | 结论 |
|---|---|
| 它是偏好 | ✓ 表达"以后点击都优先用本机语音"的持续行为，不是一次性命令 |
| 低频且跨任务 | ✓ 设一次长期有效；点击本身仍是主要交互 |
| 有可靠默认值 | ✓ 默认 `false`——无本机服务时产品照旧可用（在线合成） |
| 归属唯一 | ✓ 互动 / 点击反馈（`setting_id` 用 `click_` 前缀被 `claim_prefix("click_")` 认领，已加断言锁死） |
| 值得让用户决策 | ✓ 产品无法自动推断"用户是否装了本机 TTS 服务" |
| 契约完整 | ✓ 见 4.2 |

### 4.2 准入记录

```text
setting_id             click_self_talk_precache   （配置键 self_talk_voice_precache_enabled）
domain_id / group_id   互动 / 点击反馈
title / description    台词自动预缓存 / 保存设置后在后台把点击台词与绑定台词合成为本机语音文件…
search_aliases         无（本项目设置页没有搜索机制）→ 不适用
default                false
capability_requirement 本机 CosyVoice 服务（http://127.0.0.1:9880，DSH_PET_TTS_URL 可覆盖）
platform_availability  Windows / macOS / Linux 一致：纯 Python + 本地 HTTP，无平台分支
disclosure_level       primary（与相邻的「点击台词朗读」同组同层级）
dependency             点击触发自言自语（self_talk）——随该总开关整组显隐
preview_target         无专用预览（点一下桌宠就是真实预览，不做演示专用副本）
commit_policy          on_finish（关闭设置页时统一写回，与同页其它项一致）
migration              全新键：老配置没有它 → 取默认 false，无需迁移，旧键/旧入口无
recovery               服务不可用→整轮跳过并记日志；0 字节残file→视为缺失重合成；
                       关掉开关即停；删除 self_talk_voice 目录即清空缓存，均不影响点击可用性
```

### 4.3 准出（5 大项）

1. **契约** ✓ 归属/文案/默认值/依赖/能力条件/披露层级/保存语义如上；失败有默认值、有安全入口（关开关），不会让设置页打不开或退不出应用。
2. **TDD** ✓ 每个切片先红后绿（真实红记录见第五节）。已覆盖：默认值、持久化 round trip（`Config` 层 + 设置页 `_write_config` 层各一条）、依赖显隐（跟随自言自语总开关）、恢复路径（服务不可用）、保存策略（on_finish）、单飞、0 字节残file、启动钩子自足、清单/去重/声线稳定性。
   - 相关测试 ✓、全量 `python -m pytest -q`：**2392 passed / 9 skipped / 0 failed**（`--basetemp=C:\pt`，见第六节）✓、ruff ✓。
   - **不适用**：迁移（全新键，无旧键）、平台 capability matrix（无平台分支）、搜索/深链（无搜索机制）、`git diff --check`（本机未安装 git，且该目录不是 git 工作树）。
3. **布局与可访问性** —— **部分未完成，如实记录**：新增行沿用既有 `SettingRow` 组件，与同组相邻行同构（宽度自适应、字体度量、Tab 顺序、焦点与主题样式全部继承，未新增自定义绘制或滚动容器，未引入动效）。
   **未做的**：`compact/standard/wide` 三档宽度、放大字体、High DPI 的**视觉**验收——本会话视觉后端不可用（vision 服务返回 429 / backend unavailable，且无本地 OCR），无法产出截图或像素判读；不做"没看就说通过"的结论。
4. **跨平台** ✓ 共享 setting ID、默认值、依赖与保存语义；无平台条件分支；**真实 GUI 验收仅 Windows**（如实声明，macOS/Linux 未执行，也不推断其结果）。
5. **视觉与文档** —— 截图**未做**（原因同第 3 条）。**`CONTEXT.md` 无需更新**：本次没有改动 Shared UX Contract、Settings System 模型或 Menu Action Model，只是在既有组里增加一个同构控件、并在既有保存时机多触发一次后台任务。

## 五、技术纪要

### 5.1 分层

- `pet/self_talk_voice.py`（新）：纯逻辑 + 后台线程。`cache_path`（命名契约）、`pick_voice`（声线选择）、`collect_lines`（收集全局台词 + `character_profiles[*].click_talk_bindings[*][*]`、去重、标注来源）、`missing_lines`、`run_precache`（探测服务→逐句合成→写文件，返回 `checked/made/failed/skipped/reason`）、`start_precache`（读开关 + 单飞 + 守护线程）。
- `pet/app.py`：`AppShell.self_talk_voice_file`（非空才算命中）、`AppShell.speak_self_talk`（先本地后在线，且日志可分辨）、`AppShell.precache_self_talk_voice`（薄封装，把配置交给入口）、`AppShell._precache_self_talk_voice_on_start`（启动钩子，独立方法 + 兜底日志）、`PetInstance._modern_settings_finished`（保存后触发）。
- `pet/voice_chime_service.py`：`play_file()`（本地文件播放，忙则返回 False，不排队）。
- 设置页：开关建在 `settings_pet_controls.py`，行/显隐/写回在 `modern_settings_dialog.py`。
- 仓库外工具：`F:\dsh\tts\build_self_talk_voice.py`（手动全量产出 + `manifest.json`）、`F:\dsh\tts\pick_refs.py`（只读选声线审计）。

### 5.2 命名契约（两侧必须一致）

`<配置目录>/self_talk_voice/<md5(text.strip() UTF-8)[:16]>.wav` —— 桌宠的播放侧与产出脚本各写一份实现，模块测试锁死该契约（改一边就会被测出来）。

### 5.3 日志可分辨是硬要求

"本地播了"与"偷偷回落成在线合成（音色会变）"必须在日志里能分辨，否则文件名对不上时表现为"声音怎么变了"却查不出原因。因此本地播放成功落 `播放预缓存台词 …（本地，不走网络）`，预缓存每次完成/跳过都落一条。

### 5.4 本轮修复的真实缺陷（都是自己踩的）

1. **0 字节残file被当成已缓存** → 点击播静音、日志无痕。`is_file()` 不足以代表"可用"；模块侧与播放侧都改为"非空才算命中"，产出脚本同样对非空判断。
2. **启动钩子写成 `self.shell.…`** → `AppShell.start()` 里根本没有 `self.shell`（那是 `PetInstance` 的属性），每次都抛 `AttributeError`；pythonw 没有控制台，**Qt 槽里的异常只进 stderr**，于是预缓存从未运行、日志一条都没有。改为独立方法 `_precache_self_talk_voice_on_start` 并显式兜住 + 落日志，且加了"钩子自足"用例。
3. **设置行没登记归属** → 掉进「待分类（开发期）」，全量测试红一条。`setting_id` 改用 `click_` 前缀由 `claim_prefix("click_")` 认领，并加断言锁死归属。
4. **用外部工具手改运行中的配置** → PowerShell `Set-Content -Encoding UTF8` 写入 **BOM**，桌宠启动时判定配置损坏并隔离（`config.json.corrupt-*`），内存回落默认值，表现就是"开关明明开了、功能却不跑"。**教训：改动配置只用产品自己的 `Config` API，且必须先把桌宠停掉。** 事后核对：隔离副本与当前配置**同为 123 键、无键缺失**，设置未丢。

### 5.5 行数预算

`modern_settings_dialog.py` 实测 2330 行 > 预算 2319，按文件约定**校准预算到 2330**（带日期与理由注释），未压缩行宽/合并语句。
（本仓库落地时该校准与「配图概率」那一次合并为一条 2401 → 2419，见文首移植说明。）

## 六、本地验证记录

### 6.1 全量测试

`python -m pytest -q --basetemp=C:\pt` → **2392 passed, 9 skipped, 0 failed**（3 分 15 秒）；`ruff check pet/ tests/` → All checks passed。
（Windows 上必须用短 `--basetemp`：`test_image_directory_picker_opens_right_drawer_with_three_column_masonry` 的长文件名会撞 MAX_PATH，属环境性失败，与本次改动无关。）

### 6.2 真实 GUI 端到端（Windows，用户本人点击）

前置：故意把 5 个缓存文件中的 3 个清成 0 字节（模拟残file），桌面桌宠以 `pythonw -m pet --slot 0` 启动，开关为开。

```
22:45:02 INFO 已开始后台预缓存台词语音（启动时）
22:46:01 INFO 台词语音预缓存完成：[happy-08] 欧鲸鲸…… -> f4edaa308e6de6f8.wav
22:46:17 INFO 台词语音预缓存完成：[happy-01] 今天也要认真工作呀。 -> 552f7f348a88fcda.wav
22:46:30 INFO 台词语音预缓存完成：[sad-03] 再陪你一会儿。 -> dd02cfb49b3c2833.wav
22:47:04 INFO 点击自言自语：播放预缓存台词 dd02cfb49b3c2833.wav（本地，不走网络）
22:47:08 INFO 点击自言自语：播放预缓存台词 94e0650abf039137.wav（本地，不走网络）
… （后续每次点击都在播本地文件，5 句轮换命中）
```

结论：**少了哪句就补哪句**（只重合成 3 句，2 句非空的原样保留）；补完后的点击全部命中本地文件、无异常、开关值未被覆盖、缓存目录无残留。

## 七、变更文件清单

新增：
- `pet/self_talk_voice.py`
- `tests/test_self_talk_voice_precache.py`
- 本文件；仓库外另有 `F:\dsh\tts\build_self_talk_voice.py`、`F:\dsh\tts\pick_refs.py`（不进包）

修改：
- `pet/app.py`（本地优先播放 + 预缓存入口 + 两个触发点）
- `pet/voice_chime_service.py`（`play_file`）
- `pet/config.py`（新键默认值/归一化/保存清单/reload 白名单）
- `pet/settings_pet_controls.py`、`pet/modern_settings_dialog.py`（设置页开关）
- `tests/`：`test_voice_chime_service.py`、`test_config_schema.py`、`test_desktop_pet_features.py`、`test_menu_layout.py`、`test_architecture.py`（预算校准）

## 八、风险与回滚

- **风险**：本机服务未装/未启动时开关若被误开，只会在日志里留一条"跳过"，不产生副作用（不发探测以外的请求、不起长时间线程）。
- **回滚**：关掉开关（功能静默、点击回落在线合成）；或删除 `<配置目录>/self_talk_voice`；代码层回滚只需移除两个触发点（保存后 / 启动时）与设置行，其余代码路径在开关关闭时都是 no-op。
- **遗留**：设置页视觉/放大字体验收与 macOS/Linux 真实 GUI 验收未完成（4.3 第 3、4、5 条已如实标注）。

## 九、落地复验（本仓库 main，2026-09-22）

移植到本仓库 `main`（`56f8ad2`）之后，由移植方在本机重新跑过的一轮（命令与原始输出见下）。
环境：Windows、Python 3.11.1、PySide6 6.11.1、本机 CosyVoice（127.0.0.1:9880）**未运行**。

**（1）测试与静态门禁**

```text
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_self_talk_voice_precache.py \
  tests/test_self_talk_image_chance.py tests/test_click_self_talk_speech.py \
  tests/test_voice_chime_service.py tests/test_config_schema.py tests/test_menu_layout.py \
  tests/test_architecture.py tests/test_desktop_pet_features.py -q
-> 264 passed in 28.51s

QT_QPA_PLATFORM=offscreen python -m pytest -q --basetemp=C:/pt
-> 2774 passed, 11 skipped, 12 warnings in 220.50s (0:03:40)

python -m ruff check pet/ tests/      -> All checks passed!
git diff --check                      -> clean
```

受影响时序族（`test_voice_chime_service` / `test_click_self_talk_speech` /
`test_self_talk_voice_precache` / `test_voice_chime`）**串行复跑 3 遍**（216 passed × 3）
与**并发 3 进程**（各 310 passed）全部绿。

**（2）真机端到端（隔离 `APPDATA`，未触碰本机真实配置）**

`APPDATA=<临时目录> python -m pet`，并在该隔离配置里用产品自己的 `Config` 打开
「点击触发自言自语 + 点击台词朗读 + 台词自动预缓存」：

```text
13:27:05,943 INFO 已开始后台预缓存台词语音（启动时）
13:27:07,446 INFO 台词语音预缓存跳过：本地 TTS 服务不可用（<urlopen error timed out>）
```

= 启动后 9 秒的独立方法钩子确实跑到；服务不在线时**只落一条 info、不写文件、不抛异常**
（本机没装 CosyVoice 的通用用户路径）。

点击 A/B（真实 `PostMessage` 点击窗口中心，读日志计数）：

| 配置 | 点击次数 | `点击自言自语的朗读` 日志 | `播放音效` 日志 |
|---|---:|---:|---:|
| `self_talk_image_chance=0` | 6 | **4**（文本气泡 → 朗读路径被调用） | 6 |
| `self_talk_image_chance=100` | 8 | **0**（图片气泡按设计不出声） | 8 |

这正是设计语义在真机上的可观测差异：**图片气泡 `_last_self_talk_text=None` → 不出声**，
所以"出声次数"即"文本气泡次数"。本机没有本地缓存、在线合成排队，故日志是
`点击自言自语：音频通道忙，已排队待播`（不叠音、不打断）。

**（3）设置页视觉验收（三档宽度 × 放大字体）**

`scripts/capture_settings_pages.py` + 定向截图（`docs/screenshots/self-talk-2026-09-22/`）：
1100 / 900 / 720 px 三档宽度下三行新控件均可见、无横向溢出（控件右边界 ≤ 行宽），
720 px + 1.3 倍字体下描述文本按字体度量换行、控件仍可达（这两项是原文 4.3 第 3、5 条
标注"未做"的缺口，已在本轮补上；High DPI 缩放仍需真机显示缩放环境，未验收）。

**（4）可用性修复：点击侧与周期气泡解耦（2026-09-22 追加）**

主人在真机上反馈「点击台词朗读既看不到也听不到」。定位到两个叠加原因，均已修：

1. **设置行被藏**：`_update_self_talk_controls` 原先把 `click_self_talk` /
   `click_self_talk_speak` / `click_self_talk_precache` / `click_talk_bindings` 与
   `self_talk_*` 细项放在同一张显隐名单里，于是它们跟着「气泡自言自语」（默认关）
   一起隐藏 —— 新功能在默认配置下**在设置页里不存在**。现在点击侧另立
   `_update_click_self_talk_controls`：顶层「点击触发自言自语」常显，朗读 / 预缓存 /
   台词绑定跟随它。
2. **出声链路被卡**：`PetWindow._on_click` 原先要求 `click_show_self_talk and
   _self_talk_enabled` 才显示气泡，`AppShell._self_talk_speak_wanted` 也要求
   `self_talk_enabled` 才建音频通道 → 只想点击听声的用户必须额外打开周期气泡开关。
   现在两者都只认 `click_show_self_talk`（+ 朗读开关），语义变更已在 PR 描述里声明。

回归：`tests/test_click_self_talk_speech.py` 新增 4 条（走真实 `PetWindow._on_click`
入口 + 真 seam 断言"显示什么/朗读什么"，含反向用例与朗读开关关闭用例）；
`tests/test_desktop_pet_features.py` 新增 `test_click_self_talk_rows_follow_their_own_toggle`
（默认配置下顶层开关可见、两个开关互不牵连），并按新口径收窄既有的
`test_modern_settings_progressively_reveals_dependent_controls`（该用例原先**断言**
点击侧随周期气泡隐藏 —— 正是本次要改的行为）。
