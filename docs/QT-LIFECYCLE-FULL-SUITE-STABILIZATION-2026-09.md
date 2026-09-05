# Qt 生命周期与全量测试稳定性收口记录

**日期**：2026-09  
**范围**：`PetWindow`、外置 `PetSpeechBubble`、右键菜单、测试 QApplication 生命周期、无 Chat 配置加载。

## 1. 问题现象

Windows + `QT_QPA_PLATFORM=offscreen` 下，约 1430 个测试在同一进程执行时，曾在不同位置随机出现：

- `0xC0000005` access violation；
- `0xC0000374` heap corruption；
- Python 显示的当前测试并不一定是实际 owner，崩溃常发生在 Qt 原生对象释放或事件派发阶段。

重点排查结论：这不是某一个动画断言或跨进程 broker 压力测试的确定性逻辑失败，而是多个测试共享 QApplication 时，旧 QObject、延迟删除、独立 Tool 窗口和测试桩生命周期叠加造成的原生层风险。

## 2. 生命周期改动的归属与合并关系

本轮没有再建立一套独立的“总生命周期管理器”。最终方案是把新发现的 owner
收口合并到已有生命周期入口，并保留职责分层：

| 生命周期 | 原有入口 | 本轮最终并入的内容 | 最终状态 |
|---|---|---|---|
| `PetWindow` | `closeEvent()` 已负责 watcher、碰撞、输入、timer、素材库和当前 movie | 增加 `_close_event_done` 幂等闸门；将无父级 `PetSpeechBubble` 的断线、关闭和同步销毁放在其他服务 shutdown 之前 | 保留并合并 |
| `PetSpeechBubble` | `dismiss()` / `close()` 管理可见状态和内部 timer | 真实气泡由窗口 owner 在 GUI 线程 `shiboken6.delete()`；关闭后清空引用 | 保留并合并 |
| 右键 `QMenu` | `_show_context_menu()` 管理嵌套事件循环、活动菜单和延迟回调 | 新增 `_exec_context_menu()` 测试 seam；局部菜单测试改为定向销毁 | 保留并合并 |
| AgentLink monitor | `AgentLinkManager.shutdown()` / `BaseAgentMonitor` 自身退出协议 | pytest 每测试结束调用现有 `_shutdown_live_for_tests()`，没有复制 worker 停止逻辑 | 沿用原有入口 |
| `MovieLibrary` / WebM reader | `MovieLibrary.shutdown()`、clip `cleanup()`、`_ORPHAN_REGISTRY` | pytest 每测试结束关闭 live library；session 结束对 registry 和存活 reader 做最后有界收口 | 沿用并强化测试防线 |
| Chat session writer | `reset_writers_for_tests()` | pytest 每测试结束调用现有 reset，避免 writer 在 `tmp_path` 删除后继续写盘 | 沿用原有入口 |

排查期间曾尝试或考虑过、但**没有进入最终设计**的方案：

- 不保留扫描 `QApplication.topLevelWidgets()` 并关闭所有 `PetWindow` 的通用夹具；
  它无法证明 owner，且可能重入产品 `closeEvent()`。
- 不保留进程级 `sendPostedEvents(None, DeferredDelete)`；它会执行其他测试留下的
  删除任务，把历史 QObject 生命周期集中引爆在当前测试。
- 不把所有顶层 QWidget 一律 `shiboken6.delete()`；同步销毁只用于已确认由
  `PetWindow` 独占、无 QObject 父级的真实 `PetSpeechBubble`，以及测试自己创建的局部菜单。
- 不把测试 teardown 当成产品修复替代品；产品 owner 仍必须在自己的关闭入口停止
  worker、timer、reader 和外置窗口。

## 3. 已落实的修复

### 3.1 PetWindow 关闭幂等

`pet/window.py` 的 `PetWindow.closeEvent()` 增加关闭完成闸门。重复 `close()` 只接受事件，不重复停止 Agent monitor、素材库、定时器或交互 hold。

### 3.2 外置气泡同步销毁

`PetSpeechBubble` 是独立的无父级 Tool 窗口，不能依赖 `PetWindow` QObject 父链自动释放。关闭时现在按以下顺序处理：

1. 对真实 `PetSpeechBubble` 断开 `clicked` / `hidden_signal`；
2. 调用 `dismiss()` 和 `close()`；
3. 在 GUI 线程使用 `shiboken6.delete()` 同步销毁；
4. 清空 `PetWindow._speech_bubble` 引用。

这样避免把外置气泡留在进程级 `DeferredDelete` 队列中，随后由无关测试的全局事件冲刷触发旧 native window。

对轻量 FakeBubble 只调用其存在的 `dismiss()` / `close()`，不要求测试桩实现生产对象的 Qt 信号。

### 3.3 右键菜单执行 seam

`PetWindow._show_context_menu()` 通过 `_exec_context_menu()` 执行 `QMenu.exec()`。正式实现仍调用原生菜单；测试可以替换 Python 层 seam，而不再 monkeypatch PySide C++ 方法。

为了兼容旧的 duck-typed 测试对象，如果对象没有 `_exec_context_menu()`，仍回退到 `menu.exec()`。

### 3.4 测试中的定向清理

测试不再用以下方式清理局部对象：

```python
QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
```

该调用会处理整个共享 QApplication 中所有对象的延迟删除，不只处理当前测试创建的对象。菜单测试改为定向 `shiboken6.delete(menu)`；窗口测试直接断言气泡 wrapper 已失效。

测试夹具也不再扫描所有顶层 PetWindow 并重复调用 `close()`，避免 teardown 重入产品关闭路径。

### 3.5 无 Chat 配置加载

无 Chat PyInstaller 入口排除了 `pet.chat` 和 `keyring`，但配置迁移代码仍可能读取含历史 `chat.providers` 的配置。`Config._migrate_plaintext_keys_to_keyring()` 现在只对缺失的 `pet.chat` 跳过迁移；其他导入错误继续抛出。

这修复了：

```text
ModuleNotFoundError: No module named 'pet.chat'
```

## 3. 验证记录

所有命令均在 Windows、`QT_QPA_PLATFORM=offscreen` 下执行：

| 验证 | 结果 |
|---|---:|
| 主套件（排除最终跨进程压力测试） | 1421 passed, 7 skipped, 1 deselected |
| 最后单独运行 `test_cross_process_concurrent_publish_read_stress` | 1 passed |
| 菜单/窗口/预热相关组合 | 35 passed |
| AgentLink、窗口、预热组合 | 177 passed |
| 两个高风险回归各运行 20 个新进程 | 20 轮全部通过 |
| 架构、配置迁移、无 Chat 入口检查 | 8 passed |
| `git diff --check` | 通过 |

主套件运行到 100% 时没有再出现 access violation、heap corruption 或 Python fatal abort。最终跨进程压力测试按约定留到主套件之后执行，并通过。

## 4. 诊断规则

后续再出现 native abort 时，先按顺序处理：

1. 使用 `QT_QPA_PLATFORM=offscreen python -m pytest -x -q` 找到第一个行为失败；
2. 记录崩溃时所有 Python / Qt 线程和测试位置，但不要直接把当前测试认定为 owner；
3. 搜索该测试之前创建的无父级 QWidget、QMenu、QThread、QTimer、ffmpeg reader；
4. 检查是否调用了全局 `sendPostedEvents` 或留下 `deleteLater()`；
5. 优先把真实 native 边界替换为 Python seam，或对当前 owner 做定向同步销毁；
6. 修复后先跑相关组合，再跑非压力主套件，最后才运行跨进程/多线程压力测试。

`QApplication` 在 pytest 进程内是共享资源。普通 `close()` 不等于 C++ 对象已经完成销毁，`processEvents()` 也不保证处理所有 DeferredDelete。因此测试应明确 owner、停止 worker、清除 timer，并避免全局冲刷历史队列。

## 5. 未覆盖范围

offscreen 测试不能替代真实 Windows 桌面、macOS Cocoa 或打包后进程验证。涉及原生窗口、系统托盘、真实媒体解码和跨进程 IPC 的改动，仍需在对应平台做实机冒烟；本记录只确认本次 Windows offscreen 全量测试与指定压力测试结果。
