# PR 报告：纯桌宠版「桌宠隐藏后单击灵动岛无反应」（2026-09-23）

> **基线**：`0932668`（main，v4.2.1 发布后）　**修复提交**：`1c3a59c`
> **分支**：直接落 main（用户要求「紧急修复、不走 PR」）
> **范围**：2 个文件（实现 1、测试 1），另有本报告、README 与发布说明的补丁段
> **关联**：[`RELEASE-v4.2.1.md`](RELEASE-v4.2.1.md)、[`PR-REPORT-PERF-ISLAND-CONSOLIDATED-2026-09-23.md`](PR-REPORT-PERF-ISLAND-CONSOLIDATED-2026-09-23.md)（灵动岛近期批次）

## 一、核心特性

**问题**（用户反馈）：**纯桌宠版**（无 Chat 的 `webm` 变体）把桌宠隐藏后，点击灵动岛**没有任何反应**，用户被困在「看不到桌宠」的状态里，只能走托盘「显示桌宠」。

**根因**（一条可用性闸门 + 一条默认分支的交叉）：

| 环节 | 代码 | 行为 |
|---|---|---|
| 岛单击路由 | `pet/dynamic_island.py::mouseReleaseEvent` | 桌宠隐藏且 `hidden_chat`（配置默认 **开**）时发 `chat_requested` |
| AppShell 入口 | `pet/app.py::_chat_from_island` | 直接调 `_show_island_chat` |
| 气泡闸门 | `pet/app.py::_show_island_chat` | `if not self._island_chat_available(): return` —— **静默返回** |
| 无 Chat 变体 | `dsh-pet-standalone-webm.spec` `excludes=['pet.chat', …]` | `enable_chat=False` → `_island_chat_available()` 恒为假 |

于是：**岛发了请求，AppShell 记下请求后什么都没做**。岛又是桌宠隐藏后唯一的常驻交互面，所以这件事在纯桌宠版上是「死路」，而在有 Chat 的变体上恰好被气泡兜住了（气泡里有「显示桌宠」按钮），所以只有纯桌宠版暴露。

**修复**：`_chat_from_island` 先判可用性——没有对话能力时改调 `_show_pets_from_island_chat()`，即「显示全部窗 + 同步岛 `_pet_visible` + 收起气泡」，正是气泡内「显示桌宠」按钮用的那条通路。语义变成：**单击岛 = 显示桌宠**。

**红线 / 不变量**：

- 有 Chat 的变体行为**一字不变**：仍是「单击弹岛气泡 → 气泡内点『显示桌宠』」；
  `click_action == "toggle_pet"` 的用户偏好仍然优先（岛侧分支未动）。
- `hidden_chat` 关闭时仍然展开卡片（旧行为保留，卡片的「显示桌宠」按钮照旧可用）。
- 新代码路径在纯桌宠版之外**不可达**：有 Chat 时 `_island_chat_available()` 为真，直接走原分支。

## 二、修改文件说明

`git diff --numstat`（`0932668` → `1c3a59c`）：

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/app.py` | +11 / −2 | `_chat_from_island`：无对话能力时改为把桌宠叫回来（调 `_show_pets_from_island_chat`），并补 docstring 写明「岛是隐藏后唯一交互面」的语义与旧行为为什么是死路 |
| `tests/test_island_chat.py` | +22 / −0 | 新增 `test_shell_click_when_no_chat_reshows_pets`：`enable_chat=False`（无 Chat 变体）+ 桌宠隐藏 → `_chat_from_island()` 后 `island._pet_visible is True`，且**不会**去建气泡 |

另有（同一提交之外，纯文档）：本报告、`docs/INDEX.md` 登记、README 变更记录一条、`docs/RELEASE-v4.2.1.md` 的「发布后补丁」段（说明纯桌宠版产物已重新构建并覆盖 Release 附件）。

### 未改动（故意）

- `pet/dynamic_island.py` 的单击路由**未动**：岛不掌握「有没有对话能力」这一事实（它的 `_hidden_chat_enabled()` 只读配置），把策略放在 AppShell 一处避免两边判断分叉。
- `_show_island_chat` 的闸门未动：它同时服务「回复到达自动预览」等路径，静默返回对那条路径是正确的。
- 不新增配置键、不改任何设置页交互。

## 三、性能分析

- **稳态开销**：零。改动是 `_chat_from_island` 入口处的一次布尔判断（`_island_chat_available()` 原本就会在该调用链里被求值一次，总次数不变），不新增定时器、线程、系统调用、网络或磁盘访问。
- **新增路径成本与触发频率**：新增分支只在「无对话能力 + 用户点了岛」时进入，成本 = 一次 `for inst in _instances: win.show()`（实例数与桌宠数同级，实测单实例 <1ms）+ 一次 `set_pet_visible`。触发频率 = 用户手动点击（不是周期性路径）。
- **内存**：无增长（未新增缓存/集合/长生命周期对象）。
- **对照：修复前后的可观测差异**（真机、有 Chat 的桌宠窗 + 强制 `enable_chat=False` 模拟无 Chat 变体）：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 桌宠隐藏 → 点岛 | `_pet_visible` 仍为 `False`（点击被静默吞掉，日志无痕） | `_pet_visible` → `True`，全部窗 `show()` |
| 桌宠可见 → 点岛 | 展开卡片 | 展开卡片（不变） |
| 隐藏 + `hidden_chat` 关 → 点岛 | 展开卡片（卡片内有「显示桌宠」按钮） | 展开卡片（不变） |

## 四、实机运行记录

**① 回归用例（先红后绿）**——`QT_QPA_PLATFORM=offscreen`，`E:\Program Files (x86)\Dev-Cpp\python.exe`（CPython 3.11.1）：

```
修复前：$ pytest -q tests/test_island_chat.py -k no_chat_reshows
        E  AssertionError: 点击后桌宠仍未恢复可见
        E  assert False is True
        1 failed, 18 deselected in 1.23s        ← 复现用户报的「点了没反应」

修复后：$ pytest -q tests/test_island_chat.py tests/test_island_shell_wiring.py
        23 passed in 0.79s
```

**② 全量门禁**（本机 Windows / Python 3.11.1 / PySide6 6.11.1）：

```
$ ruff check pet/ tests/ scripts/        → All checks passed!
$ QT_QPA_PLATFORM=offscreen pytest -q    → 2914 passed / 11 skipped / 0 failed
```

**③ 产物重建与覆盖（覆盖 Release v4.2.1 的纯桌宠附件）**——由三平台 `workflow_dispatch`
（`--ref main`，即 `1c3a59c`）重新构建，构建日志里带各平台的全量测试结果；产物下载后
以 `gh release upload v4.2.1 --clobber` 覆盖下列**纯桌宠（无 Chat）**附件：

| 附件 | 平台 / 变体 |
|---|---|
| `dsh-pet-standalone-webm-portable.zip` | Windows x64 · 绿色版 |
| `dsh-pet-standalone-webm-setup.exe` | Windows x64 · 安装版 |
| `dsh-pet-standalone-webm-macos-arm64.zip` | macOS arm64 |
| `dsh-pet-standalone-webm-linux-x86_64.zip` | Linux x86_64 |

有 Chat 的四个附件**未替换**：新代码路径在它们里不可达（`_island_chat_available()` 为真），
产物行为与已发布版本逐字节等价的功能语义一致；替换只会让校验和变化而不带来任何差异。

**④ 为什么不重新打 tag**：用户要求「覆盖 release 里的对应内容」。`v4.2.1` 的 tag 与
`pet/__init__.py` / `packaging/dsh-pet.iss` 的版本号保持 `4.2.1`，纯桌宠附件是**同版本重构建**，
发布说明里已写明「构建于 `main@1c3a59c`（含岛唤起修复）」，读者可据此核对。

## 五、遗留与已知边界

- 纯桌宠版若**关闭了灵动岛**（设置 → 灵动岛 → 启用），隐藏桌宠后仍只能走托盘菜单
  「显示桌宠」（托盘入口一直在，未动）。
- 岛处于**停靠细条**模式时：第一次单击是滑出预览、第二次单击才落到本次修复的分支
  （与有 Chat 变体的手感一致）——这是 `mouseReleaseEvent` 既有的两段式语义，未改。
- 本轮没有为「无 Chat 变体」加独立的打包期用例（例如断言 spec 的 excludes 与运行时
  `enable_chat` 一致）；现有 `test_island_chat_availability_gates` 已用 `enable_chat = False`
  覆盖该状态，打包侧由 `scripts/verify_bundle_*.py` 把关。
