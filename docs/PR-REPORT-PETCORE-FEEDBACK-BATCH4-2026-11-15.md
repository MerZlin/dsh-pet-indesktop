# petcore 反馈批次 7：移动收尾 / 播放速度 / 朋友的酒 BPM / 致谢 / 手机版（2026-11-15）

## 0. 结论

本批把「按住方向键跑两步摔一跤」「2 倍速实际只有 1 倍」「唱歌时动画变慢」
「朋友的酒节奏固定 1 秒」「致谢作者说明是小字」「手机上正文右侧被裁」六个可见问题
落到具体接缝：

- 移动 clip 支持显式 `segments`，把烤进素材的绊倒/趴地排除出按住循环；松手从绊倒前一帧
  开始播完整收尾。
- 闲置降帧只对真正支持隔帧的后端生效；WebM 不再被 `set_decode_throttle` 拉成半速。
- `playback_speed` 在设置页实时作用于当前 clip；键盘移动、唱歌循环、漫游不再降帧。
- 朋友的酒新增 `music_buddy_wine_bpm`（默认 132，60–200），音乐扩展升到 1.4.0。
- 致谢作者区改为 `handle · role` 正文字号，`@浩瀚星空2005` 进入作者区。
- 手机版最小 440×560、初始 460×780；assistant 右侧 12px padding（左侧 0）；顶部 `[⋯][✕]`，
  新建会话在底部最右。

## 1. 修改文件说明

> 工作区在本批之前已有未提交改动；下表只列本批触及的文件，行数为 `git diff --numstat`
> 的当前工作区口径（未跟踪的新文件另注）。

### 1.1 键盘移动与内容解析

| 文件 | +/- | 改了什么 / 为什么 |
|---|---|---|
| `assets/characters/shenshen/videos/move_strides.json` | +1/−1 | `左转奔跑` 增加 `segments: {start:0, loop_start:56, loop_end:104, end:240}`；105 起是绊倒/趴地，按住不再每圈摔一跤。 |
| `petcore/contracts/content.py` | +17/−0 | `CharacterSpec.move_segments` 字段。 |
| `petcore/content/legacy.py` | +205/−8（含前序） | `_clean_segments` / `_read_move_segments`：只收非负整数，非法项丢弃；`load_character` 传给 `CharacterSpec`。 |
| `petcore/content/modcontent.py` | +7/−0 | mod 角色包同样解析 `move_strides.json` 的 `segments`，声明文件与默认文件两条路径都合并。 |
| `petcore/engine/keyboard_drive.py` | 新增 161 行 | `_loop_curve_span`：每圈位移 = `stride × (curve[loop_end] − curve[loop_start])`；缩短循环段不会凭空加速。 |
| `petcore/ui/pet_window.py` | +503/−24（含前序） | 传入 override；releasing 再按同方向重新起跑；`_on_finished` 按住时 `start()+jump(loop_start)`；`_on_frame` 驱动时先推进再取图，越界帧不再闪一帧趴地。 |

### 1.2 播放速度与闲置降帧

| 文件 | +/- | 改了什么 / 为什么 |
|---|---|---|
| `petcore/media/clip.py` | +21/−3 | `PetClip.supports_frame_divisor`；`set_frame_divisor` 不再回退到 `set_decode_throttle`（WebM 的降速会真的拉长时长）。 |
| `petcore/media/sheet_clip.py` | 新增 167 行 | `_tick` 每拍前进 `divisor` 帧（隔帧采样，时长不变）；`duration()` 不再乘 divisor。 |
| `petcore/ui/pet_window.py` | 同上 | `_idle_low_fps_allowed` 排除键盘驱动/循环动作/漫游，`drive_keyboard` 每次按键重置 `_last_activity`（松手后不会立刻半速）；`_apply_idle_divisor` 只给支持隔帧的后端下发并记录真实值；`_stop_clip` 复位旧 clip 的 divisor；`_play_spec` 无条件下发 `playback_speed`；新增 `apply_playback_speed`。 |
| `petcore/ui/app.py` | +208/−5（含前序） | `_apply_live_to` 把 `playback_speed` 推给当前窗口。 |

### 1.3 朋友的酒 BPM

| 文件 | +/- | 改了什么 / 为什么 |
|---|---|---|
| `petcore/config.py` | +137/−3（含前序） | 新全局键 `music_buddy_wine_bpm`，默认 132，归一化钳 60–200。 |
| `petcore/settings_schema.py` | 新增 530 行 | 标签「朋友的酒 BPM」与范围 `(60, 200)`。 |
| `examples/petcore-mods/media.music/host/runtime.py` | +105/−0（含独立节拍器） | `BUDDY_WINE_BPM_DEFAULT=132`、`buddy_wine_interval_seconds()`；`_tick_buddy_wine` 负责启停，`scheduler.call_every(60000/bpm)` 独立节拍器负责逐拍 Q 弹（1Hz 判定拍不能限速 132/200 BPM）。 |
| `examples/petcore-mods/media.music/host/settings_page.py` | +46/−0 | 「弹跳节奏」QSpinBox（60–200 BPM），快照/回滚/搜索别名。 |
| `examples/petcore-mods/media.music/mod.json` | +1/−1 | 1.3.0 → 1.4.0。 |

### 1.4 致谢与自语

| 文件 | +/- | 改了什么 / 为什么 |
|---|---|---|
| `petcore/community/model.py` | +34/−4 | `AuthorAccount.role`；`_parse_accounts` 读取 role；`DEFAULT_*` 与 JSON 同步。 |
| `petcore/community/data/credits.json` | +28/−8（含前序） | 张墨林 B站/抖音 `桌宠独立端作者`；浩瀚星空2005 移入作者区（`初始动画素材作者`），从特别鸣谢删除。 |
| `petcore/ui/settings/page_about_thanks.py` | +71/−8（含前序） | 作者标题 `handle · role`，正文字号；平台作小字副行；删除底部「张墨林制作框架…」小字。 |
| `petcore/config.py` | 同上 | `_LEGACY_SELF_TALK_LINES`：`欧金金` → `欧鲸鲸……`，只做整条精确替换。 |
| `scripts/fetch_community_credits.py` | +6/−1 | 刷新 GitHub 账号时保留 `role`，避免刷新后角色丢失。 |

### 1.5 手机版对话窗

| 文件 | +/- | 改了什么 / 为什么 |
|---|---|---|
| `petcore/ui/chat/phone_window.py` | 新增 202 行 | 440×560 / 560×1000 / 460×780；新建按钮移到底部 chips 最右；标题栏 `[⋯][✕]`；菜单新增「新建会话」；四个图标按深浅色重绘。 |
| `petcore/ui/chat/window.py` | +122/−12（含前序） | `_update_bubble_widths` 扣除右侧 12px 内边距；assistant 正文最小宽 `min(360, available−12)`；滚动条 `rangeChanged` 后重算。 |
| `petcore/ui/chat/widgets.py` | +68/−1（含前序） | 构造时不再硬钉 360 最小宽，由窗口按真实视口算。 |
| `petcore/ui/chat/styles.py` | +51/−12（含前序） | assistant `padding: 8px 12px 8px 0`；phone-new 36px 圆形；phone-close 浅/深色 hover。 |

### 1.6 测试与文档

- 新增/更新测试：`tests/test_petcore_keyboard_drive.py`（新增 314 行）、
  `tests/test_petcore_legacy.py`（+38）、`tests/test_petcore_idle_fps.py`（+112）、
  `tests/test_petcore_media_webp.py`（新增 209 行）、`tests/test_petcore_buddy_wine.py`
  （新增 133 行）、`tests/test_petcore_music_mod.py`（新增 `_Scheduler` 替身 +6/−1）、
  `tests/test_petcore_self_talk.py`（+17）、`tests/test_petcore_chat_phone.py`
  （新增 269 行）、`tests/test_petcore_about_thanks.py`（+60）、
  `tests/test_petcore_thanks_page.py`（+4/−1）、`tests/test_petcore_community_bundle.py`
  （+26）、`tests/test_petcore_community_store.py`（+30）。
- 文档：`docs/PETCORE-SPEC.md` §34、`docs/PETCORE-MOD-FORMAT.md` 的
  `move_strides.json` / `segments`、`docs/PETCORE-CHARACTER-PACKS.md` §4 的 segments、
  `docs/PETCORE-ARCHITECTURE.md` 的移动分段与降帧、
  `docs/SETTINGS-CHANGE-GATES.md` 的 BPM 键、`docs/PETCORE-LOG.md` L199、`docs/INDEX.md`。

## 2. 性能分析

### 2.1 2 倍速帧率（真实播放器，非 mock）

- 命令：`QT_QPA_PLATFORM=offscreen PET_PETCORE_COST_STATS=1 .venv/Scripts/python.exe`，
  脚本实例化真实 `PetWindow` + `shenshen`（640×360，24fps），`playback_speed=2.0`，
  1.5s 预热后采样 2.0s，统计 `_cost_frames`。
- 结果：**47.5 上屏帧/秒**，`FrameSeqClip._interval_ms()=21`；与 2x 目标 47.6fps 一致。
- 对照：闲置 31s 模拟后 divisor=2，上屏 24fps，但帧序列每拍前进 2 帧，源时间线仍是
  48 帧/秒——观感仍是 2x，不是旧实现的「时长真的翻倍」。
- 唱歌 WebM（`悠闲哼歌`）：1x **24.0fps**、2x **48.0fps**；模拟 31s 闲置后
  `supports_frame_divisor=False`、`_idle_low_fps_active=False`，3s 采样 **45.67fps**
  （圈边界 re-arm 有少量丢拍），不再掉到 12fps/半速。

### 2.2 键盘移动的每圈位移

- 探针：`load_character('shenshen')` + `drive_keyboard({'right'})`，真实 `FrameSeqClip`
  241 帧。结果：`MoveSegments(start=0, loop_start=56, loop_end=104, end=240)`，
  按下帧 **56**，松手帧 **105**。
- 每圈位移：`240 × 0.72 × (0.7966 − 0.0034) = 137.1px / 49 帧`；旧实现
  `240 × 0.72 = 172.8px / 62 帧`。两者速度 67.1 vs 66.9 px/s（差 <0.3%），缩短循环段
  没有改变移动速度。
- 审查子代理发现并修复「每圈少计 1 帧」：回绕那一拍（下一圈的 loop_start）现在先
  `_drive_frames += 1` 再 jump。实测 12s 按住：`_drive_frames=265`（=5.408 圈），
  位移 **740px**，按 137.064px/圈期望 741.3px（99.83%，差值为像素取整）；修复前是
  48/49 ≈ 98.3% 的脚底打滑。

### 2.3 新增路径的稳态成本

- `_apply_idle_divisor`：1Hz 检查 + 切 clip 时一次；新增 `supports_frame_divisor`
  getattr 与一次 int 比较，无系统调用、无新线程/定时器。
- `build_drive_plan`：每次开始键盘驱动一次 `_loop_curve_span`（两次 float 索引）。
- `move_segments`：每个角色加载时解析一次 `move_strides.json`（与原有 stride/curve
  同一文件），内存多一个 `dict[clip, dict]`（本例 1 条）。
- 朋友的酒：模式开启时新增一个重复 `QTimer`（`scheduler.call_every(60000/bpm)`，300–1000ms），1Hz 判定拍只负责启停与首拍；每拍一次 `60 / bpm` 浮点除法。模式关闭/暂停即取消，不常驻；无系统调用、无新线程。
- 手机版：`_update_bubble_widths` 实测 **7.3µs/次**（20 条纯 assistant 气泡，offscreen，
  100 次均值）；20 条含长 user 气泡时 **130–160µs/次**（成本在既有 `preferred_width`
  字体测量）。新增触发只在 `rangeChanged`（60 次内容更新触发 10 次），稳态空闲 0 次。
- 内存：没有新增帧缓存/大数组；`SheetClip` 的逐帧缓存仍是 4 帧上限。

### 2.4 取舍

- WebM/单文件 WebP 不再参与闲置降帧：它们的 `set_decode_throttle` 是「降速 + reader
  背压」，会把时长真的拉长 2 倍。代价是 WebM 闲置动画保持全速；shenshen 的稳态待机
  是帧序列，仍享受 24→12fps 的隔帧节能。若后续要压 WebM 静息成本，需要单独改
  WebM 消费端为「隔帧采样」并保留圈末/背压不变量，不能复用当前接口。

## 3. 实机运行记录

环境：Windows 本机，仓库 `.venv`，Qt offscreen（无显示器），2026-11-15。

### 3.1 键盘移动

```
clip FrameSeqClip frames 241
segments MoveSegments(start=0, loop_start=56, loop_end=104, end=240)
after press frame 56
rendered ... 102, 103, 104, 56, 57 ...   # 104 正常显示，接缝无重复 56
after 9s hold: saw 104 True, saw 105 False
release frame 105 releasing True
```

说明：按住时只在 56..104 循环，圈末 104 先画出来、下一帧越界才跳回 56，不进入绊倒帧；
松手从 105 开始播「绊倒→趴地→起身」。审查子代理的探针最初发现「104 被跳过、56 连画
两次」，修复后按实际渲染帧复测为上面的序列。

### 3.2 2 倍速与唱歌

```
2x displayed fps 47.5  interval 21
idle active True divisor 2 -> 2x + idle displayed fps 24.0
sing 1x fps 24.0 interval 42
sing 2x live fps 48.0 interval 21
sing after 31s idle active False fps 45.67
```

### 3.3 手机版

- 初始 460×780：viewport 444 → available 420 → 气泡 420、正文 408（只扣右侧 12px），
  比旧版 380 的正文 333 多 75px，约 5.8 个 13px 汉字；长段 `body.height()=405`、行距 15，
  约 27 行；左侧 padding 0、右侧 12px，文字靠左。
- 最小 440×760：viewport 417 → available 393 → 气泡 393、正文 381；长段仍能折行，
  不低于 5 行（测试断言）。
- 按钮：`phone_new_button.parent() is phone_view_bar`，view bar 末项是它；
  title bar 末两项是 `phone-more-button`、`phone-close-button`；浅/深色切换后
  close 图标 cacheKey 变化。
- 成本：`_update_bubble_widths` 7.3µs/次（20 条 assistant）；无新增线程/定时器。

### 3.4 朋友的酒 BPM

- 审查子代理发现并修复：节拍原先寄生在 1Hz 判定拍上，60–200 BPM 全区间都退化成
  1 次/秒。现在用独立 `scheduler.call_every(60000/bpm)` 节拍器，`poll` 只负责启停与首拍。
- 132 BPM：定时器 **455ms**；0.4s 不发、0.5s 发第二拍。
- 60 BPM：**1000ms**；0.5s 不发、1.1s 发第二拍。200 BPM：**300ms**；0.15s 不发、
  0.31s 发第二拍（测试直接驱动调度器回调，不再用 1Hz `poll` 冒充节拍）。
- 暂停/停止：取消定时器、不发；拖拽/抛掷/探头/击飞：`PetWindow.squash()` 返回 False，不叠加。

### 3.5 聚焦测试

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest -q \
  tests/test_petcore_keyboard_drive.py tests/test_petcore_legacy.py \
  tests/test_petcore_idle_fps.py tests/test_petcore_media_webp.py \
  tests/test_petcore_frame_pacing.py tests/test_move_sync.py \
  tests/test_petcore_buddy_wine.py tests/test_petcore_music_mod.py \
  tests/test_petcore_settings_cli.py tests/test_petcore_settings_coverage.py \
  tests/test_petcore_about_thanks.py tests/test_petcore_thanks_page.py \
  tests/test_petcore_community_bundle.py tests/test_petcore_community_store.py \
  tests/test_petcore_chat_phone.py tests/test_petcore_self_talk.py   tests/test_petcore_docs.py tests/test_pr_report_discipline.py
=> 682 passed
```

`ruff check` 本批 31 个文件 => `All checks passed!`

### 3.6 B 站完整名单

本机钥匙串没有 `community.bilibili/sessdata`（只读检查），本轮未拉完整名单：
`fans.json` 仍是 total 4788、names 3948（名单 2026-10-27）。用户已选择在设置页
粘贴 SESSDATA；凭据出现后运行 `scripts/refresh_community_snapshot.py` 即可补全。
页面口径：总数与名单分开显示，差额不再叫「同名占位」，改「B 站显示 4788 位」。

### 3.7 独立审查修复

独立审查子代理在最终代码上复跑并指出 6 项，已全部处理：

1. **P1 朋友的酒 BPM 被 1Hz 判定拍钉死**：`_tick_buddy_wine` 只挂在 `_on_decision`，
   20 次 1s 采样对 61/132/200 BPM 都只弹 20 次。改为独立
   `scheduler.call_every(60000/bpm)` 节拍器，`poll` 只负责启停与首拍；测试用调度器
   替身 `fire()` 驱动，132/60/200 BPM 分别断言 455/1000/300ms。
2. **P2 键盘移动每圈少计 1 帧**：回绕那一拍（下一圈 loop_start）原先只 jump 不计数，
   10 圈少走 1 帧/圈（48/49）。改为先 `_drive_frames += 1`/推进位置再 jump；实测
   12s 按住 265 帧（5.408 圈）位移 740px，期望 741.3px（99.83%）。
3. **P3 按键不刷新交互时间**：`drive_keyboard` 非空 keys 先 `_note_activity()`，松手后
   待机不会立刻掉到 divisor=2。
4. **P3 无 MOVE 素材时每 16ms 重试**：`_drive_holding` 只在 `_drive_active` 时置位，
   不再留下 stale holding；仍有每拍 `specs_for` 扫描（自定义无移动素材角色，已知边界）。
5. **P3 平坦循环段按整 stride 滑行**：`_loop_curve_span` 现在 clamp 到曲线长度，
   平坦段返回 0.0（位置不前进），并加回归测试。
6. **P4 未知致谢 kind 静默消失 / phone_view_bar.hide 连按钮一起藏**：未知 kind 归
   「其他」；手机版不再 hide 整条 bar。

## 4. 全量回归与出包

- setup：`rel-storage-2026-11-15b/dsh-pet-core-setup.exe`，**351,230,926 B（334.9 MiB）**，
  SHA-256 `E3F3766F287D1CFBEAD6C98995F237E5C4EC4DF2E41324B8AA3588E43F6C816C`，
  `Get-AuthenticodeSignature = NotSigned`（按用户要求不签名）。随包
  `petcore/community/data/fans.json` 已核对为 **names 4785 / total 4788**（2026-11-15）。
- 构建内 `--check --strict` 退出 0：`check: menu_template=json characters=1
  builtin_images=24 daily_lines=1200 strict=True`；`check.json` 的 `problem=None`、
  `strict.problems=[]`、`aboutme_packs=2`。
- 冻结包 CLI 冒烟：`dsh-pet-core.exe settings list --json --query 朋友的酒 --root .scratch/cli-batch7-root`
  退出 0，命中 `music_buddy_wine_bpm`（type=int、default=132、min=60、max=200）。
- 全量：`QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest -q` =
  **11917 passed, 22 skipped, 6 failed（31:17）**。6 例均为既有失败，不在本批范围：
  4×`tests/test_bundle_slim.py`（bundle 缺 QtWebView DLL）、
  `test_desktop_pet_features.py::test_product_copy_has_no_external_brand_reference`、
  `test_packaging_keyring_notices.py::test_windows_build_script_keeps_its_utf8_bom`。
  本批聚焦 682 passed 全绿。

## 5. 未验证 / 风险

- WebM 闲置动画保持全速，静息 CPU 略高于旧实现；shenshen 稳态待机是帧序列，仍降帧。
- 长 ASCII / 代码块 / 表格的右侧裁切属于 QLabel 富文本边界，本轮只验证中文长段；
  若仍复现，需要换 QTextBrowser/注入断行机会。
- 手机版只在 offscreen Qt 验证布局与配色，未在真实显示器上目视；未做 30 分钟浸泡。
- 逐条朗读/音乐识别仍受上一批边界（未配置语音后端听声、真实音乐未长时观察）。
- B 站名单已刷新到 4785/4788；运行期缓存与随包快照仍按「较新且非空」取一份。
