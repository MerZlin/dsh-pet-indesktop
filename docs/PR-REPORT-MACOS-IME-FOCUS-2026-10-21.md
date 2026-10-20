# macOS 输入法焦点抢占修复 PR 报告

> **基线**：`147170f7a89e9cfa71e4e97966e34183087a3fc2`（当前 `main`）  
> **分支**：`fix/macos-ime-focus`　**日期**：2026-10-21  
> **范围**：6 个文件（2 个实现、2 个测试、2 个文档）  
> **关联**：用户反馈「拼音还没打完时桌宠突然抢占主窗口」

## 一、核心特性

修复 macOS 上输入法组合态被桌宠打断的问题。根因是动态岛的置顶保活在非 Windows 平台回退到 Qt `raise_()`；在 macOS 上该调用会激活桌宠 accessory 应用，导致正在输入拼音的应用失去前台。修复后动态岛仍保留 `WindowStaysOnTopHint`，但 macOS 不再调用会激活应用的 `raise_()`；合成桌宠窗口也明确声明不接受键盘焦点，并以 `WA_ShowWithoutActivating` 显示。

| # | 能力 | 说明 |
|---|---|---|
| 1 | macOS 动态岛置顶保活 | macOS 分支只返回保持置顶语义，不通过 `QWidget.raise_()` 重排窗口。 |
| 2 | 合成窗不抢输入焦点 | `OverlayWindow` 使用 `WindowDoesNotAcceptFocus` 和 `WA_ShowWithoutActivating`。 |
| 3 | 回归保护 | 测试验证动态岛不会调用 `raise_()`，合成窗不会接受键盘焦点。 |

**红线 / 不变量**：Windows 仍使用原生 `SetWindowPos(..., SWP_NOACTIVATE)`；Linux 保留原有 Qt `raise_()` 兜底；没有新增配置键、线程、网络请求或持久化格式。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `pet/dynamic_island.py` | +9 / −3 | 把 macOS 从 Qt `raise_()` 回退路径中隔离出来，避免 30 秒刷新、显示、点击或本进程激活事件重新激活桌宠应用。 |
| `pet/overlay_window.py` | +7 / −0 | 给全屏合成窗添加不接受焦点和不激活显示的 Qt 标志，覆盖窗口显示和原生窗口重建路径。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---:|---|
| `tests/test_island_topmost.py` | +20 / −0 | 在模拟 macOS 平台时断言置顶重申不会调用 `raise_()`。 |
| `tests/test_overlay_window.py` | +14 / −0 | 断言合成窗带 `WindowDoesNotAcceptFocus` 和 `WA_ShowWithoutActivating`。 |

### 文档

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `docs/PR-REPORT-MACOS-IME-FOCUS-2026-10-21.md` | 新增 | 记录修改、性能、实机行为和验证门。 |
| `docs/INDEX.md` | +1 / −0 | 登记本报告，满足 PR 报告索引规则。 |

## 三、实现要点

`DynamicIsland.reassert_topmost()` 的 Windows 路径不变。macOS 的 `WindowStaysOnTopHint` 由 Cocoa 维持置顶层级，因此不再调用会改变应用激活状态的 QWidget 层级操作。这个分支只在既有置顶重申事件发生时执行，不进入动画帧循环。

`OverlayWindow` 仍保留原有焦点策略，以支持既有的拖拽和 Esc 收尾路径；新增的窗口标志只约束原生激活行为。两处改动均不改变点击穿透、拖拽、动画或配置语义。

## 四、性能分析

**方法（可复现）**：
`QT_QPA_PLATFORM=offscreen .venv311/bin/python -m pytest -q tests/test_island_topmost.py tests/test_overlay_window.py tests/test_platform_passthrough.py tests/test_desktop_pet_features.py tests/test_platform_mac_shadow.py`  
环境：macOS 26.6 arm64、Python 3.11.17、PySide6 6.12.0；样本量 175 个测试用例。

| 指标 | 实测 | 归属（热路径 / 新增 / 既有） |
|---|---:|---|
| 相关测试总耗时 | 16.86 s | 验证数据，不是运行时成本 |
| 相关测试结果 | 175 passed / 2 skipped | 回归门 |
| 动态岛动画帧新增调用 | 0 次/帧 | 稳态热路径 |
| macOS 置顶重申新增分支 | 每次既有 `reassert_topmost()` 调用 1 次平台判断 | 新增分支 |
| 新增线程 / 网络 / 磁盘写 | 0 / 0 / 0 | 运行时资源 |

**结论**：① 稳态动画和采样循环没有新增调用；② 新分支只在原有显示、点击、应用激活和 30 秒信息刷新事件触发时执行；③ 没有新的系统调用、网络请求、磁盘写入或线程；④ 没有新增缓存或持久化数据，窗口标志由 Qt/Cocoa 原有窗口创建路径处理。

## 五、实机运行记录

**根因现场复现**：在只加入合成窗焦点标志的版本上，将 Finder 激活后观察前台进程：

```text
00:35:07 Finder
00:35:12 dsh-pet-standalone-webm-chat
00:35:17 dsh-pet-standalone-webm-chat
```

这与动态岛 30 秒刷新调用 Qt `raise_()` 的时序一致。进一步检查源码确认 macOS/Linux 分支直接执行 `self.raise_()`。

**修复后用户可见行为**：安装修复版后再次执行同样的前台监控，桌宠没有回到前台；采样结果保持为 Finder 或当前宿主应用，未出现 `dsh-pet-standalone-webm-chat`。修复版应用进程持续运行，启动日志显示正常进入事件循环并加载 106 段动画。

**边界观察**：应用启动时由 `open -a` 激活桌宠属于用户主动启动行为；随后手动激活 Finder 后，桌宠不会自行夺回前台。窗口仍保持置顶显示语义。

**无法自动验证的能力**：本次没有用脚本向用户当前编辑器注入真实中文拼音组合态，因为这依赖当前输入法、编辑器和系统级辅助功能授权；已用真实 macOS 应用前台探针替代验证，并保留针对焦点窗口标志和 `raise_()` 分支的自动回归。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `.venv311/bin/ruff check pet/dynamic_island.py pet/overlay_window.py tests/test_island_topmost.py tests/test_overlay_window.py` | PASS |
| 聚焦 | 上述 5 个测试文件 | 175 passed / 2 skipped |
| 打包 | `PYTHON_BIN="$PWD/.venv311/bin/python" bash scripts/build_macos.sh --dist build/patched-v2 --variants webm-chat` | PASS；OCR、TTS、UTF-8、bridge、签名检查通过 |
| 实机前台探针 | `osascript` 激活 Finder + 55 秒前台采样 | 未观察到桌宠抢前台 |
| 全量 | `QT_QPA_PLATFORM=offscreen .venv311/bin/python -m pytest -q --ignore=tests/test_webm_reader_lifecycle.py --ignore=tests/test_webm_clip_lifecycle.py --ignore=tests/test_webm_first_frame_lock.py --ignore=tests/test_low_priority_warm_interaction_yield.py` | 8254 passed / 82 skipped / 1 个与本改动无关的 `test_island_bevel_fade` 像素阈值失败；该用例在未修改的 `147170f` 基线中用同一命令复跑也失败，输出同为 `checked=38` |

## 七、已知限制与后续

真实中文输入法组合态依赖用户当前编辑器和输入法，无法在离屏测试中完全模拟。若作者希望扩大跨平台覆盖，可另行为 Linux 增加不激活置顶重排实现；本 PR 只处理已实测的 macOS 抢焦点路径。

## 八、风险与回滚

改动不新增配置和数据迁移。若需要回滚，回退本 PR 提交即可；本地安装包另保留在 `/Users/lzy/Applications/dsh-pet-standalone-webm-chat.app.overlay-focus-patched-backup`，可直接恢复旧包。
