# PR 报告：#271 主宠设置会话路由 slot0（空身份不再静默丢每宠键）

> **基线**：`579b401`（main，v4.3.0 后的 CI 修复批）
> **分支**：`fix/271-slot0-settings-routing`　**日期**：2026-10-15
> **范围**：24 个文件（实现 5、测试 19）
> **关联**：Issue #271（主宠设置页每宠键保存不生效）；D13 逐 sprite 设置路由；G1 slot0 档案身份

## 一、核心特性

Issue #271：主桌宠（slot 0）设置页改「出图概率 / 气泡样式 / 点击朗读」等每宠键
保存报成功但不生效，重开设置页回显出厂默认；子宠完全正常。

根因（本机双臂实验实证）：设置页配置面经 `route_settings_config` 路由时，
**空身份（`instance_id=""`，主宠会话）原样返回不分流**，而 `Config.save()`
空身份分支把 `PER_PET_KEYS` 当退役键从 payload 剔除——档案已存在时每宠写入
**静默蒸发**（config.json 不写、slot0 档案不写、`save()` 仍返回 True）。
重开读不到键 → 回源默认。实验：空身份会话 `set("self_talk_image_chance", 9)`
+ `save()` 返回 True，但 `config-slot-0.json` 保持 30、`config.json` 无此键。

修法：空身份设置会话一律按 **slot0 分流**——这正是 v4.3.0 生产 D13 路径
（`--instance slot-0`）的既有行为，本 PR 把"主/子宠页同构"推广到所有入口，
消灭空身份这个分裂人格。审查返修（P1）并入：路由前先跑幂等
`migrate_slot0_profile`，未迁移目录直接进设置会话时旧每宠值不再被默认档案
抢先落盘挤掉（详见 §三）。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 主宠每宠设置真正生效 | 空身份会话每宠键落 `config-slot-0.json`，运行期照常热读 |
| 2 | 主宠页 = slot0 页（同构） | 窗口级键标注「对所有桌宠生效」、重置给「本宠(slot0) + 全局」双动作——与 D13 生产路径逐位一致 |
| 3 | 升级路径数据安全 | 未迁移目录进设置会话：先迁移（带备份）再编辑，旧值不丢 |

**红线 / 不变量**：非 slot 诊断身份（如 `probe-a`）原样返回；`config.json`
一个字节的每宠键都不许进；全局键照旧落 `config.json`；`--slot N` 兼容启动
与父进程拓扑分流（R01/F2）语义不变；已路由视图幂等。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/settings_slot_config.py` | +23 / −6 | `route_settings_config`：空身份 + 有配置目录 → 迁移幂等检查 → 新建 slot0 档案脸 + 主配置，返回 `SlotRoutedConfig`；迁移不可信退化不分流（旧行为）并 warning；模块 docstring 同步新契约 |
| `pet/settings_pet_controls.py` | +4 / −4 | `_apply_window_scope_rows` docstring：主宠页不再是例外（#271 后它也是 slot0 页），注记同口径标注 |
| `pet/settings_reset_defaults.py` | +3 / −5 | `reset_scope_is_slot` docstring 删除已过时的「legacy 拓扑原样返回」表述（与现行行为不符，本次改动前即过时） |
| `pet/modern_settings_dialog.py` | +2 / −2 | 构造注释同步：主身份也分流，删掉过时的「主身份/legacy 原样返回」 |
| `pet/__main__.py` | +1 / −1 | `_settings_instance_id` docstring：缺省空 = 主宠（按 slot0 分流） |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_slot0_ownership.py` | +74 / −1 | 新增 5 条钉新契约：空身份路由 slot0（写落档案/主配置干净/重开读回）、全局键仍落主配置、未编辑键不回灌、未迁移目录先迁移（P1 回归）；删 1 条与 `test_settings_slot_config_route.py` 重复的诊断身份用例 |
| `tests/test_settings_slot_config_route.py` | +27 / −5 | 旧契约 `test_main_pet_config_is_not_routed` 反转为 `test_main_pet_config_is_routed_to_slot0`（每宠键落档案且不进 config.json、全局键照旧） |
| `tests/test_settings_overlay_copy.py` | +16 / −5 | `test_master_page_has_no_scope_note` → `test_main_page_shows_scope_note`：主宠页（=slot0 页）与子宠页同口径标注窗口级键，措辞取自产品常量 `ALL_PETS_SCOPE_NOTE` |
| `tests/test_settings_reset_defaults.py` | +55 / −19 | 四条主宠页重置用例平移到新契约等价入口：全局文案/全局落盘/失败处理改挂显式 `RESET_SCOPE_GLOBAL`（主宠页副按钮）；tooltip 用例改为主/子页同构双断言；文件头语义契约第 6 条同步 |
| `tests/test_desktop_pet_features.py` | +16 / −10 | 三条对话框保存断言改读回口径（每宠键经 `_slot0_config` 档案视图、全局键 reload 原对象） |
| `tests/test_drop_gravity_toggle.py` | +8 / −2 | 设置页往返两条：写回从档案读、初始值改落盘预置 |
| `tests/test_requested_regressions.py` | +28 / −3 | 5 处 reload 读回、2 处全局键落盘预置、2 条每宠键改档案读回 |
| `tests/test_settings_network.py` | +19 / −2 | 共享 helper 落盘预置 + reload 读回 + `_main.path` 读盘路径 |
| `tests/test_settings_game_fps.py` | +13 / −1 | 同上形态 |
| `tests/test_weather_settings.py` | +9 / −1 | 内存预置改落盘（路由视图只从盘读）、`_main.path` 读盘 |
| `tests/test_music_player_settings.py` | +9 / −3 | 三条 child_page 读盘路径改 `_main.path` |
| `tests/test_quiet_mode.py` | +3 / −1 | 同上 |
| `tests/test_click_sound.py` | +8 / −1 | 运行期配置面改生产形态（slot-0 路由视图），预置落盘；即时生效断言未削弱 |
| `tests/test_menu_layout.py` | +3 / −0 | 内存预置补 `save()` 落盘 |
| `tests/test_settings_process_isolation.py` | +3 / −0 | 保存后 reload 再断言 |
| `tests/test_modern_settings_rows_topwindow.py` | +3 / −0 | 预置补落盘（在记录 Show 事件之前，不污染被测窗口） |
| `tests/test_effects_integration.py` | +1 / −0 | reload 读回 |
| `tests/test_island_extension_chain.py` | +1 / −0 | reload 读回 |
| `tests/test_settings_and_resources.py` | +3 / −0 | 全局键预置改落盘 |

### 未改动（有意）

- `pet/config.py`：`save()` 空身份剔除每宠键的分支**保留**——它是防污染的
  安全网（原始全局保存不该带每宠键），修复点在路由层而非剔除层。
- `pet/app.py`：D13 各入口（托盘/右键/设置进程 `--instance slot-0`）本就正确，
  不动。运行期 reload 通道（overlay 档案 watcher / legacy 签名盯梢）已覆盖
  slot0（审查核对 `overlay_shell.py:4757`、`app.py:2134-2148`），不动。
- `pet/settings_pet_controls.py` / `pet/settings_reset_defaults.py` 的**行为**：
  曾试过给主身份开特例（不加注记/全局重置），评审后回退——主宠页与子宠页
  完全同构才是消灭分裂人格的正解，v4.3.0 生产（D13 路径）本就如此。

## 三、实现要点

**为什么修在路由层而不是 save() 层**：`Config.save()` 的剔除分支语义是
「全局文件永不带每宠键」，本身正确；缺陷是空身份会话**没有路由可去**。
在 save() 里把剔除改为重定向写档案也能治写入蒸发，但治不好读取侧（重开
仍读 config.json 显示默认）——路由层一处修复，读写两侧同时归位。

**P1（审查返修）**：路由分支直接构造 slot0 档案脸时，若目录还没迁移过
（升级用户只有旧 config.json），表单读到的是出厂默认，保存会让默认档案
抢先落盘、`Config.save()` 的迁移分支被跳过、旧每宠值被当退役键清掉且无
备份。修法 = 路由前先跑幂等 `migrate_slot0_profile`（先备份、后迁移、
可重跑），`not ok` 时退化为不分流（旧行为）并记 warning——不在坏地基上盖楼。

**传入对象处理**：空身份分支不使用传入的 Config 对象（其内存草稿不带入
表单），生产调用方（独立设置进程）传进来的本来就是刚从盘上读的；已在
代码注释写明此口径。

## 四、性能分析

受影响路径只有一条：设置页构造时的 `route_settings_config`（每开一次设置
窗执行一次，无热路径）。实测（本机，Windows，Python 3.13，稳态已迁移目录，
n=200 取均值）：

| 路径 | 耗时 | 说明 |
|---|---|---|
| 空身份新分支（迁移幂等检查 + slot0 脸/主配置构造） | **7.57 ms/次** | 新增 |
| slot-N 既有分支（同样有双 Config 构造，无迁移检查） | 2.47 ms/次 | 对照 |

增量 ≈ 5ms，发生在用户点「桌宠设置」的一次性动作上（设置窗本身构造约
200ms+），可忽略。无新增线程/进程/网络/系统调用；内存无增长（两个 Config
轻对象随对话框回收）。迁移检查是锁内一次文件存在性判断，稳态（档案已存在）
不读全文。

## 五、实机运行记录

环境：本机 Windows 11，部署构建 = 本分支 `build_onedir.ps1 -SkipZip` 产物
（onedir，smoke `--settings started OK`），配置目录 = 日常真实配置
（`%APPDATA%/dsh-pet-standalone-webm-chat`，slot0 档案已迁移、含自定义值）。

操作：主进程运行中，直接拉起**空身份**设置进程（`--settings --settings-page 互动`，
即 issue 报告的入口形态），UIA 驱动真实窗口：

1. **读侧**：互动域各每宠行显示 slot0 档案真值而非出厂默认——气泡方案
   「柔蓝对话 · 正上方（已修改）」、音效音量 50%（默认 70）、天气城市
   「长沙（已修改）」、配图大小 146%、配图概率 30%。窗口级键「鼠标穿透」
   带「（对所有桌宠生效）」注记；侧栏同时给出「恢复默认设置…」与
   「恢复全局默认…」两个动作。
2. **写侧**：配图概率 30 → 7，点「保存并退出」。落盘核验：
   `config-slot-0.json` 的 `self_talk_image_chance` = **7**；
   `config.json` 无该键（全文核验不含任何每宠键）；未编辑的
   `bubble_style=soft_blue_top`、`speak=False` 原样保留（无默认值回灌）。
3. **回显**：重新拉起设置进程，配图概率显示 **7%**（带「已修改」标记）——
   issue 的「重开回默认值」症状消除。
4. **还原**：配图概率设回 30 保存，档案复核 = 30，用户配置恢复原状。
   设置进程正常退出（关窗即进程退出）。

局限如实登记：运行期热生效（保存后运行中主宠行为即时变化）未做真机逐帧
核对，依据是审查对 reload 通道的静态核查（overlay 档案 watcher 签名集合显式
含 0）+ 既有测试 `test_click_sound_immediate_toggle_in_dialog_affects_pet_window`
（生产形态路由视图下即时生效）保持绿色。

## 六、验证

- 契约族 + 受影响 21 文件回归批：**678 passed, 1 skipped**（唯一红
  `test_island_extension_chain.py::test_extension_error_and_recovery_repaint_through_the_real_loop`
  为既有事件循环时序抖动，单跑绿，与本改动无关）。
- 新增/改编测试的 red-green：5 条新契约测试在产品修复前红（路由未生效/
  迁移缺失）、修复后绿。
- `ruff check` 全部改动文件干净。
- 双盲审：ops5.5「可推 PR」（无 blocker/major，minor 注释已清扫）；astra
  「需返修」→ P1（迁移先于路由）已修并补回归测试、P2（本报告）即本节。
- 全量套件结果见 PR 描述（推前闸门）。
