# PR 报告：聊天历史分页渲染与窗口卡顿根治（2026-10-21）

> **基线**：`339526c`（fix #315 winmm 跟随默认设备）
> **分支**：`feat/asset-arch-lossless-pack-market`　**日期**：2026-10-21
> **范围**：7 个文件（实现 5、测试 2，其中 1 个新测试文件）
> **关联**：PR #318；用户实测反馈「打开/删除/新建对话卡死数秒并崩溃一次」

## 一、核心特性

用户实测：AI 对话在**打开窗口 / 删除对话 / 新建对话**三个节点整个桌宠卡死
6–12 秒，并有一次进程「崩溃」。根因定位（实机日志 + WER 报告 + offscreen
实测三证合一）：

- 主会话 870 条消息（270KB），`ChatWindow._load()` 把**全部**消息一次性建成
  `MessageBubble`（870 条 ≈ 8700 个 QWidget），同步 ~3s + 事件循环排空 5–9s；
- 三个放大器：`_add` 每条都全量重算所有气泡宽度（O(n²)，870 条 = 76 万次
  setMin/MaxWidth）；每条消息排 4 个零延时定时器（≈3480 个布局失效回调）；
  `_style()` 对 8700 控件整树 setStyleSheet（~1.6s）。
- 所谓「崩溃」是 **AppHangB1**（WER 报告 `AppHang_dsh-pet-standalo_*`，
  2026-10-20 23:57:29）：GUI 线程卡 ≥5s 被系统判「未响应」后终止——
  治好卡顿即治好「崩溃」。

修法不是「优化渲染」，是**不渲染**：首屏只渲染最新 50 条，顶部「加载更早
消息」行按页取回，配合批量加载守卫消掉 O(n²) 与定时器风暴。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 历史分页 | `_PAGE_SIZE=50`；modern/legacy 两个前端同款；加载行显示剩余条数，取完自动消失 |
| 2 | 视口锚定 | 加载更早时视口钉在原第一条，不跳屏、不滚回底部（rangeChanged 收敛锚点） |
| 3 | O(n²)→O(n) | 批量期间每条气泡只设自己的宽度，末尾统一一次全量刷新 |
| 4 | 泄漏×3 | 会话列表行控件（每次刷新泄漏一批）、删除确认框（每次删除泄漏一个顶层窗）、右键菜单（WA_DeleteOnClose） |
| 5 | 悬挂引用 | `_remove_bubble` 后 `self._bubble` 置 None（流式信号不再碰已删对象） |
| 6 | COM 配平 | `sound_winmm._default_render_device_id()` 只在 S_OK/S_FALSE 时 CoUninitialize |

**红线 / 不变量**：流式渲染（`_started`/`_delta`/`_activity`/`_typewriter_tick`）
逐条行为不变；会话文件格式与 `SessionStore` 零改动；发给模型的历史截断
（`prompt.trim_history`）语义不变；快捷聊天（quick_chat）不渲染历史，不在
本次范围（豁免理由见 §七）。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/chat/widgets.py` | +162 / −14 | 分页（`_load`/`_load_earlier`/`_prepend`/加载行三方法）；`_bulk_loading` 守卫（`_add` 批量期只设自身宽度、不排 singleShot）；`_set_bubble_width` 从 `_update_bubble_widths` 提取；`_prepend_anchor` 视口锚定（`_apply_prepend_anchor` + rangeChanged 追平，用户滚动/贴底/清会话解除）；`_refresh_sessions` 摘件+deleteLater；删除确认框 deleteLater；QMenu WA_DeleteOnClose；`_remove_bubble` 清 `_bubble` |
| `pet/chat/legacy_widgets.py` | +126 / −6 | modern 同款镜像（分页/守卫/锚点/悬挂引用）；legacy 无 `_bottom` 定时器风暴与 QListWidget/对话框泄漏点，故泄漏三刀只有悬挂引用适用 |
| `pet/chat/modern_styles.qss` | +5 / −0 | `QPushButton#load-earlier-button` 扁平样式 |
| `pet/chat/legacy_styles.qss` | +10 / −0 | 同上（legacy 配色语言） |
| `pet/sound_winmm.py` | +10 / −5 | `_default_render_device_id()` 记录 `CoInitializeEx` 返回值，仅 S_OK(0)/S_FALSE(1) 才配平 `CoUninitialize`；`RPC_E_CHANGED_MODE`（GUI 线程已是 STA，Qt 初始化过 COM）不增加计数，此前无条件配平会错扣 Qt 的计数（播放热路径每 2s 一次，典型无 traceback native 崩溃隐患） |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_chat_history_paging.py` | +354（新） | 13 条：首屏 50/隐藏计数/加载行文案、prepend 顺序与加载行消失、`restore_thinking` 分页内恢复、锚点不跳屏（含不许滚回底部）、流式底部追加不重置分页态、`new_session` 重置、legacy 镜像、`_load`/`_load_earlier` 期间 `_update_bubble_widths` ≤2 次、`_refresh_sessions` 行控件不增长、删除框取消路径也销毁、QMenu WA_DeleteOnClose、`_remove_bubble` 后 `_bubble is None` |
| `tests/test_winmm_sound.py` | +50 / −0 | COM 配平参数化：hr=0/1 断言配平一次；hr=0x80010106/−1 断言不配平（mock ole32 操作系统边界） |

### 未改动

- `pet/quick_chat.py`：不渲染历史（单 QLabel 显示当前轮），无全量气泡路径；
- `pet/chat/session_store.py` / `models.py` / `prompt.py`：持久化与 prompt
  截断与本缺陷无关；
- `_style()` 整树 setStyleSheet（放大器③）：分页后控件数 8700→~500，其成本
  随控件数线性下降，不再构成秒级项，不单独动（见 §七）。

## 三、实现要点

- **分页方向选「首屏最新 + 顶部取回」而非虚拟化**：QListView 虚拟化要重写
  气泡高度模型（不等高 + markdown 异步折行），风险远大于收益；分页把首屏
  成本钉死在 50 条，「加载更早」是低频主动操作，O(n) 全量渲染只在用户
  明确要翻历史时按页付费。
- **锚点用 rangeChanged 收敛而非单次补偿**：prepend 后内容高度异步继续长
  （气泡折行/渲染），实测单次 singleShot 补偿会被后续布局甩飞（锚点 y 从
  2067 长到 4068）；挂起 `(锚点气泡, 锚点相对视口顶偏移)`，每次
  rangeChanged 追平一次，用户滚动（`_on_scroll_value_changed`）、显式贴底
  （`_bottom`）、清会话时解除。锚点气泡已销毁（切会话）时 RuntimeError
  自愈解除。
- **`_bulk_loading` 计数守卫**而非布尔：`_load_earlier` 内嵌 prepend 与
  `_load` 共用 `_add`，计数形态允许将来嵌套；批量期 `_add` 提前 return
  （不排 `_update_conversation_height` / `_bottom` 的 singleShot），批量
  末尾统一一次 `_update_bubble_widths()` + 高度/贴底。
- **COM 配平按 MSDN 语义**：`CoInitializeEx` 只有 S_OK/S_FALSE 需要配平
  （两者都给公寓计数 +1）；`RPC_E_CHANGED_MODE`（0x80010106，线程已是
  另一套间）与失败值不加计数。`init_hr` 预置 −1 保证异常路径不配平。

## 四、性能分析

**方法（可复现）**：`QT_QPA_PLATFORM=offscreen python .scratch/chat_perf_probe.py <label>`
（合成 870 条会话写临时目录，不碰用户真实数据）。环境：Win11 / CPython
3.13.7 / PySide6 6.11.2。

| 指标 | 修改前 | 修改后 | 归属 |
|---|---|---|---|
| `ChatWindow.__init__` 同步 | 3.559s / 3.074s | **0.177s / 0.180s** | 热路径（每次开窗） |
| 首轮 processEvents 排空 | 0.128s / 0.147s（offscreen 下限） | 0.005s / 0.006s | 热路径 |
| 渲染气泡数（870 条会话） | 870（≈8700 QWidget） | 50 | 新增分页 |
| `_update_bubble_widths` 调用/`_load` | ~870 次（O(n²)） | 1 次（测试断言 ≤2） | 热路径 |
| 主 agent 复核（同脚本） | — | init_sync=0.168s total=0.323s bubbles=50 | 复核 |

**结论**：① 稳态开销：打开聊天窗从秒级降到百毫秒级；offscreen 排空数字
远低于实机（调查代理实测实机 5–9s，offscreen 无真实绘制），真实收益主要
来自不建 8700 个 QWidget + 不排 ~3480 个布局回调。② 新增路径：「加载更早」
每页 50 条 ~90ms 级，用户主动触发、低频。③ 无新增系统调用/网络/磁盘/线程。
④ 内存：常驻气泡控件数 8700→~500（首屏），单调下降的内存曲线。

## 五、实机运行记录

- **根因现场（实机，用户数据）**：`%APPDATA%/dsh-pet-standalone-webm-chat/`
  - `pet-38452.log`：`tick 间隔 … max=7898.6 / 9018.2 / 7462.9ms`，全部落在
    「最新会话 = 870 条长会话」时段；00:05:20 用户新建短会话后 max 立刻降
    到 31–118ms；
  - `pet-36604.log`（被终止实例）：`max=6021.6 / 12015.5ms`，同一条长会话；
  - WER ReportArchive：`AppHang_dsh-pet-standalo_*`，EventType=AppHangB1、
    本地时间 2026-10-20 23:57:29、「未响应 → 关闭程序」、IsFatal=1——
    「崩溃」定性为卡死被系统终止，无 Python traceback 与残留 ETW 会话均
    为被强杀的结果；
  - 会话文件实测：`sessions/shenshen/773d6cd9….json` 870 条 / 270,531 字节。
- **量级对照**：offscreen 实测 `_load()`（870 条）同步 2.9–3.4s + 排空
  5–9s，与实机 tick max 6–12s 同量级（实机另有 DWM 合成与真实绘制）。
- **用户可见行为**：卡顿三节点为共创者本人实测反馈（2026-10-21 凌晨）；
  修复版的实机确认随 4.4.0 候选构建部署后由共创者验收（合并后执行，见
  §七）。「加载更早」行在 870 条会话上的真机观感同属验收项。
- **边界路径**：新会话 0 条（空态页）、49 条（无加载行）、50 条（无加载行）、
  51 条（加载行「还有 1 条」）均有测试钉住；删除当前会话后落到剩余长会话
  同样只渲染一页。
- **无法自动验证**：实机 DWM 下的真实绘制耗时无法 offscreen 复现（已用
  「控件数 8700→500」这一结构性事实替代量化）；macOS/Linux 端 Qt 行为
  差异由 CI 覆盖。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `ruff check pet/chat pet/sound_winmm.py tests` | All checks passed |
| 聚焦（新测试） | `pytest tests/test_chat_history_paging.py tests/test_winmm_sound.py -q` | **52 passed, 1 skipped** |
| 相关回归 | `pytest tests/ -k "chat or session_store or quick_chat or island_chat"` | **436 passed, 2 skipped** |
| 时序族连跑 | test_chat_history_paging ×3 | 8.1s / 7.7s / 7.3s 全绿 |
| 全量 | `QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q` | **8528 passed, 14 skipped, 0 failed**（1717s） |
| test-first 红绿 | 新增 15 项修改前红（13 聊天 + 2 COM 参数）→ 修改后全绿 | 符合 |

## 七、已知限制与后续

1. `_style()` 整树 setStyleSheet 未单独优化：分页后控件数 ~500，成本随之
   降到百毫秒级以下；如开窗观感仍需压榨，另立任务（样式表分层/按需 polish）。
2. 实机验收（真窗口开 870 条会话、加载更早的观感、tick max 回落）随 4.4.0
   候选构建部署后进行，本报告届时追加一节实机对照。
3. quick_chat 豁免：无历史渲染路径，仅有 prompt 截断（既有）。
4. 全局记忆 / 历史压缩是会话数据层的独立课题（不在本报告范围，见
   `.scratch/feature-modules/` 设计线）。

## 八、风险与回滚

- 影响面：modern/legacy 两个聊天前端的会话渲染与列表管理；winmm 默认设备
  探测的 COM 配平。不动持久化、不动配置键、不动流式协议。
- 回滚：`git revert` 即完全回退；无落盘状态、无配置迁移、无数据残留。
- 分页行为变化是用户可见的（首屏 50 条 + 加载行），属有意的产品行为变更，
  已在 §一声明。
