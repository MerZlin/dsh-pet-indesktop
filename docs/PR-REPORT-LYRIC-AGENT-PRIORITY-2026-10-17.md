# 歌词让位于高优先级气泡（2026-10-17）

基线：`a8593a8`；分支：`feat/lyric-agent-priority`。
范围：3 个产品文件 + 1 个新增测试 + 本报告 + INDEX 一条登记。
状态：**缺陷修复，不新增设置、不改既有气泡门控语义、无依赖/配置迁移。**
关联：`docs/NETWORK-PROXY-AND-VPN-2026-09-22.md`（歌词取词路由，未改动）。

## 一、核心特性

开着「显示歌词」时，Agent 联动的开始/完成气泡几乎永远不出现；关掉歌词后，当前这一轮也不会补发，只有把联动关掉再打开才恢复。根因不是联动写错了状态，而是**歌词把「重要气泡位」长期占满**，而联动气泡按设计要给重要气泡让路——两边都让，结果歌词永远赢。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 歌词不再占位 | 歌词是最低优先级的被动内容，刷新时不再调用 `hold_bubble`，也不让宿主延长占位 |
| 2 | 状态提示可抢占 | 联动/聊天等普通气泡可以临时盖住歌词；提示结束后，歌词由既有让路逻辑自动恢复 |
| 3 | 既有保护不变 | 审批、提醒队列、设置页抑制、隐藏窗、飞行期仍然优先；歌词刷新绝不清除或缩短别的内容已建立的占位 |

**红线 / 不变量**：不改变「谁可以盖谁」的判断条件，只改变「歌词是否算作重要占位」。`show_bubble` 默认行为保持原样（`reserve=True`），其它调用方零感知。

### 根因证据

- 歌词每秒 `_show(force=True)`，每次都 `hold_bubble(3s)`（`pet/music_lyric_controller.py`），宿主再按 `duration + 2s` 占 4s，占位被无限续期。
- 联动气泡统一走 `_show_link_bubble`，开头就对 `_bubble_busy_until` 让路（`pet/agent_link.py`）。
- `_maybe_notify_start` 只在「非忙 → 忙」边沿弹一次，所以关掉歌词后当前忙周期不会补发开始提示——既有设计，不是本次目标，但解释了「关了也没立刻恢复」的观感。

### 影响范围

`_show_link_bubble` 是所有联动 Agent（DSH / Claude Code / Cursor / OpenCode 及自定义 Agent）共用的出口，因此这是**产品级缺陷**，与任何特定接入方无关。

## 二、修改文件说明

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/music_lyric_controller.py` | +3 / −5 | 去掉歌词的 `hold_bubble`；`show_bubble(..., reserve=False)`；更新注释 |
| `pet/overlay_shell.py` | +5 / −2 | overlay 宿主按 `reserve` 决定是否延长占位；默认 `True`，行为不变 |
| `pet/window.py` | +5 / −2 | legacy/capture 宿主同契约 |
| `tests/test_lyric_agent_priority.py` | +86 / −0 | 4 条跨域回归（真 OverlayShell + 真 PetSpeechBubble + 真 MusicLyricController + 真 AgentLinkManager） |
| `docs/PR-REPORT-LYRIC-AGENT-PRIORITY-2026-10-17.md` | 新增 | 本报告 |
| `docs/INDEX.md` | +2 / −0 | 报告入口 |

未改动：`pet/agent_link.py`（让路逻辑原样）、`pet/window_alerts.py`（提醒队列原样）、任何持久设置键。

## 三、实现要点

`show_bubble(..., reserve=False)` 是**内部**关键字参数，只有歌词控制器传；宿主在 `reserve=False` 时跳过 `hold_bubble(duration + 2)`。没有直接删掉宿主的 hold，是因为其它调用方（聊天、识屏、余额、提醒）依赖它避免被自言自语顶掉，语义必须保留。

恢复路径复用既有机制：歌词每秒刷新前先看 `_yield_active()`——气泡里不是自己的内容就让路并续期，直到对方离开。不需要新增计时器或状态机。

## 四、性能分析

方法：真 overlay 测试壳 + 真 `PetSpeechBubble`，连续 500 次 `_show('同一句歌词', force=True)`，`perf_counter` 计时；offscreen，不发 SMTC/网络请求。

| 指标 | 实测 | 归属 |
|---|---|---|
| 500 次刷新 | 平均 **0.395 ms/次** | 既有热路径 |
| 刷新后重要占位 | **0.0**（旧实现为 ≥4s 且持续续期） | 修复目标 |

结论：新增开销只是一个布尔分支与一次跳过；删掉了每拍一次 `hold_bubble` 调用。无新线程、网络、磁盘、缓存或计时器。**未做旧实现同机对照，不声称更快。**

## 五、实机运行记录

- **真 Qt 复现**：修复前用真壳 + 真气泡跑回归，两条用例红——歌词占位 `>0`、状态提示被歌词盖住；修复后转绿。另外两条（聊天占位受保护、审批仍优先）修复前后都通过，说明没有放松既有保护。
- **冻结运行时验证**：用已安装 v4.3.1 的归档做增量热修复（只重编 3 个模块，其余 1853 个 PYZ payload 逐字节不变），独立探针 EXE 在真实 Qt 控件上验证六项行为：歌词不占位、开始覆盖歌词、歌词让位、歌词恢复、完成覆盖歌词、聊天仍受保护，全部通过。
- **用户侧**：本机管理员在真实桌宠里开启歌词后，状态提示恢复正常；同一现场还包含桥接侧的独立修复（PR #298），因此这里不把「恢复」单独归给本提交。
- **无法自动验证**：本机工具运行在非交互 Windows 会话，无法读取桌面窗口截图；视觉观感由用户确认。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 聚焦 | `pytest tests/test_lyric_agent_priority.py -q` | 4 passed |
| 相关族 | `pytest tests/test_lyric_agent_priority.py tests/test_music_lyric.py tests/test_agent_link.py tests/test_overlay_bubble_wiring.py tests/test_flight_bubble_suppression.py -q -k 'not test_window_smooth_chaining and not test_on_anim_ended_continuation'` | **420 passed, 2 deselected** |
| 架构/聊天 | `pytest tests/test_architecture.py tests/test_lyric_agent_priority.py tests/test_chat_shared_behavior.py tests/test_chat_service.py -q` | 46 passed |
| 静态 | `ruff check pet/music_lyric_controller.py pet/overlay_shell.py pet/window.py tests/test_lyric_agent_priority.py` | 通过 |

**两条被排除的用例是仓库既有失败**（`test_window_smooth_chaining`、`test_on_anim_ended_continuation`：动画启动被拒绝、回退待机），与本改动无关，单独复跑同样失败；本 PR 不修改它们，也不声称它们通过。未跑整仓全量；未做 macOS/Linux 实机验证。

## 七、已知限制

- 只调整优先级，不改变「同一句歌词持续期间不重绘」等既有节流；歌词仍每秒刷新。
- 若将来有第三方需要「既占位又不阻断状态提示」的等级，应新增显式优先级枚举，而不是复用 `reserve`。

## 八、风险与回滚

改动集中在两个宿主的 `show_bubble` 普通分支与歌词调用点；默认参数不变，其它调用方行为一致。回滚即 `git revert`，无持久数据或配置残留。
