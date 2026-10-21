# 功能模块目录化（10 个模块平铺单文件 → 目录包）

> **基线**：`be0eb46`（`origin/main`，Merge PR #327「Qt 插件路径消毒」）
> **分支**：`feat/module-dirs`　**日期**：`2026-10-21`　**HEAD**：`ae35117`
> **范围**：190 个文件（实现：51 个文件搬迁 + 4 个新包 `__init__.py` + 16 个改 import 的产品文件；
> 测试 113；文档 2；脚本 3；`pyproject.toml` 1）
> **关联**：[`PR-REPORT-FEATURE-MODULES-2026-10-21.md`](PR-REPORT-FEATURE-MODULES-2026-10-21.md)
> （功能模块化激活层——本片动的是**代码布局**，不动激活层语义）；纪律出处
> [`../AGENTS.md`](../AGENTS.md) 的 *Delivery evidence discipline* 与
> [`PR-REPORT-TEMPLATE.md`](PR-REPORT-TEMPLATE.md)。

## 一、核心特性

`pet/` 下 10 个功能模块原来各由一组平铺在 `pet/` 根目录的单文件构成
（`pet/weather.py` + `weather_menu.py` + `weather_service.py` +
`settings_weather.py`），目录里既看不出「哪些文件属于同一个功能」，也没法按功能做
边界检查。本片把这 10 组文件搬进各自的目录包：

`island/`、`music/`、`festival/`、`voice/`、`weather/`、`divination/`、`todo/`、
`proactive/`、`agent_link/`、`file_interpret/`。

**用户可见行为零变化**——本片不新增/删除任何功能、配置键、菜单项、设置行、
后台服务、定时器或线程。改动面只有两类：

| # | 改动 | 说明 |
|---|---|---|
| 1 | 文件落点 | 51 个文件 `git mv` 进 10 个目录包；4 个模块新建 1 行 docstring 的 `__init__.py` |
| 2 | 模块寻址 | 45 个模块的 dotted 名变化（`pet.music_lyric` → `pet.music.lyric`）⇒ 全仓 import 语句、`README`/`AGENTS.md` 指针、测试里按磁盘路径读源码的用例、ruff 的 per-file 忽略段同步改名 |

**红线 / 不变量**（本片逐条钉住）：

1. **不动运行逻辑**：51 个搬迁文件里，除 import 语句与其后的模块名文字/路径深度常量外，
   一行代码都没改（§四有机器化证据）。
2. **不动模块对外 API**：6 个原本有同名单文件的模块（`weather` / `divination` /
   `festival` / `file_interpret` / `proactive` / `agent_link`）把原文件内容整体搬进
   `<pkg>/__init__.py`，因此 `from pet.weather import …`、`import pet.proactive` 这类
   既有导入点写法不变、语义不变。**不做任何转发 shim**（没有 `pet/weather_menu.py`
   保留成 `from .weather.menu import *` 这种残骸）。
3. **不动功能激活层**：`pet/features.py` 的模块清单/硬依赖边、`feature_activation`
   迁移、三层门控一个字节都没改——`pet/features.py` 本身甚至不在本片的 190 个文件里
   （`git diff origin/main...HEAD --name-only | grep -c 'pet/features.py'` = `0`）。
4. **不做兼容垫片**：45 个旧 dotted 名一律不保留别名模块——仓库内所有引用点同批改完，
   由全量套件（8818 passed）与 AST 静态扫描（0 条无法解析的 import）把关。

## 二、修改文件说明

### 2.1 整体规模（`git diff` 的三点口径）

`origin/main` 是 `HEAD` 的祖先（`git merge-base --is-ancestor origin/main HEAD` 为真，
合并基就是 `be0eb46`），所以两点与三点等价；下面统一用三点写法，避免把上游后续提交
卷进统计：

```
$ git diff origin/main...HEAD --numstat | awk '{a+=$1; d+=$2; n++} END {print "files="n, "add="a, "del="d}'
files=190 add=649 del=629

$ git diff origin/main...HEAD --name-status | awk '{print $1}' | sort | uniq -c
      4 A          # 4 个新包的 __init__.py（island / music / todo / voice）
    135 M          # 改 import 路径的文件
      3 R096       # 重命名（相似度 96%）——6 个同名单文件→ __init__.py + 2 个 settings
      2 R097
      1 R098
     31 R099
     14 R100       # 逐字节相同的搬迁
```

**为什么必须开着重命名检测看**：

```
$ git diff origin/main...HEAD --no-renames --numstat | awk '{a+=$1; d+=$2; n++} END {print "files="n, "add="a, "del="d}'
files=241 add=34661 del=34641
```

关掉重命名检测后是 `+34661 / −34641`（241 个文件）——其中 **34 012 行**是 51 个文件
「删掉 + 原样写入新路径」的假改动。真实改动是 `+649 / −629`，净 `+20` 行。

按目录拆分：`pet/` 51 R + 4 A + 16 M；`tests/` 113 M；`docs/` 1 M；`AGENTS.md` 1 M；
`scripts/` 3 M；`pyproject.toml` 1 M。

### 2.2 逐提交说明（13 个提交，按时间正序）

`git log --oneline origin/main..HEAD` 共 13 个提交。前 10 个是**一个模块一个提交**，
每个提交自包含「搬迁 + 该模块的 import 引用点 + 该模块的测试」，可独立回滚。

| # | 提交 | 摘要 | 规模 | 改了什么 / 为什么 |
|---|---|---|---|---|
| 1 | `34380a5` | `refactor(weather)` | 11 files, +13/−13 | `pet/weather.py`→`weather/__init__.py`（R100 逐字节）、`weather_menu.py`→`weather/menu.py`、`weather_service.py`→`weather/service.py`、`settings_weather.py`→`weather/settings.py`；产品侧只改 `app.py`/`context_menus/registry.py`/`modern_settings_dialog.py`/`settings_interaction.py` 的 4 条 import；测试 3 个文件同步 |
| 2 | `242dce6` | `refactor(divination)` | 11 files, +17/−17 | 4 个文件进 `pet/divination/`（`__init__.py` / `quotes.py` / `service.py` / `settings.py`）；改 `app.py`、`modern_settings_dialog.py`、`settings_interaction.py`；测试 4 个 |
| 3 | `9f4b160` | `refactor(todo)` | 6 files, +9/−8 | `todo_panel.py`→`todo/panel.py`、`todo_reminder.py`→`todo/reminder.py`；**新建** `pet/todo/__init__.py`（原无同名单文件，1 行 docstring）；改 `app.py`；测试 2 个 |
| 4 | `9ab74a3` | `refactor(festival)` | 25 files, +55/−55 | 11 个文件进 `pet/festival/`（签库 `quotes_*.py` ×5、`calendar.py`、`data.py`、`service.py`、`settings.py`、`settings_panel.py`、`__init__.py`）；改 `app.py`、`balance.py`（函数内 import + docstring）、`modern_settings_dialog.py`、`settings_standalone.py`；测试 10 个 |
| 5 | `db9858b` | `refactor(voice)` | 22 files, +88/−87 | `voice_chime_service.py`→`voice/chime_service.py`、`voice_chime_settings.py`→`voice/chime_settings.py`、`self_talk_voice.py`→`voice/self_talk.py`；**新建** `pet/voice/__init__.py`；改 `app.py`、`modern_settings_dialog.py`、`settings_standalone.py`；测试 14 个（含 `conftest.py`） |
| 6 | `8fcd725` | `refactor(music)` | 44 files, +148/−140 | 8 个文件进 `pet/music/`（`detect.py` / `foobar.py` / `lyric.py` / `lyric_controller.py` / `now_playing.py` / `players.py` / `settings.py` / `title.py`）；**新建** `pet/music/__init__.py`；改 `app.py`、`config.py`、`context_menus/shared.py`、`modern_settings_dialog.py`、`overlay_shell.py`、`settings_pet_controls.py`、`window_alerts.py`、`window_optional_services.py` + `scripts/bench_music_lyric_align.py`；测试 27 个 |
| 7 | `a2c9eab` | `refactor(file_interpret)` | 11 files, +32/−32 | `file_interpret.py`→`file_interpret/__init__.py`、`file_eater.py`→`file_interpret/eater.py`、`settings_file_interpret.py`→`file_interpret/settings.py`；改 `modern_settings_dialog.py`、`overlay_shell.py`、`sprite_feeding.py`、`window_optional_services.py`；测试 4 个 |
| 8 | `22c3361` | `refactor(proactive)` | 4 files, +15/−15 | `proactive.py`→`proactive/__init__.py`、`proactive_memory.py`→`proactive/memory.py`（三层锁实现原样搬，`pet/chat/memory.py` 复用它的锁表，故 `chat/memory.py` 的 import 在下一个提交里一并改）；测试 2 个 |
| 9 | `e451c6d` | `refactor(agent_link)` | 13 files, +35/−34 | `agent_link.py`→`agent_link/__init__.py`（R099）、`agent_cost.py`→`agent_link/cost.py`、`dsh_state.py`→`agent_link/dsh_state.py`；改 `dynamic_island.py`；测试 8 个 |
| 10 | `785505d` | `refactor(island)` | 81 files, +231/−223 | 11 个文件进 `pet/island/`（`balance.py` / `bridge.py` / `chat.py` / `collision.py` / `dynamic_island.py` / `extension_host.py` / `fps.py` / `fps_monitor.py` / `game_fps.py` / `layout.py` / `segments.py`）+ **新建** `pet/island/__init__.py`；改 `app.py`、`config.py`、`gpu_monitor.py`、`overlay_shell.py`、`settings_network.py`、`sys_monitor.py`、`agent_link/__init__.py`、`festival/quotes_west.py`、`file_interpret/__init__.py`、`weather/menu.py`；`docs/ISLAND-SEGMENTS-EXTENSION.md`（对外契约文的路径同步）；`pyproject.toml`（ruff per-file 忽略段 `"pet/balance.py"`→`"pet/island/balance.py"`）；`scripts/verify_presentmon.py`；测试 69 个 |
| 11 | `16dedb3` | `docs: AGENTS.md` | 1 file, +4/−4 | `AGENTS.md` 的 Context pointers 里 4 条模块路径指针（`island/bridge.py`、`agent_link/dsh_state.py`、`island/game_fps.py`+`fps_monitor.py`+`fps.py`、`music/lyric.py`）随目录化同步——不同步就会让下一个人按旧路径找文件 |
| 12 | `f05a745` | `test: 按磁盘路径读 agent_link.py 的用例` | 2 files, +5/−4 | 两个用例按磁盘路径读源码做 AST 断言（`tests/test_desktop_pet_features.py`、`tests/test_persona_template.py`）——`ast.parse(open("pet/agent_link.py"))` 这种写法不经过 import，改 import 路径改不到它，必须单独改到 `pet/agent_link/__init__.py` |
| 13 | `ae35117` | `test(imports): rebase 后 #325/#327 残留` | 2 files, +2/−2 | rebase 到 `#325`/`#327` 之后，上游新加的两处引用仍是旧平铺名：`pet/chat/memory.py` 的 `from ..proactive_memory import …`、`scripts/build_onedir.ps1` 的 `from pet import game_fps`。这两处在 `#325`/`#327` 落地前不存在，所以前 10 个提交覆盖不到；rebase 后由全量门禁与 AST 扫描抓出并收口 |

### 2.3 每个模块包的迁移方式

| 情形 | 模块 | 做法 |
|---|---|---|
| **原本有同名单文件**（6 个） | `weather`、`divination`、`festival`、`file_interpret`、`proactive`、`agent_link` | 该单文件**整体**变成 `<pkg>/__init__.py`（R096–R100）。因此 `import pet.proactive` / `from pet.weather import …` 这类既有导入点**写法与语义都不变** |
| **原本没有同名单文件**（4 个） | `island`、`music`、`todo`、`voice` | 新建 `<pkg>/__init__.py`，内容**只有一行模块 docstring**（60–97 字节，无 import、无代码）。这 4 个是唯一的新增文件 |
| 其余同组文件 | 全部 10 个模块 | `<旧名>.py` → `<pkg>/<短名>.py`（如 `pet.music_lyric_controller` → `pet.music.lyric_controller`），保留短名可读、去掉冗余前缀 |

**零 shim**：没有留任何「旧路径转发文件」。旧 dotted 名（45 个）在仓库内不存在别名，
所有引用点同批改完。代价是任何**仓库外**引用旧名会 `ModuleNotFoundError`——这是有意的
取舍（本项目没有对外承诺的 `pet.*` 公开 API，`docs/ISLAND-SEGMENTS-EXTENSION.md` 的
第三方契约是 JSON 清单 + provider 协议，不是 Python 模块路径）。

### 2.4 51 个旧→新路径映射的获取方式

映射来自 git 自己的重命名记录，不是手工维护的表：

```
$ git diff origin/main...HEAD -M --name-status --diff-filter=R
R096  pet/festival.py                 pet/festival/__init__.py
R099  pet/dynamic_island.py           pet/island/dynamic_island.py
R100  pet/weather.py                  pet/weather/__init__.py
...（共 51 行）
```

`-M` 打开重命名检测，`R<score>` 是 git 给出的相似度（100 = 逐字节相同）。
**51 = 搬迁文件总数**，其中 `R100` 14 个（完全没动）、`R099` 31 个、
`R098` 1 个（`self_talk_voice.py`）、`R097` 2 个、`R096` 3 个
（`festival.py`、`file_interpret.py`、`settings_file_interpret.py`；相似度最低是因为
这两个 `__init__.py` 的 import 块最大）。相似度低于 100 的原因全部是 import 路径变长/
模块名变化带来的行替换，§四给出口径与实测。

按目标模块统计：`island` 11、`festival` 11、`music` 8、`weather` 4、`divination` 4、
`voice` 3、`file_interpret` 3、`agent_link` 3、`todo` 2、`proactive` 2（合计 51）。

<details>
<summary>51 条映射全表（展开）</summary>

| 相似度 | 旧路径 | 新路径 |
|---|---|---|
| R100 | `pet/agent_cost.py` | `pet/agent_link/cost.py` |
| R099 | `pet/agent_link.py` | `pet/agent_link/__init__.py` |
| R100 | `pet/dsh_state.py` | `pet/agent_link/dsh_state.py` |
| R099 | `pet/divination.py` | `pet/divination/__init__.py` |
| R100 | `pet/divination_quotes.py` | `pet/divination/quotes.py` |
| R099 | `pet/divination_service.py` | `pet/divination/service.py` |
| R097 | `pet/settings_divination.py` | `pet/divination/settings.py` |
| R096 | `pet/festival.py` | `pet/festival/__init__.py` |
| R100 | `pet/festival_calendar.py` | `pet/festival/calendar.py` |
| R099 | `pet/festival_data.py` | `pet/festival/data.py` |
| R100 | `pet/festival_quotes_cn.py` | `pet/festival/quotes_cn.py` |
| R099 | `pet/festival_quotes_west.py` | `pet/festival/quotes_west.py` |
| R100 | `pet/festival_quotes_west_game.py` | `pet/festival/quotes_west_game.py` |
| R100 | `pet/festival_quotes_west_movie.py` | `pet/festival/quotes_west_movie.py` |
| R100 | `pet/festival_quotes_west_song.py` | `pet/festival/quotes_west_song.py` |
| R099 | `pet/festival_service.py` | `pet/festival/service.py` |
| R099 | `pet/festival_settings.py` | `pet/festival/settings.py` |
| R099 | `pet/festival_settings_panel.py` | `pet/festival/settings_panel.py` |
| R100 | `pet/file_eater.py` | `pet/file_interpret/eater.py` |
| R096 | `pet/file_interpret.py` | `pet/file_interpret/__init__.py` |
| R096 | `pet/settings_file_interpret.py` | `pet/file_interpret/settings.py` |
| R097 | `pet/balance.py` | `pet/island/balance.py` |（k3 复审校正：直方图 R097 正确，详表笔误）
| R099 | `pet/dynamic_island.py` | `pet/island/dynamic_island.py` |
| R099 | `pet/fps_monitor.py` | `pet/island/fps_monitor.py` |
| R099 | `pet/game_fps.py` | `pet/island/game_fps.py` |
| R099 | `pet/island_bridge.py` | `pet/island/bridge.py` |
| R099 | `pet/island_chat.py` | `pet/island/chat.py` |
| R099 | `pet/island_collision.py` | `pet/island/collision.py` |
| R099 | `pet/island_extension_host.py` | `pet/island/extension_host.py` |
| R099 | `pet/island_fps.py` | `pet/island/fps.py` |
| R099 | `pet/island_layout.py` | `pet/island/layout.py` |
| R100 | `pet/island_segments.py` | `pet/island/segments.py` |
| R099 | `pet/foobar_playing.py` | `pet/music/foobar.py` |
| R100 | `pet/music_detect.py` | `pet/music/detect.py` |
| R099 | `pet/music_lyric.py` | `pet/music/lyric.py` |
| R099 | `pet/music_lyric_controller.py` | `pet/music/lyric_controller.py` |
| R100 | `pet/music_players.py` | `pet/music/players.py` |
| R100 | `pet/music_title.py` | `pet/music/title.py` |
| R099 | `pet/now_playing.py` | `pet/music/now_playing.py` |
| R099 | `pet/settings_music.py` | `pet/music/settings.py` |
| R099 | `pet/proactive.py` | `pet/proactive/__init__.py` |
| R099 | `pet/proactive_memory.py` | `pet/proactive/memory.py` |
| R099 | `pet/todo_panel.py` | `pet/todo/panel.py` |
| R099 | `pet/todo_reminder.py` | `pet/todo/reminder.py` |
| R098 | `pet/self_talk_voice.py` | `pet/voice/self_talk.py` |
| R099 | `pet/voice_chime_service.py` | `pet/voice/chime_service.py` |
| R099 | `pet/voice_chime_settings.py` | `pet/voice/chime_settings.py` |
| R099 | `pet/settings_weather.py` | `pet/weather/settings.py` |
| R100 | `pet/weather.py` | `pet/weather/__init__.py` |
| R099 | `pet/weather_menu.py` | `pet/weather/menu.py` |
| R099 | `pet/weather_service.py` | `pet/weather/service.py` |

</details>

### 2.5 测试侧改动（113 个文件，`+878/−878` 行，其中非 import 行 103）

测试改动**全部是引用点改名**，没有放宽任何断言。三类形态：

| 形态 | 例子 | 数量 |
|---|---|---|
| import 语句 | `from pet.music_lyric_controller import …` → `from pet.music.lyric_controller import …` | 大多数 |
| 按磁盘路径读源码 | `(REPO_ROOT / "pet" / "dsh_state.py").read_text()` → `(REPO_ROOT / "pet" / "agent_link" / "dsh_state.py").read_text()` | 20 余处 |
| logger 名 / 模块名字符串 | `caplog.at_level(logger="pet.island_bridge")` → `"pet.island.bridge"` | 10 余处 |

有两处**检查形态**变了（值得评审看一眼，结论是**更严或等价**）：

1. `tests/test_desktop_pet_features.py`：原来用 `path.name in {"agent_link.py", "test_agent_link.py"}`
   挑文件；搬迁后 `path.name` 变成 `__init__.py`（会和其它包的 `__init__.py` 撞），
   于是改为 `path.name in {"test_agent_link.py"} or path.as_posix().endswith("pet/agent_link/__init__.py")`
   ——等价映射，且不会误伤其它包。
2. `tests/test_settings_process_isolation.py:708`：`assert "voice_chime_service" not in stripped`
   → `assert "chime_service" not in stripped`。子串变短 ⇒ **判定变严**（多抓一类回潮），
   不是放宽。

**未改动**：`pet/features.py`（模块清单与 `entry_names`，**整文件不在本片 diff 里**）、
`pet/feature_activation.py`、`pet/context_menus/menu_templates/*.json`、
`pet/config.py` 的键集一律没动（`pet/config.py` 只改了 4 条 import）。
`requirements.txt`、`THIRD_PARTY_NOTICES.md` 未动（无依赖变化）。

## 三、实现要点

1. **搬迁用 `git mv`，逐目录一个提交**：每个提交自包含「文件搬迁 + 该模块的引用点 +
   该模块的测试」，任一提交可单独 `git revert` 而不牵连其它模块。10 个模块提交之后再叠
   3 个收口提交（`AGENTS.md` 指针、按磁盘路径读源码的用例、rebase 残留），每个都能独立
   说明「为什么它是第 11/12/13 个而不是前 10 个之内」。
2. **允许 `parents[N]` 深度 +1 的四处特例**：4 个文件里有
   `Path(__file__).resolve().parent.parent / "assets"` 这类**基于 `__file__` 的资源路径**。
   文件多了一层目录，所以常量必须 +1（`parent.parent.parent` / `parents[2]`）。
   这不是行为改动而是**等价改写**：§四给出实算（新旧两种写法解析到同一个绝对路径）。
   四处分别在 `agent_link/__init__.py`（`integrations/dsh-pet-bridge` 候选目录）、
   `file_interpret/__init__.py`（`assets/characters`）、`island/extension_host.py`
   （`_PROJECT_ROOT`）、`island/game_fps.py`（`bundled_backend_path` 的根）。
   **没有**把这四处改成 `importlib.resources` 之类的新机制——那会把「搬文件」变成
   「改资源定位语义」，超出本片范围。
3. **`config.py` 的 `from . import X` 拆成 `from .island import segments as X`**：
   45 个模块里有一批是被 `from . import <旧名>` 形式引用的，改成子模块后必须写成
   `from .<pkg> import <短名> as <旧别名>`，并**保留原变量名**（例如
   `from .island import segments as island_segments`）——这样同文件里下游几百处
   `island_segments.xxx` 一个都不用动，diff 才能保持在「只改 import」的量级。
   这是本片让 190 个文件的改动仍然可逐行审的关键手法。
4. **按磁盘路径读源码的用例单独处理**：`ast.parse(open(...))`、`read_text()` 这类写法
   不经过 import 系统，改 import 改不到它们。先把它们全找出来（`grep -rn 'read_text\|ast.parse'
   tests/`），再逐个改路径。`f05a745` 就是这个补丁——它证明「改 import」不等于「改完了」。
5. **不做兼容 shim 的取舍**：留 `pet/music_lyric.py` 做转发会让「目录化」变成「两套路径
   并存」，下一个人继续按旧路径写新代码，目录化就没意义了；且转发 shim 会让
   `import pet.music_lyric` 与 `import pet.music.lyric` 产生**两个不同模块对象**，
   `isinstance`/monkeypatch 会分叉（本仓历史上踩过同类坑）。因此选择一次性全改，
   用门禁 + AST 扫描兜底。

## 四、性能分析

**结论先行**：本片**零运行时行为变化**，唯一可测量的成本是进程启动时多创建 **4 个
只含一行 docstring 的包模块对象**（合计约 2.5 ms，一次性，占 `import pet.app` 的 0.34%）。
稳态零开销、无新增系统调用/网络/磁盘/线程、内存增量 < 4 KB。

**方法（可复现）**：Python 3.13.7 / Windows 11（本机）/ `PySide6 6.11.2`；基线对照组是
`git worktree add --detach /d/dsh-pet-mod-dirs-baseline be0eb46`（同一台机器、同一个解释器、
同一块盘）。

### 4.1 口径一：搬迁文件的内容等价性（机器化）

任务里建议的「非 import 行改动为零」这个口径**不完全成立**，如实记录实际做了什么、
输出是什么：

```
$ git diff origin/main...HEAD -M -- pet/ | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)' | wc -l
360                                    # pet/ 下全部增删行（含搬迁文件内的 import 改写）

$ ... 同上，再 | grep -vE '^[+-][[:space:]]*(from[[:space:]]|import[[:space:]]|#|$)' | wc -l
28                                     # 其中不是 import / 注释 / 空行的
```

28 行逐条核实后的归属（**没有一行是运行逻辑**）：

| 行数 | 是什么 | 在哪 |
|---|---|---|
| 8（4 增 4 删） | `Path(__file__)…parents[N]` 深度 +1，资源根不变 | `agent_link/__init__.py`、`file_interpret/__init__.py`、`island/extension_host.py`、`island/game_fps.py` |
| 6 | 多行 import 语句的续行（别名行 + `)` 行）——import 改写的排版副产物 | `island/dynamic_island.py` |
| 6（3 增 3 删） | docstring 里的模块路径文字：`pet.balance`→`pet.island.balance`、`pet.festival_service`→`pet.festival.service` | `pet/app.py` |
| 4 | 4 个新包 `__init__.py` 的单行 docstring（纯新增） | `island`/`music`/`todo`/`voice` |
| 4（2 增 2 删） | docstring 里的模块路径文字 | `festival/quotes_west.py`、`island/balance.py` |

**更强的证据：AST 等价性检查**（逐对比较旧 blob 与新 blob，删掉 `Import`/`ImportFrom`
节点后比 `ast.dump`）：

```
$ git diff origin/main...HEAD -M --name-status --diff-filter=R | python astcheck.py
pairs=51 ast_identical_after_import_strip=45 differ=6
```

再叠加「把 docstring 常量置空」这一步：

```
pairs=51 identical_after_strip_imports_and_docstrings=47 differ=4
```

剩下 4 个 = 上面那 4 处 `parents[N]` 深度。对它们做**实算等价性**（新旧两种写法解析到
同一个绝对路径）：

```
pet/agent_link.py        parents[2] -> D:\dsh-pet-mod-dirs        |  pet/agent_link/__init__.py        parents[3] -> D:\dsh-pet-mod-dirs        equal=True
pet/file_interpret.py    parents[2] -> D:\dsh-pet-mod-dirs        |  pet/file_interpret/__init__.py    parents[3] -> D:\dsh-pet-mod-dirs        equal=True
pet/island_extension_host.py parents[1] -> D:\dsh-pet-mod-dirs\pet |  pet/island/extension_host.py      parents[2] -> D:\dsh-pet-mod-dirs\pet   equal=True
pet/game_fps.py          parents[1] -> D:\dsh-pet-mod-dirs\pet    |  pet/island/game_fps.py            parents[2] -> D:\dsh-pet-mod-dirs\pet   equal=True
```

**文件字节数**：51 个搬迁文件里 **16 个字节数不变**——其中 14 个是 `R100`（逐字节相同），
另 2 个（`pet/festival/quotes_west.py`、`pet/island/game_fps.py`）字节数相同但内容有差，
差的都是**等长替换**（`festival_data`→`festival.data`、`island_segments`→`island.segments`）。
其余 35 个的字节差全部来自 import 行长度变化，最大是 `island/dynamic_island.py`
**+77 B**（256 260 → 256 337）。没有任何文件的字节数减少超过 80 B。

### 4.2 口径二：启动期导入的东西是不是同一批

计时类对比在跨工作树时**不可用**（见 4.3 的负对照），所以用**集合型**证据——它不受
机器负载、文件系统缓存、杀软影响：

```
$ python -c "import pet.app; 打印 len(sys.modules) 与全部 pet.* 模块名"
基线 be0eb46：398 个模块，pet.* = 79
分支 ae35117：402 个模块，pet.* = 83
```

逐个对账：

| 项目 | 结果 |
|---|---|
| 名字没变的模块 | 70 个，**全部**仍出现在分支的启动集合里（集合包含关系为真） |
| 被搬迁且启动时真的会加载的模块 | 9 个，按迁移映射一一对应、9/9 全部命中：`island_segments`→`island.segments`、`music_players`→`music.players`、`proactive`→`proactive`（同名包化）、`proactive_memory`→`proactive.memory`、`self_talk_voice`→`voice.self_talk`、`todo_reminder`→`todo.reminder`、`voice_chime_service`→`voice.chime_service`、`weather`→`weather`（同名）、`weather_menu`→`weather.menu` |
| 分支独有 | 恰好 4 个新包：`pet.island`、`pet.music`、`pet.todo`、`pet.voice` |
| 对账 | 70 + 9 + 4 = 83 = 分支实际值 ✔ |

即：**应用启动时执行的代码模块集合与包化前完全一致，唯一增量是 4 个空包对象**。

### 4.3 新增路径的绝对成本与触发频率

```
$ python  # 在分支上，先 import pet，再对每个新包做 200 次「弹出 sys.modules + 重新 import」
pet.island   冷导入 中位=622.5us p95=801.4us  文件字节=97B
pet.music    冷导入 中位=618.1us p95=843.8us  文件字节=90B
pet.todo     冷导入 中位=634.8us p95=803.5us  文件字节=60B
pet.voice    冷导入 中位=669.4us p95=896.2us  文件字节=76B
```

4 × ≈ 0.63 ms ≈ **2.5 ms**，触发频率 = **每进程一次**（启动期）。参照量：同一方法下
分支上 `pet/music/title.py`（一个真实业务模块）冷导入中位 **686.7 µs**——说明这 0.6 ms
是解释器 `find_spec` + 模块对象创建的**固定成本**，与文件大小无关，不是「多读了代码」。

计时类**跨树对比在此机器上不可用**，这里如实给出排除证据（否则会得出「目录化快了 16%」
这种假结论）：

```
隔轮交替 A/B，5 轮 × 每轮进程内 25 次取中位（基线工作树 vs 分支）
[改动涉及] pet.island_segments  基线=3518.3us 分支=2527.0us  差中位=-1128.5us
[改动涉及] pet.balance          基线=1357.1us 分支= 630.2us  差中位= -682.1us
[改动涉及] pet.festival_data    基线=2661.5us 分支=1659.8us  差中位=-1027.6us
[改动涉及] pet.music_title      基线=1514.0us 分支= 686.7us  差中位= -763.4us
[改动涉及] pet.weather          基线=3022.7us 分支=2306.5us  差中位= -712.8us   ← 该文件逐字节相同(R100)
[负对照] pet.catalog            基线=1621.7us 分支= 876.8us  差中位= -868.4us   ← 本片完全没碰
[负对照] pet.sprite_collision   基线=2739.4us 分支=1787.7us  差中位= -993.5us   ← 本片完全没碰
```

两个**本片完全没碰**的模块也测出 −0.87 ms / −0.99 ms 的系统性偏移，且
`pet/weather.py`（R100，逐字节相同）同样测出 −0.71 ms ⇒ 该偏移来自「基线工作树是本次
全新签出」，不是目录化。用负对照把偏移量掉之后，改动本身的可归因成本落在
**−0.2 ms ~ +0.25 ms 之间且正负号随机**，即**低于本机测量分辨率，测不出**。
这一条如实登记，不拿它当性能收益。

### 4.4 逐条回答

| 问题 | 结论 |
|---|---|
| ① 稳态开销有没有变化 | **没有**。搬迁文件的 AST 在去掉 import 与 docstring 后 47/51 完全相同，剩 4 处只改了 `parents[N]` 常量（实算解析到同一目录）；热路径代码一个字节没动 |
| ② 新增路径的绝对成本与触发频率 | 4 个新包 `__init__` 各 ≈0.62–0.67 ms（中位，n=200），合计 ≈2.5 ms，**每进程一次**，占实测 `import pet.app` 728.7 ms 的 **0.34%**；用户可感知不到（启动期本来就在等 Qt） |
| ③ 有无新增系统调用 / 网络 / 磁盘 / 线程 | **零**。AST 等价性是最强的机器化证据——代码里没有任何新增的调用节点，`open`/`socket`/`threading` 的调用点数量不变；实测每个被测模块 import 前后 `threading.active_count()` 恒为 `1 -> 1` |
| ④ 内存与缓存有无增长 | +4 个模块对象（每个 = 一个含一行 docstring 的包，源文件 60–97 B），`sys.modules` 398 → 402。模块对象本体在 CPython 里百字节级，合计 < 4 KB。无新缓存、无新落盘文件 |

**基准脚本**：无专门基准脚本（本片不该有）；本文的测量脚本是临时脚本，命令已逐条写在
上面，用同一条命令可复测。若后来者要重跑 A/B，只需要
`git worktree add --detach <dir> be0eb46` 后按 4.3 的命令跑两棵树。

## 五、实机运行记录

**本机真实环境**：Windows 11 / Python 3.13.7 / PySide6 6.11.2 / `QT_QPA_PLATFORM=offscreen`，
不是 CI、不是 mock。工作树 `/d/dsh-pet-mod-dirs`（`feat/module-dirs` @ `ae35117`）。

### 5.1 全量门禁（分支任务日志，本轮未重跑）

| 门 | 命令 | 结果 |
|---|---|---|
| 全量套件 | `QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q` | **8818 passed / 0 failed**，耗时 **1790 s**（≈30 分钟） |
| 静态检查 | `python -m ruff check pet tests scripts` | **All checks passed!** |
| 收集 | `python -m pytest tests/ --collect-only -q` | **8833 collected** |
| import 解析 | AST 静态扫描全仓 import | **0 条无法解析**（旧平铺名已无残留引用） |

**算术自洽性核对**：8833 collected = 8818 passed + 15 skipped，与历史上本仓的
「15 skipped」口径一致（`skipped` 是平台/设备相关用例，不是失败）。这一条是本次核对
加上去的交叉检查，不是日志里的原话。

### 5.2 当班复跑的证据（本次会话现场跑出）

| 门 | 命令 | 真实输出 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests scripts` | `All checks passed!`（rc=0） |
| 收集 | `QT_QPA_PLATFORM=offscreen python -m pytest tests/ --collect-only -q` | `8833 tests collected in 5.68s` |
| 聚焦（交付纪律自身） | `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_pr_report_discipline.py -q` | `217 passed in 4.03s` |
| 聚焦（功能门控） | `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_feature_modules_gating.py -q` | `89 passed in 37.66s` |
| 启动链路 | `python -c "import pet.app"` | `import pet.app ok 788.2 ms 402 modules`（单次冷启动，含解释器启动后的模块导入） |
| 搬迁等价性 | AST/字节/AST 集合三项检查 | 见 §四（45/51 → 47/51 → 4 处实算等价） |
| 包化新增成本 | 进程内 200 次重复 import | 见 §4.3 |

### 5.3 用户可见行为的确认

本片**不改变任何用户可见行为**，因此不存在「新增的可见行为」需要确认；取而代之的
确认口径是「同一批用户可见行为的回归由既有套件钉住」：

- 全量 **8818 passed / 0 failed**（含菜单布局、设置页装配、灵动岛、音乐/歌词/语音、
  节日/待办/求签、文件解读、Agent 联动、主动识屏等全部受影响族的用例）；
- `tests/test_feature_modules_gating.py` **89 passed**（模块激活三层门控：菜单投影 /
  设置层三形态 / 执行层构造前挡）——本片没有动激活层，这条是「没动」的证明；
- `tests/test_menu_layout.py`、`tests/test_sprite_menu_parity.py`、
  `tests/test_settings_page_guide.py` 等菜单/设置契约族均在改后通过（含在本片被改
  import 路径的 12 个用例：`test_menu_layout.py` +12/−12、`test_sprite_menu_parity.py`
  +3/−3、`test_settings_page_guide.py` +2/−1）。

### 5.4 边界与失败路径

| 场景 | 观察 |
|---|---|
| 旧 dotted 名已不存在 | `python -c "import pet.balance"` → `ModuleNotFoundError: No module named 'pet.balance'`（本片有意不保留别名；仓库内无残留引用由 AST 扫描 0 条保证） |
| 同名单文件的模块仍可原样导入 | `pet.weather.py`→`pet/weather/__init__.py` 是 R100 逐字节搬迁，`import pet.weather` 的写法与导出符号完全不变 |
| 按磁盘路径读源码的用例 | 若只改 import 不改磁盘路径，`tests/test_desktop_pet_features.py` 会 `FileNotFoundError`——该提交（`f05a745`）正是被这类用例逼出来的 |
| rebase 后新引入的旧名引用 | `#325`/`#327` 落地后新加的 2 处旧名被 AST 扫描 + 全量门禁抓出（`ae35117`） |

### 5.5 无法自动验证的能力（如实登记）

- **真机 GUI 视觉**：本片不涉及视觉，未截图、未开真窗核对（无必要，也无新视觉）。
- **打包产物**：未重新构建三端产物。`scripts/build_onedir.ps1` 有一行被改
  （`from pet.island import game_fps`），该命令只在构建期执行；本轮**未跑构建**，
  也没有跑 `tests/test_presentmon_packaging.py` 之外的冻结布局验证。理由：本片零行为
  变化，且该 ps1 行与 `scripts/verify_presentmon.py` 的同类行有测试覆盖（
  改后通过）。**这一项是本片唯一没有实机旁证的改动**，如果评审要求，需要一次完整
  onedir 构建。
- **全量套件本轮未重跑**：1790 s 的成本按 CI 成本纪律不该在同一份改动上重复付两遍；
  本轮只做了 5.2 的快检 + 4.1/4.2 的静态等价性复核。全量绿的说法**溯源自分支任务
  日志**，本文没有自己复现它——这一点不隐瞒。

## 六、测试与验证（本轮当班实测）

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests scripts` | `All checks passed!` |
| 收集 | `QT_QPA_PLATFORM=offscreen python -m pytest tests/ --collect-only -q` | `8833 tests collected in 5.68s` |
| 交付纪律 | `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_pr_report_discipline.py -q` | `217 passed in 4.03s`（按生效日之后的每份报告参数化；本文缺失章节或未登记 INDEX 会在此变红） |
| 功能门控族 | `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_feature_modules_gating.py -q` | `89 passed in 37.66s` |
| 全量（分支任务日志） | `QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q` | `8818 passed / 0 failed`，1790 s |
| 搬迁等价性 | AST 去 import（45/51 相同）+ 去 docstring（47/51 相同）+ 4 处路径深度实算等价 | 全部通过，见 §四 |
| 启动模块集合 | 基线 79 vs 分支 83 个 `pet.*`，70 同名全在 + 9 映射全中 + 4 新包 | 对账成立 |
| 架构红线 | 由全量套件内的 `tests/test_architecture.py`（+8/−8，仅改 import 路径）覆盖 | 通过（全量的一部分） |

## 七、已知限制与后续

1. **70 处注释/文档字符串仍写旧文件路径**（`pet/` `tests/` `scripts/` `tools/` `AGENTS.md`，
   按「dotted 名真正改变的 45 个搬迁」口径统计；不含 6 个同名包化）。逐条分类：
   **代码位置的命中 0 处**，全部落在文本里——字符串/文档字符串 52 行、注释 17 行、
   `scripts/build_onedir.ps1` 的 `throw` 消息 1 行。它们不影响运行，但会误导后来者按
   旧路径找文件。**本片不修**（会把一个可逐行审的机械改动变成 46 个文件的大 diff），
   建议作为独立 docs 提交处理。
2. **`tools/import_cost.py` 是一个真被打断的开发者工具**（属上面 70 行的 3 行，但性质
   不同：它的默认目标列表是**活数据**，不是注释）。实测：

   ```
   $ python tools/import_cost.py pet.balance pet.festival_service pet.dynamic_island
         MB   new pet modules  module
        ERR                    pet.balance: ModuleNotFoundError: No module named 'pet.balance'
        ERR                    pet.festival_service: ModuleNotFoundError: No module named 'pet.festival_service'
        ERR                    pet.dynamic_island: ModuleNotFoundError: No module named 'pet.dynamic_island'
   ```

   默认列表 10 个目标里 4 个已死（`pet.balance`、`pet.festival_service`、
   `pet.voice_chime_service`、`pet.dynamic_island`），docstring 第 4 行的示例命令
   （`tools/import_cost.py pet.window pet.balance`）同样过时。**不是产品路径**，无测试
   覆盖，建议与第 1 条一并修。
3. **三处「代码行上的旧路径」逐个核过**（这三是上面 70 行里唯一落在**非纯注释行**上的，
   因此会被「注释行扫描」漏掉）：

   | 位置 | 现在的内容 | 性质 | 本片是否改动 |
   |---|---|---|---|
   | `tests/test_runtime_dependencies.py:39` | `"comtypes": "pycaw 的依赖（pet/music_detect.py 走 pycaw 时用到）"` | dict 值 = 未声明依赖测试的**失败消息**文本，真路径已是 `pet/music/detect.py` | **未改**（该行不在这 113 个测试文件的 diff 里） |
   | `tests/test_runtime_dependencies.py:132` | `"psutil 必须留在 requirements.txt：pet/now_playing.py 用它判断播放器进程在不在，"` | `assert` 的**失败消息**文本，真路径已是 `pet/music/now_playing.py`（同一函数的下一行 `from pet.music import now_playing` **已改对**） | **未改** |
   | `pet/features.py:309` | `needs_network=True,  # 取词三源并发（pet/music_lyric.py:772-775）` | 代码行尾随注释，真路径已是 `pet/music/lyric.py`；`pet/features.py` **整文件不在本片 diff 里** | **未改** |

   结论：三处**至今仍是旧路径**，且都不是本片引入的——它们是搬迁前就存在的文本，
   搬迁没扫到（前两处是字符串字面量，第三处在未改动的文件里）。影响面限于
   「测试失败时的提示文字指向一个已不存在的文件」与「注释误导」，**不影响任何断言
   逻辑或运行行为**；`tests/test_runtime_dependencies.py` 改后仍是绿（全量 8818 passed 的一部分）。
4. **未构建三端产物**，见 §5.5。
5. **没有做「按模块目录做边界检查」的架构红线**（例如「`pet/music/**` 不许 import
   `pet/chat/**`」）。目录化让这类检查**变得可能**，但本片不新增它——新增红线会立刻红
   一堆既存越界，属后续独立议题。

## 八、风险与回滚

**影响面**：仅 Python 模块寻址。不涉及配置键（`feature_activation` 等键集一字未动）、
不涉及落盘数据、不涉及协议/端口、不涉及依赖版本。

**风险点与现状**：

| 风险 | 现状 |
|---|---|
| 仓库外代码 import 旧 `pet.*` 名 | 会 `ModuleNotFoundError`。本项目无对外承诺的 Python API（第三方契约是 JSON 清单 + provider 协议，见 `docs/ISLAND-SEGMENTS-EXTENSION.md`），可接受 |
| 某处引用点被漏改 | 由 AST 静态扫描（0 条无法解析）+ 全量 8818 passed 兜底；`ae35117` 证明这条兜底真的抓到过 rebase 后的 2 处漏网 |
| `__file__` 相对路径算错 | 4 处已实算同路径；对应测试（`test_presentmon_packaging.py`、`tests/test_application_icon.py`、`test_file_interpret.py`）在改后通过 |
| 循环 import | 包化会让「子模块 → 包 `__init__`」多一条隐式边。6 个同名包化的 `__init__` 就是原单文件内容（原来也在这条边上），4 个新包只含 docstring，不可能引入新环。全量绿已验证 |

**回滚**：`git revert` 13 个提交中的任意子集即可；每个模块一个提交，互不依赖（唯一
顺序依赖是 `8fcd725`(music) 之后 `785505d`(island) 里改了 `weather/menu.py`，以及
`e451c6d`(agent_link) 与 `785505d`(island) 之间互改了对方的 import——所以**要么全回滚、
要么按 1→13 的逆序回滚**）。无落盘残留、无配置文件迁移、无缓存需要清理。
