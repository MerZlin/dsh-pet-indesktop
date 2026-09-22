# 本地 WIP 批次：交付证据纪律 + 构建 Qt 排他 + 产物 TTS 自检 + 角色头部框（2026-09-22）

一句话：把本机工作树里攒下、且不适合塞进 PR #175（歌词对齐）的一批改动，作为独立
PR 落地——① 交付证据纪律（三份证据 + 机器化校验）② `build_onedir.ps1` 的 Qt 绑定
排他（不修则构建被 PyInstaller 直接中止）③ 产物 TTS 自检脚本（对着真产物曾判假红，
本轮修掉）④ `character_head_box()` 取值函数与 shenshen 的头部框数据。

红线/不变量：

1. **不改变产品运行时行为**：`pet/` 侧只新增一个当前**零调用方**的取值函数，没有任何
   既有路径改用它（`git diff` 里 `pet/` 只有 `catalog.py` 一个文件、纯新增）；
2. 构建脚本只**新增** `--exclude-module`，原有排除清单一条不减；
3. 自检脚本「缺 TTS 就非 0 退出」的判定强度**不许降低**——有回归用例钉住
   （真包仍被判为「需要」+ 负例产物仍报 25 个缺失）。

## 修改文件说明

`git diff --numstat origin/main HEAD`（7 个提交：4 个本地 WIP + 1 个自检修复 + 2 个
本报告与索引登记不计入下表）：

| 文件 | 增/删 | 改了什么 + 为什么 |
|---|---|---|
| `AGENTS.md` | +20 / −0 | 新增 "Delivery evidence discipline" 小节：每个 PR 必须交付修改文件说明 / 性能分析 / 实机运行记录，写明判定标准（形容词不算分析、不许用沉默代替结论）与纯文档豁免口径。 |
| `docs/DEV-HANDOVER.md` | +25 / −6 | §8.2 把三份证据列进 PR 描述必备项；§8.3 补逐条判定标准表、模板指针、机器化校验说明；§8.4 新增「交付证据门」。 |
| `docs/PR-REPORT-TEMPLATE.md` | +105 / −0（新增） | 三份证据的逐节骨架，供开新 PR 时整份复制。 |
| `tests/test_pr_report_discipline.py` | +138 / −0（新增） | 把规范本身与报告合规变成断言：模板存在且标题级含三章节；`AGENTS.md`/`DEV-HANDOVER.md` 仍写明要求并指向模板；文件名日期 ≥ 2026-09-22 的报告必须标题级含三章节且在 `docs/INDEX.md` 登记。历史报告豁免。 |
| `docs/INDEX.md` | +2 / −0 | 按新文档入场规则登记模板与 issue 草稿。 |
| `docs/issue-draft-主动识屏v420.md` | +66 / −0（新增） | v4.2.0「主动识屏永不触发」的 issue 草稿：`MultiWindowProxy._physics_mode` 返回 `bool` 破坏哨兵语义 → G1 守卫恒真 → 每次 8s tick 静默拦截；附最小修复建议与同版本启动装配缺口。尾部按入场规则补基线声明与互链。 |
| `pet/catalog.py` | +32 / −0 | 新增 `character_head_box(character_id)`：读 manifest 的 `head_box`（源像素、640×360 画布坐标，范围校验后返回），未声明时按身体框上缘取 `HEAD_FALLBACK_RATIO = 0.45` 兜底；缓存口径沿用 `character_body_box` 的 `lru_cache`。**目的**：边缘探头要「只露头」，锚点必须是头（用整只轮廓会把切面落在胸口，实测真实露出 100%）。 |
| `assets/characters/shenshen/videos/manifest.json` | +2 / −1 | 声明 `head_box: [185, 60, 436, 195]`；`body_box` 原值不变。 |
| `scripts/build_onedir.ps1` | +20 / −0 | 排除其余 Qt 绑定（`PyQt5`/`PyQt6`/`PySide2`）与把绑定拖进来的上游链（matplotlib 家族、IPython/jupyter/zmq/nbformat）。**为什么**：打包机上装了 PyQt5 时，PyInstaller 在收集阶段直接中止（见实机记录 4），而 edge-tts 正是在收集阶段被打断的。 |
| `scripts/verify_bundle_tts.py` | +257 / −0（新增） | 产物 TTS 自检：打包机算 `import edge_tts` 的导入闭包，产物 exe 的 CArchive→PYZ 与 `_internal/` 两侧取真实清单做包含判定；缺一即非 0 退出。 |
| `tests/test_verify_bundle_tts.py` | +114 / −0（新增） | 钉住闭包三条过滤（启动期注入不算、运行时合成模块不算、pkgutil 空壳不算）与「真包仍要算」；改前 5 red → 改后 7 green。 |

合计：11 个文件，+781 / −7。

## 性能分析

环境：Windows，解释器 `E:\Program Files (x86)\Dev-Cpp\python.exe`（CPython 3.11.1），
PySide6 6.11.1，PyInstaller 6.20.0；该环境 `site-packages` 里装着 matplotlib(27 MB)、
PyQt5(148 MB)、IPython(7 MB)、ipykernel(1 MB)、seaborn(3 MB)、zmq(3 MB)——**合计
189 MB（上界）**，这正是「打包机环境是构建结果的函数」那个坑的来源。

逐条回答（数字均为本机实测）：

| 问题 | 结论 | 依据（命令 / 数字） |
|---|---|---|
| 稳态开销 | **0** | 产品代码唯一改动是 `pet/catalog.py` 新增函数，全仓 grep 只有不入库的 `.scratch/edge-peek-exposure/calib.py` 调用它；没有任何产品路径新增调用。 |
| 新增路径成本与触发频率 | 冷 0.529 ms / 命中 0.0000 ms | `timeit`（200 次/组，同进程）：`cache_clear()` + `character_head_box('shenshen')` = 0.529 ms/次；命中 `lru_cache` = 0.0000 ms/次。接线后预计每次探头起手 1 次。 |
| 有无新的系统调用 / 网络 / 磁盘 / 线程 | **无** | `character_head_box` 只在冷缓存时读一次 `manifest.json`（`load_character_manifest`），之后命中缓存不再读盘；新增文件 `scripts/verify_bundle_tts.py` 是**构建期**脚本，不启动 GUI、不联网、不建线程，读产物 exe 的 CArchive 实测 0.464–0.605 s/次；`build_onedir.ps1` 只多传 20 个 `--exclude-module`。 |
| 内存有无增长 | 无新增常驻缓存 | `lru_cache` 只缓存一个 4 元组（每角色一条）；自检脚本是秒级短命进程，不进产品进程。 |
| 产物侧效果 | 新产物 `_internal/` 里**没有** `PyQt5` / `matplotlib` / `IPython` 目录 | `ls -d dist-onedir/*/_internal/{matplotlib,PyQt5,IPython}` 无输出；同时 `edge_tts` 存在。 |

**未测并说明原因**：构建耗时 A/B 没做。理由不是「不方便」，而是本机上**不存在可比
基线**——不加排他时构建在收集阶段就被 PyInstaller 中止（实机记录 4），根本跑不到
完成；加了排他才有产物。因此本 PR 对构建期的断言只落在「产物内容」与「能否构建
完成」两件可测的事上，不用耗时形容词凑数。

## 实机运行记录

本机真实环境（非 CI、非 mock），下列命令与输出为原样摘录。

### 1. 修复后的自检（真产物，PASS）

```
$ python scripts/verify_bundle_tts.py --app-dir dist-onedir/dsh-pet-standalone-webm-chat
[tts] bundle TTS stack OK (25 modules required, 2178 frozen)
{"exe": "...\\dsh-pet-standalone-webm-chat.exe", "frozen_modules": 2178, "required_modules": 25, "missing": [], "checked": ["aiohappyeyeballs", "aiohttp", "aiohttp.client", "aiosignal", "attr", "certifi", "edge_tts", "edge_tts.communicate", ..., "tabulate", "typing_extensions", "yarl"]}
exit=0        real 0m0.605s

$ python scripts/verify_bundle_tts.py --app-dir dist-onedir/dsh-pet-standalone-webm
[tts] bundle TTS stack OK (25 modules required, 2068 frozen)
exit=0        real 0m0.464s
```

### 2. 修复前的同一命令（真产物，假红）

```
$ python scripts/verify_bundle_tts.py --app-dir dist-onedir/dsh-pet-standalone-webm-chat
[tts] FAIL missing TTS modules: cython_runtime, google, mpl_toolkits, pywin32_bootstrap
exit=4
$ python scripts/verify_bundle_tts.py --app-dir dist-onedir/dsh-pet-standalone-webm
[tts] FAIL missing TTS modules: backports, cython_runtime, google, mpl_toolkits, pywin32_bootstrap
exit=4
```

四个（no-chat 五个）「缺失」全与 edge-tts 无关：`google` / `mpl_toolkits` /
`pywin32_bootstrap` 是 `.pth` 在解释器启动期注入的（探针：`import edge_tts` 之前
它们就已在 `sys.modules`）；`cython_runtime` 是 Cython 扩展运行时现造的模块
（`__file__` 为 `None`）；`backports` 是 pkgutil 空壳（`__init__.py` 只有一行
`extend_path`），aiohttp 的 `from backports.zstd import ...` 先建空壳再失败，而
`backports.zstd` 只是 aiohttp 的 `speedups` extra（未装，且包在 `try/except
ImportError` 里）。

### 3. 判定强度未降低：负例仍红

现造一个**真的没有 TTS 栈**的 onedir 产物（`PyInstaller --onedir`，5 s 完成）：

```
$ python scripts/verify_bundle_tts.py --app-dir C:/tmp-ttsneg/dist/ttsneg
[tts] FAIL missing TTS modules: aiohappyeyeballs, aiohttp, aiohttp.client, ..., yarl
exit=4        （25 个必需模块全缺）
$ python scripts/verify_bundle_tts.py --app-dir C:/tmp-ttsneg/nope
[tts] FAIL app-dir-missing
exit=2
```

### 4. Qt 绑定互斥：最小复现（这也是构建必须排他的原因）

```
$ python -m PyInstaller --onedir --noconfirm --clean --distpath C:/tmp-qtneg/distB -n qtnegB appB.py
ERROR: Aborting build process due to attempt to collect multiple Qt bindings packages: attempting to run hook for 'PyQt5', while hook for 'PySide6' has already been run! PyInstaller does not support multiple Qt bindings packages in a frozen application - either ensure that the build environment has only one Qt bindings package installed, or exclude the extraneous bindings packages via the module exclusion mechanism (--exclude command-line option, or excludes list in the spec file).
exit=1
```

`appB.py` 只做两件事：`import PySide6.QtCore` + `import PyQt5.QtCore`。PyInstaller 在
错误信息里给出的处置方式正是本 PR 采用的 `--exclude`。

**边界说明（不许用沉默代替结论）**：产品级 `build_onedir.ps1` 的这次中止发生在
2026-09-22 的构建会话里（当时构建被中止 → 加排他 → 重建成功，产物
`dist-onedir/dsh-pet-standalone-webm{,-chat}` 即其产物，330/331 MB，含 `edge_tts`）；
本轮**没有重跑产品级 A/B**，因为不加排他必然中止、加上排他则要数分钟且会覆盖现有
产物目录。所以本轮只把机制用最小复现钉死，并把可复测的部分（产物内容、自检结果）
跑在真产物上。

### 5. 回归用例：先红后绿

```
$ pytest -q tests/test_verify_bundle_tts.py     # 改前
5 failed, 2 passed in 0.94s
  FAILED test_startup_injected_module_is_not_part_of_closure
  FAILED test_synthesized_module_without_file_is_not_packable
  FAILED test_pkgutil_namespace_shim_is_not_packable
  FAILED test_regular_package_is_still_packable
  FAILED test_module_missing_its_file_is_not_packable

$ pytest -q tests/test_verify_bundle_tts.py     # 改后
7 passed in 0.58s
```

### 6. 代码门禁

```
$ ruff check pet/ tests/            → All checks passed!
$ pytest -q（全量，QT_QPA_PLATFORM=offscreen，--basetemp=C:/pt-wip）
2811 passed, 11 skipped, 11 warnings in 210.27s (0:03:30)
$ pytest -q tests/test_verify_bundle_tts.py tests/test_pr_report_discipline.py
（自检回归 7 条 + 交付证据纪律校验，均通过；本报告即受该校验约束的报告之一）
```

## 已知限制与后续

1. **`character_head_box()` 当前零调用方**：`pet/window.py` 的边缘探头锚点尚未改用它，
   所以本 PR 只落「数据 + 取值函数」，不会让任何现有行为发生变化。接线（并与
   `EDGE_PEEK_EXPOSURE` 的语义一起校正）是下一步，实测基线与修复方向见
   `.scratch/edge-peek-exposure/HANDOFF.md`（不入库）。
2. `docs/PR-REPORT-TEMPLATE.md` 是本 PR 新引入的模板；历史报告不回溯补齐（纪律只对
   2026-09-22 及之后的报告生效，有测试钉住豁免边界）。
3. 打包机上 `--collect-all aiofiles` 已是死重量（edge-tts 7.2.8 不再使用它），本 PR
   未处理，留作后续瘦身项。

## 风险与回滚

- **风险面**：产品运行时代码零改动；构建脚本改动只影响**构建期**（多 20 个排除项），
  若某个排除项被误判为必需，症状是产物启动时报 ImportError——用 `git revert` 本 PR
  的构建脚本提交即可恢复（排除清单是纯增量，回滚不涉及数据迁移）。
- **自检脚本**：新增文件，不被产品代码引用；若判定过严会导致构建判红，可临时以
  `python scripts/verify_bundle_tts.py` 手工复核，再按本报告的假红章节排查。
- **回滚**：`git revert` 对应提交即可；无配置键变更、无持久化数据变更、无迁移。
