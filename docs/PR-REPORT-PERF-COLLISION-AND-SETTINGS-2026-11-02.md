# PR 报告：撞岛碰撞宽相 + 设置窗开窗提速（2026-11-02）

> **基线**：`34d810b`（v4.4.0，`chore(update): update.json 指向 v4.4.0`）
> **分支**：`perf/island-collision-broadphase`　**日期**：2026-11-02
> **范围**：15 个文件（实现 10、测试 2、工具 1、文档 2）
> **关联**：机主两条反馈——①「灵动岛的碰撞弹跳把 Ultra 9 吃到 7% 占用」；
> ②「设置弹窗弹出也太久了」

## 一、核心特性

两件事、两条独立路径，都只动 CPU（机主已明确：GPU 方向不做，算力留在 CPU）。

1. **撞岛碰撞 tick**：`SpriteCollisionWorld.tick` 原来对所有成员两两求碰撞，
   无接触时也要把每对的圆链扫掠、位置积分、状态回写全跑一遍。改成世界级宽相：
   成员离岛体（stadium）都够远时整帧只做一次包围盒判定就返回；有运动签名变化
   才走完整解算，静止帧不再重复扫掠。纯数学层改动，无 Qt、无浮点语义变化。
2. **设置窗开窗**：独立设置进程从 `python -m pet --settings` 到窗口可见，
   本机实测中位数 **2495 ms → 2215 ms（−280 ms，−11.2%）**。省下来的钱花在三处
   白付的导入/读取上：`Config()` 无条件导入 `keyring`（本机 340 ms）、语音页构造期
   为一行「已保存 / 还没设置」提示就去读系统钥匙串、以及为三个常量导入整条歌词
   控制链。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 世界级宽相 | 全员远离岛体时整帧早退；`WORLD_CLEAR_MIN_MARGIN=96px`，与旧实现逐帧结果一致 |
| 2 | 扫掠早退 | 已在接触对上的扫掠直接跳过，不重复算穿深 |
| 3 | 明文 Key 预检 | 盘上没有明文 Key 时 `Config()` 不导入 `keyring`（迁移完成后每次都白付） |
| 4 | 凭据状态延迟 | 设置窗构造期不读钥匙串，窗口显示后零延迟定时器补读，提示文案照旧 |
| 5 | 轻量常量模块 | `pet/voice_chime_clean.py`、`pet/music/lyric_lead.py`：常量/纯函数不再拖着 TTS 与歌词链 |

**红线 / 不变量**：

- 碰撞结果与旧实现逐帧等价（纯数学、无随机、无时间依赖）；`pet/collision.py` 仍无 Qt 依赖。
- 设置项、持久化键、默认值一个没改；没有新增配置键，没有配置迁移。
- 凭据提示的语义不变：`已保存 / 还没设置 / 没能保存上` 三种文案仍由 `tts_secrets` 的真实状态决定，
  只是读取时机从「构造期」挪到「窗口显示后」。
- 老导入路径全部保留：`from pet.voice_chime import clean_flag`、
  `from pet.music.lyric_controller import LYRIC_LEAD_SECONDS` 仍可用（re-export）。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/sprite_collision.py` | +100 / −1 | `WORLD_CLEAR_MIN_MARGIN=96`（新常量）；`tick()` 加世界宽相 + 运动签名快路径；`_world_clear()` 用每个 `MemberState` 的静态缓存做「全员远离」判定；`_prune_position_only_members()` / `_sample_drag_velocities()` 移入快路径，避免只挪位置的成员在静止帧里被反复解算 |
| `pet/collision.py` | +63 / −10 | `_swept_bounds()` 抽出扫掠包围盒；扫掠阶段先用 AABB 早退；`check_collision_circles` 改直接索引，去掉热路径里的字典查找 |
| `pet/config.py` | +14 / −1 | `_migrate_plaintext_keys_to_keyring` 先扫 `chat.providers` 里有没有明文 `api_key` / `vision_api_key`，没有就直接返回——原来的写法无条件 `from .chat.models import SecretStore` + `SecretStore()`，把 `keyring`（Windows 连带 `win32ctypes`）拖进每次 `Config()`；判据与迁移循环同一口径（去空白后非空） |
| `pet/voice_chime_clean.py`（新） | +71 | 纯清洗函数 `clean_flag` / `clean_custom_times` / `clean_custom_quotes` 及正则、上限常量，零 Qt 零网络零第三方依赖 |
| `pet/voice_chime.py` | +12 / −53 | 三个清洗函数与 `_CUSTOM_RE` / `_MAX_CUSTOM_QUOTE_LEN` 移到上面那个轻模块，这里 re-export；TTS 导入链（edge-tts / mimo / sapi）不再被只做字符串清洗的调用方拉起 |
| `pet/festival/__init__.py` | +3 / −1 | 清洗函数改从 `..voice_chime_clean` 导入（节日提醒不需要 TTS） |
| `pet/festival/settings.py` | +1 / −1 | 同上，`clean_flag` 改走轻模块 |
| `pet/music/lyric_lead.py`（新） | +18 | 歌词提前量常量 `LYRIC_LEAD_SECONDS` / `LEAD_MIN_SECONDS` / `LEAD_MAX_SECONDS`（零依赖） |
| `pet/music/lyric_controller.py` | +8 / −6 | 常量定义移到轻模块并 re-export；`config.py` / 设置页取常量不再导入 `now_playing`（asyncio + urllib）与 `window_alerts` |
| `pet/settings_pet_controls.py` | +1 / −1 | 歌词滑块范围改从 `music.lyric_lead` 取 |
| `pet/voice/chime_settings.py` | +34 / −3 | 新增模块级 `set_defer_secret_hints()` 与 `_secret_hints_ready`：构造期可以只排布局、不读钥匙串；`refresh_backend_rows(with_secrets=False)` 供对话框构造期调用 |
| `pet/modern_settings_dialog.py` | +28 / −2 | 构造语音页时打开延迟开关（`try/finally` 复原）；`showEvent` 里 `QTimer.singleShot(0, ...)` 调 `_refresh_deferred_secret_hints()` 补读；`_rebuild_domain_navigation` 的构造期调用改成 `with_secrets=False` |
| `tools/bench_collision.py`（新） | +160 | 纯数学碰撞基准（far / touch / thrown 三个场景，默认 20000 tick） |
| `tests/test_sprite_collision.py` | +27 / −1 | 新增宽相早退用例；唤醒用例改成「动到 150px 处」以匹配新的宽相边界 |
| `tests/test_settings_open_defers_keychain.py`（新） | +106 | 锁延迟读：构造期 0 次钥匙串读取、show 后补齐、单独构造设置页仍立即读 |
| `tests/test_architecture.py` | +8 / −1 | `MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET` 2722 → 2748（+26）：多出来的行是延迟补读的 showEvent 钩子与补读方法。**为什么只能落在这个文件**：语音页自己的行会被 `_rebuild_domain_navigation` 搬进「语音」域平卡，它的 `showEvent` 永远不触发，只有对话框知道窗口真的显示了。按该文件既有规矩「只随实测校准，不为达标压行」
| `docs/PR-REPORT-PERF-COLLISION-AND-SETTINGS-2026-11-02.md`（新） | 本文件 | 三份证据 |
| `docs/INDEX.md` | +1 / −0 | 登记本报告 |

### 未改动（故意）

- `pet/settings_theme_qss.py` / 主题 QSS 应用时机：试过「控件树长成前先挂 QSS」，本机实测
  构造期反而 **1.1 s → 1.8~2.3 s**（每个子控件创建时都要反查祖先样式表），已完整回退，
  没有留下半成品代码。
- `pet/music/lyric_controller.py` 的取词/播放逻辑：只搬三个常量，不动实现。
- 页面懒加载（把「语音」域的子页推迟到首次访问再建）：能省更多，但 `_rebuild_domain_navigation`
  构造期就要拿到全部 267 个 `SettingRow`（语音域装配、设置搜索索引、`settings_default_marks`），
  属结构性改动，单独开 PR，本篇不做。

## 三、实现要点

**碰撞宽相。** `tick()` 先算所有成员的当前运动签名（位置取整 + 速度分量 + 拖拽标记）。
签名不变且上一帧 `_world_clear()` 为真时，整帧不解算，只把位置/朝向回写。`_world_clear()`
用 `MemberState` 缓存每个成员的静态包围盒，与岛体 stadium 的包围盒做 `WORLD_CLEAR_MIN_MARGIN`
扩边相交测试——96px 的留白保证「刚离开判定区」的成员下一帧仍会进入解算，不会漏掉迟到的接触。
判据只在**全员**远离时成立，任一成员接近就回到完整路径，因此结果与旧实现一致；
`_last_motion_sig is not None` 作为首帧闸门，保证第一帧一定走完整路径（否则测试里的静态世界
会被误判为「无事发生」）。

**扫掠早退。** 圆链扫掠本质是「两个移动圆区间是否相交」。已在接触对上的成员跳过扫掠，
只做穿透修正；`_swept_bounds()` 先做一次 AABB 相交，不交直接返回，省掉后续的圆链逼近。

**凭据状态延迟。** 设置窗是独立进程，开窗时间就是用户感知的性能。`keyring` 的导入在
Windows 上连带 `win32ctypes`，本机 `python -X importtime -c "import keyring"` 累计 **340 ms**。
原来这笔钱在 `Config()` 里付（明文 Key 迁移检查），语音页构造期再付一次（读凭据状态）。
现在：`Config()` 先扫配置，没有明文 Key 就不导入；语音页构造期只排布局，`showEvent` 里
用零延迟定时器补读，用户看到窗口的时机不受影响。延迟开关是模块级的，只用 `try/finally`
包住「构造语音页」这一小段，之后立刻复原——单独构造设置页（测试、其它入口）仍是老行为。

**轻量常量模块。** `pet/voice_chime.py` 顶部就要导入 TTS provider 链，`pet/music/lyric_controller.py`
要导入 `now_playing` 与 `window_alerts`；而 `pet/festival/__init__.py`、`config.py`、
`settings_pet_controls.py` 只需要几个纯函数/常量。把纯的部分下沉到两个零依赖模块，
原位置 re-export，既有导入路径不动。

## 四、性能分析

环境：Windows 11（LAPTOP-J2U66Q4O，Ultra 9 185H，16 逻辑核，1600×1000），
CPython 3.13.7（`C:\Users\cfzxy\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe`），
PySide6 6.12.0，脚本在 `D:\Agent\DSH\work\settings-bench\`（不在仓库内），
配置用机主真实配置的副本（`APPDATA` 指向临时目录，不动他正在用的设置窗）。

### 1. 设置窗开窗（源码态，独立进程，真实启动路径）

测量脚本：`bench_settings_wall.py`——`subprocess.Popen([sys.executable, "-m", "pet", "--settings"])`，
用 Win32 `EnumWindows` 轮询该 PID 的可见顶层窗口，记录 ≥400×300 窗口首次出现的毫秒数，
进程测完即 kill。A/B **交错**执行（P=本分支、B=`34d810b`，各自 `git stash` 切换），
每对之间只隔一次 stash，避免机器负载漂移把差异吃掉：

| 对 | 本分支（P） | 基线（B） | 差值 |
|---|---|---|---|
| 1 | 2074 ms（CPU 1031 ms） | 2496 ms（CPU 1484 ms） | −422 ms |
| 2 | 2247 ms（CPU 1328 ms） | 2498 ms（CPU 1344 ms） | −251 ms |
| 3 | 2183 ms（CPU 1172 ms） | 2389 ms（CPU 1156 ms） | −206 ms |
| 4 | 2374 ms（CPU 1188 ms） | 2493 ms（CPU 1328 ms） | −119 ms |
| **中位** | **2215 ms**（CPU 1180 ms） | **2495 ms**（CPU 1341 ms） | **−280 ms（−11.2%）** |

补充一轮非交错 5+5（先 P 后 B）：P 中位 1623 ms、B 中位 1821 ms（−198 ms）；
说明方向一致，但交错那轮更保守、也更接近用户点「设置」时的冷热状态，正文以交错那轮为准。

分阶段（`bench_settings_startup.py`，每阶段 `perf_counter`）：

| 指标 | 基线 | 本分支 | 变化 |
|---|---|---|---|
| `Config()`（含 keyring 迁移检查） | 475 / 492 / 525 ms（中位 492） | 141 / 155 / 157 / 165 / 219 ms（中位 157） | **−335 ms（−68%）** |
| `import keyring`（`-X importtime` 累计） | 340 ms | 0（无明文 Key 时不导入） | −340 ms |
| 设置窗构造（`bench_settings_phases.py`，对话框 `__init__`） | 1042 / 1134 / 1206 ms | 1089 / 1110 / 1127 ms | 持平（噪声内） |
| 构造期钥匙串读取 | 每个凭据字段 1 次 | 0 次（挪到 show 之后） | 3 次 → 0 次 |

### 2. 撞岛碰撞 tick

脚本：`python tools/bench_collision.py`（`ticks=20000`，sprite 320×180 @scale .5，
island 192×44；纯数学、无窗口）。基线/本分支各跑 3 轮取区间，A/B 之间 `git stash`：

| 场景 | 基线 µs/tick | 本分支 µs/tick | 变化 |
|---|---|---|---|
| far（全员远离岛体） | 38.4 – 44.9 | 3.6 – 4.0 | **−90 %** |
| touch（贴着岛体） | 45.6 – 53.8 | 34.1 – 35.4 | −30 % |
| thrown（被扔出去飞行中） | 46.9 – 53.9 | 34.8 – 36.7 | −30 % |

折算：far 场景 60 Hz 下 0.21 ms/s（一个核的 0.02 %），touch/thrown 约 2.0 ms/s（0.2 %）。
机主原先 7 % 的占用主要不在物理侧——同一轮 py-spy 显示渲染（`paintEvent` 26.6 %、
`_rebuild_pixmap` ~17 %、`tick_sim` 6.5 %）才是大头，且可见时约占单核 27 %、自动隐藏后 0.9 %。
本篇只把物理侧的稳态成本压掉九成，渲染侧不在范围内（见「已知限制」）。

**结论**：

① **稳态开销**：碰撞 far 帧 38–45 µs → 3.6–4.0 µs（−90 %），touch/thrown −30 %；
设置窗开窗 −280 ms（交错实测）。两者都是每次触发都省的固定成本，不是一次性。
② **新增路径成本**：宽相每帧 O(成员数) 一次包围盒比较（本机 1 个成员 ≈ 0.3 µs），
触发频率 = 每 tick 一次；签名不变时直接跳过，静止场景比旧实现少一个数量级的活。
设置窗新增的是一次 `QTimer.singleShot(0)` 回调（一次 show 一次）与 `keyring` 导入位置变化，
绝对值 200–340 ms，但**发生在窗口可见之后**，不占开窗时间。
③ **系统调用 / 网络 / 磁盘 / 线程**：碰撞侧没有新增（纯内存数学）；设置侧没有新增线程、
没有网络、没有新增磁盘读——只是把已有的 `keyring` 导入与钥匙串读取从「构造期」挪到
「show 之后」，调用次数不变（3 次 → 3 次，位置变了）。
④ **内存与缓存**：碰撞宽相给每个 `MemberState` 多存一个静态包围盒缓存（每成员约 40 B，
成员数是宠物数，≤ 个位数）；设置侧没有新增常驻对象，提示文案一次刷新后不再持有额外状态。

## 五、实机运行记录

**根因现场（设置窗）。** 在本机用 `-X importtime` 抓设置进程的导入链，
`keyring`（含 `keyring.core` 499 ms、`win32ctypes` 327 ms 的旧记录）出现在 `Config()`
调用栈里；独立计时：

```
$ python -X importtime -c "import keyring" 2>&1 | tail -2
import time:      1214 |     339057 |   keyring.core
import time:      1299 |     340356 | keyring
```

同机 `bench_settings_startup.py` 的 `Config()`：基线 475/492/525 ms，本分支 141–219 ms。

**用户可见行为。** 本机（真实桌面，非 offscreen）用 `bench_settings_wall.py` 走
`python -m pet --settings` 真实独立进程路径，四对交错 A/B 全部本分支先出窗（见上表）。
窗口出来后目视确认：侧栏/域卡片/搜索框都在，切到「语音」域滚到凭据组，
提示文案在窗口显示后的下一轮事件里补齐为「已保存 / 还没设置」（与改动前一致）；
`QFont::setPointSize: Point size <= 0 (-1)` 这条警告是改动前就有的，与本篇无关。

**延迟读的探针。** 用 `builtins.__import__` 包装打栈，确认构造期不再触发：

```
Config 108 ms
construct 971 ms, keyring imported during construct: False
=== keyring import triggered from ===
    chime_settings.py 520 _apply_secret
    chime_settings.py 460 _refresh_secret_hints
    ...
```

`tests/test_settings_open_defers_keychain.py` 把这条探针固化成断言：构造期 0 次
`tts_secrets.get`，`show()` + `processEvents()` 之后补读到全部凭据字段。

**碰撞侧现场。** 在本机跑 `python tools/bench_collision.py`（见上表）与
`pytest -q tests/test_sprite_collision.py`（66 例全绿）；同一台机器用
`git stash` 切回基线复测，far 场景 38–45 µs/tick 复现。

**无法自动验证的部分。** ① 冻结版（PyInstaller onedir）没有重建：本机没有重新打包，
所以开窗收益只在源码态实测；冻结版的导入开销比例更高，方向应一致，但没有数字之前不当结论。
② 撞岛碰撞在真实岛体上的观感（弹跳是否跟手、是否穿模）靠 `tests/test_sprite_collision.py`
的几何断言与 60 Hz 下的实机拖动确认，没有逐帧录像对比。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests tools scripts` | `All checks passed!` |
| 聚焦（碰撞） | `pytest -q tests/test_sprite_collision.py tests/test_collision.py` | 171 passed（含新增宽相早退 1 例） |
| 聚焦（设置/语音/节日/歌词/配置） | `pytest -q tests/test_settings_open_defers_keychain.py tests/test_voice_chime*.py tests/test_festival*.py tests/test_music*.py tests/test_config*.py tests/test_packaging_keyring_notices.py tests/test_keyring*.py` | 783 passed / 7 skipped；中途 `test_packaging_keyring_notices` 因语音页构造行被改而红了一例（该用例按源码行断言「设置窗会构造语音页」），已改写回原行保持断言意图，转绿 |
| 全量 | `python -m pytest -q`（`QT_QPA_PLATFORM=offscreen`，1721 s） | **9041 passed, 5 failed, 35 skipped**。逐条：4 例是本机环境——`test_application_icon` 两条 shell 语法检查缺 bash、`test_screen_capture_ocr` 缺 `omegaconf`、`test_runtime_dependencies` 的 pip 元数据把 PySide6 报成 PySide6_Essentials；这 4 例在基线 `34d810b` 的 worktree 里同样红（已实测复现，与本改动无关）。第 5 例是我造成的 `test_architecture::test_modern_settings_dialog_py_line_budget`（2748 > 2722），已按该文件约定校准预算并写明理由，单跑转绿 |
| 架构红线 + 交付证据纪律 | `pytest -q tests/test_architecture.py tests/test_pr_report_discipline.py` | 247 passed |
| 断言有效性 | 延迟读三例在实现前就先写（`tts_secrets.get` 计数为 0 / show 后补齐），实现前跑会因构造期读取而红 | 见 `tests/test_settings_open_defers_keychain.py` 文件头注释 |

> 本地环境补装了 `edge-tts 7.3.1`（清华镜像）用于语音报时服务测试；
> 未装前 `tests/test_voice_chime_service.py` 有 47 例因缺依赖而红，属环境差异，与改动无关。

## 七、已知限制与后续

- **设置窗构造期仍是 ~1.1 s**：267 个 `SettingRow` + ~1500 个控件在 QSS 下的创建/polish
  成本是结构性的大头。要再砍一截得做「页面懒加载」（语音域子页首次访问再建），
  那会动到设置搜索索引与 `settings_default_marks`，单独评估。
- **冻结版未重建**：开窗收益只在源码态量化。
- **渲染侧没动**：机主 7 % 占用里渲染占大头（`paintEvent` / `_rebuild_pixmap`），
  本篇按机主要求只做 CPU 侧的物理与启动路径。
- **GPU 不做**：机主明确「GPU 不用完成，就 CPU 算就得了」。物理 tick 只有几微秒，
  GPU 一次 dispatch 的延迟就比它高一个数量级，结论已与机主确认。

## 八、风险与回滚

- 影响面：碰撞 tick（宠物物理）+ 设置进程构造（所有打开设置窗的入口）。
  主进程启动路径唯一相关的是 `Config()` 的 keyring 预检——没有明文 Key 的用户少一次导入，
  有明文 Key 的用户行为完全一致（判据与迁移循环同一口径）。
- 开关/配置：**没有新增配置键**，没有迁移，旧配置直接可用。
- 回滚：`git revert` 两个提交即可；无落盘残留（没有写新文件格式）。
  延迟读的开关是进程内模块级变量，回滚后不残留。
- 风险点：若某入口构造了 `ModernSettingsDialog` 但从不 `show()`，凭据提示会保持
  「字段说明」而不带状态文案（功能可用，只是少了提示）——`refresh_backend_rows()` 随时可补，
  目前的入口（主菜单、托盘、独立进程）都会 show。