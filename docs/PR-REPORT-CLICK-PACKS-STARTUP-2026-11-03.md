# PR 报告：内置点击音效包 / 自启首帧后装扩展 / 点击反馈抗阻塞（2026-11-03）

分支：`feat/petcore-vpet-architecture`　对应 LOG：`docs/PETCORE-LOG.md` L167

用户三项反馈：

1. 把 `Deep!Seek!` 与 `to鲸wu` 两个音频文件夹做成内置点击音效包（包名 = 文件夹名）；
2. 开机自启动用时久；
3. 个别用户点击桌宠时 Q 弹/动画/气泡全都不触发；换回内置音效后动画与气泡恢复，
   但 Q 弹仍然没有。

---

## 1. 修改文件说明

（`git diff --numstat`，2026-11-03；`petcore/config.py`、`docs/*` 含同工作区未提交的
L164–L166 改动，实际本 PR 行数见括注。）

| 文件 | 增/删 | 改了什么 + 为什么 |
|---|---|---|
| `assets/sounds/Deep!Seek!/`（新增 22 个文件） | — | 11 组素材的 mp3 + 同名 44.1kHz/16-bit PCM wav。wav 由 QtMultimedia `QAudioDecoder` 离线转出，只为让运行期走 `QSoundEffect`（不新建 `QMediaPlayer`）。 |
| `assets/sounds/to鲸wu/`（新增 10 个文件） | — | 同上，5 组素材。两包合计 6.61 MB。 |
| `petcore/media/click_sound.py` | +47/−14 | 内置包白名单 `BUILTIN_PACK_DIRS`；builtin 分支由「硬编码 duck」改成「`default` → `click.wav`，其余 id → `assets/sounds/<id>`」；内置候选进程级缓存；folder 分支补 `OSError` 兜底 + 按目录 mtime 缓存（点击路径不再每次 `iterdir`）。 |
| `pet/click_sound.py` | +56/−14 | legacy/overlay 路径同款泛化；`_duck_candidates` 改 wav 优先（与 petcore 同口径）；新增 `_folder_candidates` 缓存；`_reset_caches_for_tests` 一并清理。 |
| `petcore/config.py` | +1/−1（另含 L166 的 17/1） | `_CLICK_SOUND_BUILTINS` 加两个新 id；未知 id 仍回退 `default`。 |
| `pet/config.py` | +5/−1 | 新增 `_CLICK_SOUND_BUILTINS` 并用于清洗 `click_sound_pack`。 |
| `petcore/ui/settings/page_interaction.py` | +19/−10 | `BUILTIN_SOUND_PACKS` 注册表驱动下拉；`_sound_path_text()` 泛化；搜索词补 `Deep!Seek!` / `to鲸wu`。 |
| `pet/settings_widgets.py` | +16/−6 | `ClickSoundPackPicker` 同款注册表；`set_pack()` 对未知 builtin id 兜底到 default（不再误配）。 |
| `petcore/ui/pet_window.py` | +51/−16 | 松手不再内联解析/播放音效（只 cancel token，`_on_click` 里 `singleShot(0)` 播）；`_play_spec` 异常不再吃掉 `start_squash`；`is_transparent_at` 改用「基准帧矩形 + 镜像反变换」；拖拽阈值取 `max(5, QApplication.startDragDistance())`；预热限量 8 且 `click_sound` 关闭时跳过；点击闸门写 debug 日志。 |
| `petcore/ui/effects.py` | +17/−3 | Q 弹起点改为「第一次绘制时锚定」（`_squash_started=None`），隐藏窗口 1s 后自动收表。点击处理后半段再慢，脉冲也不会按墙钟先走完。 |
| `petcore/ui/app.py` | +103/−33 | `attach_window` 拆成 `attach_window_shell` + `feature_load_steps()`（内置 feature → 用户 mod → 菜单/灵动岛）；`run()` 在首帧后 120ms 分批加载；新增 `bootstrap` / `config` / `features` 分段日志。 |
| `petcore/cli.py` | +6/−1 | 记录 `BOOT_T0` 与 `Config.load` 耗时并传给 `run()`。 |
| `petcore/__init__.py` | +5 | 导出 `BOOT_T0`。 |
| `scripts/build_petcore.ps1` | +4 | `--add-data` 把 `THIRD_PARTY_NOTICES.md` 与 `LICENSE` 放进包根（此前 petcore 构建不分发声明）。 |
| `tests/test_petcore_click_sound.py` | +81 | 新包解析（11/5 个 wav）、两套白名单一致、legacy 与 petcore 白名单一致、设置页枚举、folder 缓存、目录不可读兜底。 |
| `tests/test_petcore_interaction.py` | +80 | Q 弹首帧锚定、松手不内联解析音效、素材失败仍 Q 弹、镜像命中、预热限量。 |
| `tests/test_click_sound.py` | +4/−3 | duck 候选断言改成 wav 优先（与产品口径一致）。 |
| `THIRD_PARTY_NOTICES.md` | +15 | 第 10 节：两个音效包的来源（用户提供）、无第三方许可声明、转码说明与撤下方式。 |
| `docs/PETCORE-SPEC.md` | +1/−1 | §11 音效包清单补两个新包与「目录名即包名 / wav 优先」。 |
| `docs/PETCORE-LOG.md` / `docs/PETCORE-LOG-INDEX.md` | +L167 行 | 变更记录与索引。 |

未改动：打包脚本（`--add-data assets\sounds` 是整目录，新子目录自动进包）、
`source_sha256` 的「每次按内容重算」口径、安装目录结构。

---

## 2. 性能分析

环境：Windows 11 26200、Python 3.12.10（项目 `.venv`）、PySide6 6.12.0、本机 NVMe、
页缓存热；无音频端点（offscreen），音频设备打开耗时只能代码口径。

### 2.1 稳态开销

| 路径 | 改前 | 改后 | 说明 |
|---|---|---|---|
| 内置包候选解析（每次点击/碰撞） | duck 每次目录扫描 + 排序（11 目录项） | **6.7–11.9 µs/次**（2000 次平均） | 进程级缓存；`default` 仍是 1–2 次 `stat` |
| folder 包候选解析（每次点击） | 每次 `iterdir` + `sort`（11 文件实测 **0.29 ms**；128 文件按线性约 1.5 ms，网络盘无上限） | **17.5 µs/次**（缓存命中），首扫 0.29 ms | 缓存键 = 目录 mtime，增删文件自动失效 |
| 音效预热（首帧后 200ms 一次） | 包内**全部**候选（folder ≤128 → 一次建 128 个播放器） | ≤8 个候选 + `click.wav`；`click_sound` 关闭时 0 个 | 新增包 11/5 个 wav，最多 9 个 `QSoundEffect` |
| 点击处理 | 松手时同步解析 + 播放在 `_on_click` 之前（首个 mp3 **185 ms**、首个 wav 71 ms） | 解析/播放挪到 `singleShot(0)`，且异常被兜住 | Q 弹/动画/气泡不再被音效阻塞 |
| 启动 attach 段 | 首帧前同步 **中位 688 ms、最大 3777 ms** | 首帧后分 3 拍；同一 profile 实测 **35–103 ms** 总耗时 | 窗口可用时间从「attach 结束」提前到 `character`（281–303 ms） |

### 2.2 新增路径成本与触发频率

- **内置包缓存**：首次解析一次目录（µs 级），之后每次点击/碰撞 7–12 µs；触发频率 = 点击与
  有效碰撞（原逻辑本来每次都付，改后只付一次）。
- **folder 缓存**：只在 mtime 变化后重扫；正常情况下每次点击 17.5 µs。
- **镜像命中**：`is_transparent_at` 多一次 `_frame_base_rect()`（纯算术，无分配）与一次
  `_mirrored()`；10 ms 轮询路径实测无可见变化（无 I/O、无锁）。
- **Q 弹锚定**：无新增调用；只是把 `_squash_started` 从 `start_squash` 挪到 `squash_rect`。
- **启动分拍**：`feature_load_steps()` 把原来的顺序循环拆成 3 个闭包 + 3 次 `singleShot(0)`；
  一次性开销是 2 个额外事件循环拍（<1 ms 调度成本）。
- **预热限量**：从「最多 128 次 `prepare`」降到 9 次，首帧后那段尖峰随之消失。

### 2.3 新的系统调用 / 网络 / 磁盘 / 线程

- 新增磁盘：**无额外运行期读**。内置包首次解析一次目录列表（与原逻辑同量）；folder 缓存
  反而把「每次点击一次目录扫描」降为「目录变化后一次」。
- 新增系统调用：无。`QApplication.startDragDistance()` 是 Qt 缓存值；
  `_frame_base_rect()` 纯算术。
- 新增网络 / 线程：**无**。启动分拍仍在 GUI 线程，只是让出事件循环。
- 启动多了一次 `time.perf_counter()`（两次）与一行日志。

### 2.4 内存

- 选中新包时最多 9 个 `QSoundEffect`（≤2s × 44.1kHz × 2ch × 16bit ≈ 350KB/个）
  ≈ **3 MB**，与原来 duck 的 2 个同量级；`_MAX_CACHED=16` 上限不变。
- 内置候选缓存：4 个包 × ≤11 个 `Path`，KB 级；folder 缓存上限 8 条（每次 ≤128 `Path`）。
- 音效包打包体积：**+6.61 MB**（16 个 mp3 ≈ 0.4 MB + 16 个 wav ≈ 6.2 MB，实测目录 6,926,166 B）。

### 2.5 实测命令

```powershell
# 候选解析（2000 次平均）
.venv\Scripts\python.exe - <<'PY'  # 见下方输出
...
PY
# builtin default cached: 11.9 us/call；duck 6.7；Deep!Seek! 7.4；to鲸wu 7.9
# folder first=0.29 ms cached=17.5 us/call
```

启动（headless，`--root` 指向空根 / 复制真实配置）：

```
启动耗时 bootstrap=392ms config=12ms qapp=51ms lock=54ms host=294ms character=303ms attach=304ms restore=314ms
扩展加载耗时 features=35ms          # 空根

启动耗时 bootstrap=353ms config=6ms qapp=46ms lock=48ms host=274ms character=280ms attach=281ms restore=296ms
扩展加载耗时 features=103ms         # 复制真实 config + mods
```

改前同机 24 次真实启动（`%APPDATA%\dsh-pet-core\logs\petcore.log`）：

```
qapp median 33.5ms / host 358.5ms / character 381ms / attach 1177ms / restore 1195.5ms
attach 段时长（attach-character）：median 688ms，max 3777ms
```

---

## 3. 实机运行记录

### 3.1 真机启动（源码）

```powershell
$env:QT_QPA_PLATFORM="offscreen"
.venv\Scripts\python.exe -m petcore --root .scratch\run-root
```

真实输出（日志原文）：`启动耗时 bootstrap=417ms config=13ms qapp=49ms lock=51ms
host=316ms character=323ms attach=325ms restore=335ms` / `扩展加载耗时 features=40ms`；
无 Traceback、无 `ERROR`。复制真实配置/扩展后的第二轮：`character=280ms attach=281ms
restore=296ms` + `features=103ms`，同样无异常。

**用户可见行为**：进程启动到桌宠出现在桌面上是 `character` 段（281–303 ms）；扩展菜单与
灵动岛在那之后的三拍内补齐。改前这段 688 ms–3.8 s 是在窗口 `show()` 之后同步跑的
（窗口已可见但事件循环未起，点击无响应）。

### 3.1.1 安装包产物（签名，可直接安装）

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 `
  -Clean -Installer -OutputRoot rel-storage-2026-11-03d `
  -SignThumbprint 217337C099EC8624AC481BFC9F3E1664D0A442E1
```

- `rel-storage-2026-11-03d/dsh-pet-core-setup.exe`：310,460,024 B（296.08 MiB），
  SHA-256 `35D4A325018CFB42F65EF527880D77C889817B014F265018239CD945E6B21488`，
  Authenticode `Valid`（`CN=DSH Pet Core Dev`）；主 `dsh-pet-core.exe` 同签名。
- 比 L165 的 307,985,983 B 多 2,474,041 B（= 两个音效包的 mp3+wav）。
- 构建内 `--check --strict`：`menu_template=json characters=1 builtin_images=24
  daily_lines=1200 strict=True`、`diagnostics=[]`、退出 0。
- 包内路径确认：`_internal/assets/sounds/Deep!Seek!/`（22 个文件）、
  `_internal/assets/sounds/to鲸wu/`（10 个文件）；`_internal/THIRD_PARTY_NOTICES.md`
  与 `_internal/LICENSE` 随包（本轮给 `scripts/build_petcore.ps1` 补的 `--add-data`——
  此前 petcore 构建不分发许可声明，素材等于裸分发）。
- 打包产物真机启动 12s（`--root .scratch/pkg-root-d`，跑完被强制结束）：
  `bootstrap=176ms config=9ms qapp=34ms lock=36ms host=262ms character=279ms
  attach=280ms restore=298ms` + `扩展加载耗时 features=922ms`。
  这是**打包后的真实启动**：桌宠在 279ms 出现，扩展/灵动岛在之后分三拍补齐
  （改前这 922ms 是在窗口 `show()` 之后、事件循环之前同步跑的）。

### 3.2 音效包

- 设置 →「互动与物理 → 音效音源」下拉现在有 `内置 · Deep!Seek!` 与 `内置 · to鲸wu`；
  选中后「音效文件」行显示 `内置音效包 · <名字>（assets/sounds/<名字>）`，试听走同一
  `SoundPlayer`。
- 解析结果（真机脚本）：`Deep!Seek!` 11 个 wav、`to鲸wu` 5 个 wav，全部存在。
- 播放探针（offscreen，QSoundEffect）：4 个内置包各前 3 个候选 `prepare → True`、
  `play → True`；缓存 11 个 `QSoundEffect`、0 个 `QMediaPlayer`。

### 3.3 单测

见 §3.4；Q 弹首帧锚定、镜像命中、松手不内联解析音效、素材失败仍 Q 弹均有回归用例。

### 3.4 聚合结果（本机，4 分片并行，按 CI 口径忽略 4 个 webm 生命周期族文件）

```powershell
$env:QT_QPA_PLATFORM="offscreen"
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider <597 个文件按收集序四等分>
```

| 分片 | 结果 |
|---|---|
| 1 | 3016 passed / 11 skipped，4 failed（全部 `tests/test_bundle_slim.py`） |
| 2 | 2710 passed / 2 skipped，4 failed（2 个 `test_module_level_validation`、1 个 `test_overlay_instance_gate`、1 个 `test_packaging_keyring_notices::...utf8_bom`） |
| 3 | 2199 passed / 1 skipped |
| 4 | 3645 passed / 5 skipped，2 failed（`test_settings_stale_form_exit` ×2） |

**合计 11,570 passed / 10 failed**。逐条复核：

- `tests/test_bundle_slim.py`（4 个）：`scripts/slim_bundle.py:112` 已把
  `_internal/PySide6/Qt6WebView.dll` 列为必需文件，而测试的假 bundle 不生成 webview
  文件 → **HEAD 上本来就红**（与本 PR 无关，未修改）。
- `test_windows_build_script_keeps_its_utf8_bom`：`git show HEAD:scripts/build_onedir.ps1`
  前 3 字节是 `232 0a14`，BOM 已不在库里 → **HEAD 上本来就红**（未修改该脚本）。
- 其余 5 个（2 个 `test_module_level_validation`、`test_overlay_instance_gate`、
  `test_settings_stale_form_exit` ×2）单独重跑 **全部通过**，判定为 4 进程并行下的
  时序抖动（与本 PR 无关）。

本 PR 直接相关的回归：`test_petcore_click_sound / test_petcore_interaction /
test_click_sound / test_petcore_click_listeners / test_effects_integration /
test_petcore_settings_coverage / test_petcore_architecture / test_petcore_config_scope /
test_config_schema / test_pr_report_discipline / test_petcore_docs` 全绿。
