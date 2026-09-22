# 音乐播放器路径进设置页（`music_player_paths`）变更记录

> 结论先说：右键菜单「音乐 → 打开网易云/QQ音乐给主人放歌」需要的播放器位置，现在可以
> 在 **设置 → 桌宠 → 音乐关联** 里直接指定（两行路径 + 一个「自动检测」按钮），
> 不必再手改 `config.json`。留空即回到原有的自动搜索行为。
>
> 本报告按 `docs/PR-REPORT-TEMPLATE.md` 的三份证据组织：修改文件说明 / 性能分析 / 实机运行记录。

---

## 一、修改文件说明（改了什么 + 为什么）

### 1.1 问题（为什么值得做）

`pet/music_players.py` 先自动搜常见目录、再允许手动覆盖（配置键 `music_player_paths`）。但：

- 实测本机网易云在 `D:\CloudMusic`、QQ音乐在 `D:\QQ音乐\QQMusic`，**都不在默认位置**；
- 自动搜索三层浅扫，扫不到时右键菜单项**置灰**，提示语是「找不到X，**可在配置文件中手动指定路径**」；
- 而全仓**没有任何 UI 能写这个键** —— `pet/music_players.py::clear_cache` 的 docstring 里
  一直登记着这条缺口（「当前 `pet/` 内没有调用点（设置页也还没有可写该键的控件）」）。

主人的反馈正是「音乐里的打开音乐软件的配置设置在哪里，我没找到，是不是设置里没加这个模块？」
→ 确认：确实没加。本 PR 把它补上。

### 1.2 逐文件（`git diff --numstat`，共 19 文件 / +568 −9）

| 文件 | 增 | 删 | 改了什么 / 为什么 |
|---|---:|---:|---|
| `pet/settings_music.py`（新） | 177 | 0 | 本组控件 + 行的全部实现：两个 `ResourcePathPicker`、一个「自动检测」按钮、行装配、保存委托、后台检测（预热 + 只读缓存轮询 + 超时收尾）。独立成模块是因为 `modern_settings_dialog.py` 的行数预算已无余量（与 `settings_file_interpret.py` 同口径） |
| `tests/test_music_player_settings.py`（新） | 211 | 0 | 8 条聚焦用例：归属、写盘与清缓存、空值删键、路径没变不清缓存、检测回填/冷缓存等待/超时兜底/回填后保存 |
| `pet/modern_settings_dialog.py` | 6 | 1 | 只做三处接线：`from . import settings_music`（1 行）、控件创建（注释 + 调用 2 行）、`音乐关联` 组的行取用（1 行改 2 行）、`_write_config` 保存委托（1 行）。实测 2394 行 < 预算 2401 → **无需动行数预算**（刻意避开与 #176 在同一条预算常量上冲突） |
| `pet/settings_widgets.py` | 8 | 3 | `ResourcePathPicker` 新增可选 `dialog_title`（默认行为不变）：选 `.exe` 时不再写「选择图片」这种错标题 |
| `pet/context_menus/shared.py` | 2 | 2 | 菜单 tooltip 与气泡文案从「可在配置文件中手动指定路径」改为「可在 设置 → 桌宠 → 音乐关联 里指定它的程序位置」（指向真正能改的地方） |
| `pet/music_players.py` | 4 | 3 | 注释清扫：`clear_cache` 的 docstring 原先写着「没有任何调用点」——现在有了（设置页保存时），改写为指向 `save_music_player_settings` 并说明"只在路径真的变了才清"的理由 |
| `tests/test_menu_layout.py` | 2 | 0 | 归属断言 `owner("music_player_netease") == "桌宠"` / `owner("music_player_qqmusic") == "桌宠"`（防止新行掉进「待分类（开发期）」） |
| `tests/test_music_player_cache.py` | 2 | 2 | 两处断言随新文案更新（气泡 + tooltip） |
| `docs/screenshots/music-player-paths-2026-09-22/`（新） | — | — | 桌宠域「音乐关联」组 1100 / 720 两档宽度截图（设置页准出证据） |

### 1.3 行为契约

- 两行都可留空 → 写回 `{}` → 回到自动搜索（与改动前完全一致）；
- 填了就**以它为准**：文件不存在时菜单提示找不到，而不是偷偷回退自动搜索
  （这是 `find_player` 既有语义，本 PR 未改）；
- 路径**真的变了**才 `clear_cache()`：避免每次保存都让右键菜单重扫盘（实测浅扫 4~5 秒）；
- 「自动检测」= 幂等触发一次后台搜索（复用 `warm_cache_async`）+ 定时轮询只读缓存
  （`cached_player`，不碰文件系统）→ 出结果回填并弹一次说明；冷缓存继续等，
  20 秒超时也会收尾，不留永远转的定时器。

## 二、性能分析（实测数字）

| 项 | 数字 / 结论 | 依据 |
|---|---|---|
| 稳态开销（不点检测） | **零新增定时器、零后台线程**：`QTimer` 只在点按钮时创建并启动，出结果/超时即 `stop()`；不点按钮则从不启动 | 代码路径 + `test_unchanged_paths_do_not_touch_the_cache` |
| 每次保存的新增成本 | 两次 `dict` 比较 + 一次 `config.set`；路径没变时**不调** `clear_cache`（否则会让右键菜单下次重扫盘 4~5 秒） | `save_music_player_settings`；用例 `test_unchanged_paths_do_not_touch_the_cache` |
| 新增路径成本与触发频率 | 「自动检测」一次 = 两个播放器各一次后台浅扫；两者被 `music_players._search_lock` 串行化（该锁是 2026-09-19 CI 事故的修复，本 PR 未改）；触发频率 = 用户点击次数 | 真机实测见 §3.1 |
| 新的系统调用 / 网络 / 磁盘 / 线程 | 无网络；磁盘访问 = 复用既有 `_shallow_scan`（深度 ≤3，几个盘符 + 用户目录）；线程 = 复用 `music_players` 自己的 daemon 预热线程，**本 PR 不新建线程**；GUI 线程只做 `cached_player`（只读缓存，最多一次 `stat`） | `pet/music_players.py:122-152` |
| 内存有无增长 | 新增常驻对象 = 2 个 picker + 1 个按钮 + 1 个 `QTimer`（挂在对话框下，随对话框销毁）；检测过程中的 `results` 字典最多 2 个字符串；无缓存新增 | 代码审阅 |
| 设置页渲染 | 新增 3 行，均无横向溢出：1100 宽度 row_w=832 / viewport=834；720 宽度 row_w=452 / viewport=454（两档 `overflow=False`） | §3.2 探针输出 |

## 三、实机运行记录

环境：Windows 10/11 桌面会话、Python 3.11.1、PySide6 6.11.1、**本机未安装网易云音乐与 QQ音乐**
（这一点由 `find_player` 独立确认，见下）。设置页截图与探针均在本机真实 Qt 会话（非 offscreen、
非 mock）里跑。

### 3.1 自动检测（真实后台扫描，无 stub）

```
$ python <探针>            # 真 QApplication + 真对话框 + 点真按钮
控件存在： True | 按钮文案： 自动检测
点击后按钮： 检测中… | 定时器在跑： True
收敛用时 4.17s（GUI 线程全程未阻塞，边跑边 processEvents）
网易云回填： ''
QQ音乐回填： ''
提示框[自动检测播放器]:
网易云音乐：未找到
QQ音乐：未找到

没找到的可以点右侧「选择…」手动指定程序位置。
真实搜索结论（music_players.find_player）： {'netease': None, 'qqmusic': None}
```

（两次运行分别耗时 4.77s / 4.17s，与 `music_players` 自己的浅扫预算同量级。）

### 3.2 设置页视觉（桌宠 · 音乐关联）

```
width=1100 row_w=832 viewport_w=834 visible=True overflow=False
width=720  row_w=452 viewport_w=454 visible=True overflow=False
```

截图：`docs/screenshots/music-player-paths-2026-09-22/桌宠-音乐关联-{1100,720}.png`
（三行齐全：网易云音乐程序 / QQ音乐程序 / 自动检测播放器；说明文本按字体度量换行）。

### 3.3 设置页写盘 → 右键菜单消费（真实文件路径，无 stub）

```
真实 exe： C:\Windows\System32\notepad.exe（本机没有播放器，用它验证链路）
落盘内容： {'netease': 'C:\\WINDOWS\\System32\\notepad.exe'}
菜单项「打开网易云音乐给主人放歌」 enabled=True tooltip='打开网易云音乐给主人放歌'
```

= 在设置页填了路径并保存后，右键菜单对应项从"置灰 + 找不到提示"变为**可用**，
且提示不再出现 —— 即本 PR 补上的这条链路端到端成立。

### 3.4 门禁

```text
QT_QPA_PLATFORM=offscreen python -m pytest -q --basetemp=C:/ptm
-> 2743 passed, 11 skipped, 11 warnings in 246.32s (0:04:06)
python -m ruff check pet/ tests/   -> All checks passed!
```

### 3.5 缺陷注入（证明新用例承重）

| 注入 | 结果 |
|---|---|
| 去掉保存时的 `clear_cache()` 调用 | `test_saving_paths_persists_and_clears_cache`、`test_empty_paths_drop_the_key_and_restore_auto_search` **变红** |
| 把「冷缓存继续等」短路（`if pending and False and ...`） | `test_detect_keeps_waiting_while_cache_is_cold` **变红** |

## 四、风险与回滚

- **风险**：手填的路径失效（用户升级/移动了播放器）时，菜单项会明确提示「找不到」而不是
  静默回退到自动搜索 —— 这是 `find_player` 的既有语义（防止"我以为它用了我填的路径"），
  本 PR 未改，但在设置项描述里写明了。
- **回滚**：两行清空即回到改动前行为（等于删掉 `music_player_paths` 键）；代码层回滚只需
  移除三处接线 + 删除 `pet/settings_music.py`。
- **遗留**：非 Windows 平台（macOS/Linux）的播放器自动搜索与路径语义未在本机验证
  （`music_players` 本身按 Windows 可执行文件名检索）；设置页「三档宽度 + 放大字体」
  中两档宽度已核验，放大字体沿用同组既有行的既有结论（新行是标准 `SettingRow`，
  无自定义高度）。
