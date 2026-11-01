# PR 报告：聊天/DSH 稳定性 + 启动/静息 CPU（2026-11-01）

> **基线**：`6440259f`（工作区备份：用户反馈批次 3–4）
> **分支**：`feat/petcore-vpet-architecture`　**日期**：2026-11-01
> **范围**：20 个已跟踪文件（实现 10、测试 4、文档 6）+ 5 个新测试/脚本 + 本报告
> **关联**：用户 v4.4.0 三条反馈（最大化按钮、审批卡弹出时桌宠卡死、启动 9%/静息 2%）

## 一、核心特性

| # | 能力 | 用户可见变化 |
|---|---|---|
| 1 | 标题栏最大化按钮归位 | 按钮从标题左侧的裸方块回到右上角，顺序为「最小化 → 最大化 → 关闭」 |
| 2 | 审批卡不再卡死桌宠 | 旧 `dsh.channel` 扩展从自己的 SSE 线程调 ChatPort 时，窗口创建/气泡/托盘通知一律排队到 GUI 线程；DSH 审批路径不再整段重放历史、不再逐帧写盘 |
| 3 | 启动尖峰与静息 CPU | 首跑素材迁移改为「系统空闲 60s 后才跑」；新增「闲置降帧」（默认开）：30s 无交互后动画 24→12fps，交互立即恢复 |

**红线 / 不变量**

- 端口（ChatPort / PresentationPort）可被第三方 mod 的 worker 线程调用，但**控件只在 GUI 线程碰**；排队调用不假装已显示（`say/show_image` 返回 False，`notify` 返回 True=已受理）。
- DSH 历史/审批的**顺序与去重**不变：审批卡仍按 request id 去重，历史回放后仍补挂待审批；重放期间的实时帧按到达顺序落地。
- 首跑供给的**转换质量与配额不变**（仍每轮最多 `migration_clips` 个旧产物 + 无旧产物的新 clip 按计划），只改变「什么时候起手」。
- 闲置降帧只改**消费节拍**，不改动画素材/时长/播放速率；`playback_speed=2.0` 的 48fps 在降帧后为 24fps，仍是用户设置的全速素材。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/ui/chat/window.py` | +3 / −0 | `maximize_button` 从未 `addWidget` 进 `title_layout`，作为裸子控件停在标题栏 (0,0)（用户截图红框）。插到 `minimize` 与 `close` 之间，顺序变为 `pin → 最小化 → 最大化 → 关闭`。 |
| `petcore/ui/chat/controller.py` | +108 / −16 | ① 新增 `append_received` / `open_received` 信号，`append/open` 判 `QThread.currentThread() == self.thread()`：后台线程只 emit，窗口只在 GUI 线程 `_ensure` 创建（旧 `dsh.channel` 的 SSE 线程首次 append 会后台建整个 ChatWindow → AppHang）。② DSH 会话对象缓存 + 400ms 合并落盘 + 落盘时才 `_refresh_history`；关闭/切会话/切视图/DSH 提交/回退前 `_flush_dsh`；`_on_dsh_approval` 记录耗时，>1s warning。 |
| `petcore/ui/chat/dsh_view.py` | +71 / −12 | `_on_history` 分批（20 条/批，`QTimer.singleShot(0)`），批内不逐条滚底；重放期间的实时/审批帧进 `_deferred_frames`，结束后按序落地；`activate()` 在重放中不再提前补审批卡。 |
| `petcore/ui/app.py` | +96 / −1 | `_PresentationAdapter` 增加 `_on_gui_thread` / `_defer`（`host.scheduler.call_later(0, …)`）；`say / say_sequence / show_image / notify / play_event / play_animation / release_loop / hide` 全部 GUI 线程收口。`run()` 增加一行启动阶段耗时日志。 |
| `petcore/ui/pet_window.py` | +65 / −0 | 闲置降帧门控：记录点击/拖动/滚轮/右键/手动播放/取消隐藏；1Hz 检查「开关开 + 可见 + 未锁屏 + 非拖拽/探头/击飞 + 距上次交互 ≥30s」，命中后 `clip.set_frame_divisor(2)`，交互立即回 1；`_stop_clip`/`shutdown` 复位。 |
| `petcore/media/provision.py` | +69 / −8 | `start_background_provision` 从固定 5s 定时改为线程等待：Windows 上 `GetLastInputInfo` 连续空闲 ≥60s（每 20s 查一次）才跑，非 Windows 保留 5s；日志写等待/开始（已空闲 N 秒）/elapsed；注入 `idle_provider/sleep` 便于测试。 |
| `petcore/media/clip.py` | +11 / −0 | `PetClip.set_frame_divisor`：优先原生 `set_frame_divisor`（帧序列），否则退回 `set_decode_throttle`（WebM）。 |
| `pet/frameseq_clip.py` | +29 / −7 | 新增 `set_frame_divisor(1–4)`：间隔 ×n、`_advance` 每拍前进 n 帧、预取目标帧也按 n 跳（少读少画），动画总时长不变；`start()` 的预取从 1 改为 n。 |
| `petcore/config.py` | +6 / −0 | 新增 per-pet 键 `idle_low_fps_enabled`（默认 True）：DEFAULTS + normalize（老配置缺键取默认）。 |
| `petcore/ui/settings/page_interaction.py` | +6 / −0 | 动画页新增「闲置降帧」行，复用 `_CHECK_KEYS` 的 checkbox；保存/取消/搜索沿用页面既有语义。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_chat_dsh_ui.py` | +28 | 标题栏布局 index + 实际 geometry（min < max < close）。 |
| `tests/test_petcore_chat_ui.py` | +45 | 后台线程 `append` 建窗在 GUI 线程；后台 `open` 延后显示。 |
| `tests/test_petcore_chat_dsh_persist.py`（新） | +144 | DSH 3 帧只落盘 1 次；关闭前 flush；会话对象缓存；60 条历史分批 + 实时帧排在历史后。 |
| `tests/test_petcore_ports_thread.py`（新） | +134 | 后台 `say`/`notify` 排队到 GUI 线程；GUI 线程调用保持同步。 |
| `tests/test_petcore_idle_fps.py`（新） | +148 | 默认开；30s 阈值；随机自播也降帧；拖拽/锁屏/开关关闭不降；点击立即恢复。 |
| `tests/test_petcore_provision_idle.py`（新） | +86 | 空闲序列 [0,0,120] → 睡 2 次后起手；每进程只一次；无角色不启动。 |
| `tests/test_frameseq_clip.py` | +31 | divisor=2 时帧序列 0→2→4、间隔 ×2；运行中改回 1 立即落地。 |
| `tests/test_petcore_settings.py` | +15 / −1 | 闲置降帧开关默认勾选、可写配置、搜索文本命中。 |

### 新增脚本 / 文档

| 文件 | 行数 | 用途 |
|---|---|---|
| `scripts/bench_petcore_idle_cpu.py`（新） | 237 | 启动/静息 CPU 实测：临时数据根、`--set` 覆盖、启动时间线、60s 平均/峰值（进程树，psutil）。 |
| `docs/PETCORE-LOG.md` L152 / `docs/PETCORE-LOG-INDEX.md` | +68 / +1 | 批次记录与索引。 |
| `docs/SETTINGS-CHANGE-GATES.md` | +6 | `idle_low_fps_enabled` 准入行（本宠 / 默认 True / 失败路径 / 搜索别名）。 |
| `docs/PETCORE-SPEC.md` | +1/−1 | 动画设置一句补充（闲置降帧）。 |
| `petcore/AGENTS.md` | +6 | 5 条新红线：端口 GUI 亲和、标题栏顺序、审批路径降载、闲置降帧、供给等空闲。 |
| `docs/INDEX.md` | +1 | 本报告登记。 |

## 三、实现要点

1. **端口线程模型**：`ChatController` 是 QObject（GUI 线程），新增两个信号；`append/open` 的跨线程路径只 emit，槽函数在 GUI 线程做原逻辑。`_PresentationAdapter` 没有 QObject 身份，改用 `host.scheduler`（`QtScheduler` 本身就是信号排队）；`_defer` 同时防止「没有调度器」的测试替身误碰控件（返回 False / True 的语义见红线）。
2. **审批路径降载**：`DshView` 分批重放 + 延后实时帧，避免一次建 200 个控件；`ChatController` 缓存 DSH 会话对象（不再逐帧 `ensure_conversation` 读目录）、400ms 合并落盘（不再逐帧 `store.save` + 重建侧栏），所有「会从磁盘重新读」的入口前强制 flush。审批处理打点，>1s 留 warning 供下次现场定位。
3. **启动 CPU**：用户现场 log（01:48:02 启动 → 01:49:11 `首跑供给完成 converted=2 ... deferred=24`）说明每次启动在迁移 2 个 `random/` 新素材；孤立实测两个 clip 63.5s / 67.2 CPU-s（见 §四）。新逻辑只在系统空闲 60s 后起手，用户活跃时启动不再派生 ffmpeg。
4. **静息 CPU**：`FrameSeqClip` 的 divisor 是「跳帧 + 间隔倍率」，帧 0 预热/链式预取仍成立；`PetWindow` 只对用户无交互的时段降帧，随机自播动作也降（同主分支的口径），拖拽/探头/击飞/锁屏排除。默认开是用户明确要求；设置页可关，取消回滚走页面快照。

## 四、性能分析

**环境**：Windows 11，Python 3.12（`.venv`），16 逻辑核 / 8 物理核，3.8GHz；`playback_speed=2.0`；数据根为临时目录（源码树素材），未装用户 mod（除 A/B 的 legacy 复制）。总 CPU% = 进程树 CPU 秒 / 墙钟 / 16 × 100（任务管理器口径）。

**命令（可复现）**

```bash
# 受控：100% 待机、无随机动作，开/关对比
PET_FRAMESEQ=0 .venv/Scripts/python scripts/bench_petcore_idle_cpu.py \
  --root .scratch/cpu/ctrl-off --mode idle --warmup 40 --seconds 60 \
  --set playback_speed=2.0 --set idle_low_fps_enabled=false \
  --set anim_weight_idle=100 --set anim_weight_act=0 --set anim_weight_turn=0 --set anim_weight_move=0
# 同一条把 idle_low_fps_enabled 改 true、root 换 ctrl-on
# 默认权重（30/10/40/20）：root-off → 1.30%，root-on2 → 0.80%
```

| 指标 | 实测 | 归属 |
|---|---|---|
| 受控待机，降帧关 | 60.2s 平均 **1.736% 总 CPU / 27.771% 单核**，峰值 6.25% | 基线（24fps→2x=48fps 解码+绘制） |
| 受控待机，降帧开 | 60.1s 平均 **0.873% 总 CPU / 13.970% 单核**，峰值 5.16%（**−49.7%**） | 新路径（divisor=2） |
| 默认权重，降帧关 | 60.0s 平均 **1.300% / 20.802% 单核**，峰值 7.81% | 基线 |
| 默认权重，降帧开 | 60.0s 平均 **0.800% / 12.806% 单核**，峰值 5.08%（**−38.5%**） | 新路径 |
| 启动阶段（最小数据根） | qapp 63–68ms / lock 66–70ms / host 295–300ms / character 316–335ms / attach 344–383ms / restore 361–403ms | 启动总耗时 <0.5s |
| 启动阶段（带用户 legacy mod） | qapp 70ms / lock 99ms / host 431ms / character 455ms / attach 800ms / restore 824ms | 仍 <1s，未含供给 |
| 两个待转 clip（安装素材副本） | clip1 34.1s / 35.9 CPU-s；clip2 29.4s / 31.3 CPU-s；合计 **63.5s / 67.2 CPU-s，平均 6.62% 总 CPU，峰值 13.28%** | 旧安装每次启动的尖峰来源 |

**结论**

1. **稳态开销**：静息平均从 1.30–1.74% 降到 0.80–0.87%，即 16 核口径 −38.5%～−49.7%；单核从 20.8–27.8% 降到 12.8–14.0%。用户看到的「2%」量级与实测基线一致。
2. **新增路径成本**：1Hz 门控 = 每秒一次几项属性比较（无可测量 CPU）；`set_frame_divisor` 只在状态切换时调用一次，无每帧开销。启动供给的等待线程每 20s 一次 `GetLastInputInfo`（微秒级），不派生 ffmpeg 时无转换成本。
3. **系统调用/网络/磁盘/线程**：新增 1 条 `petcore-provision` 守护线程（原本就有 `threading.Timer`，改为等待循环）；无新网络；DSH 逐帧写盘改为 400ms 合并，磁盘写入次数下降；无新系统调用（`GetLastInputInfo` 是既有 `platform/foreground` 探针）。
4. **内存**：DSH 会话缓存按会话 id 缓存 1–N 个 `ChatSession` 对象（每会话最多 200 条消息），与原先每帧构造新对象相比只减不增；`_deferred_frames` 仅在重放期间存在，重放后清空。

**审批 A/B（卡死判定）**

| 版本 | 审批到达后 45s 采样 | 结果 |
|---|---|---|
| 旧安装包（E:\dsh-pet-core，01:32 构建）+ 旧 `dsh.channel` mod | 90 次计划，实际第 1 次即 `IsHungAppWindow` / `SendMessageTimeout` 超时 | **HUNG=1**（复现用户现场） |
| 新源码（同 root/同 mod） | 90 次采样全部响应（`hung=0`，进程存活） | **PASS** |

旧包现场日志：`dsh.channel: 等确认 id=e81fb6dc-... tool=pwsh`；新代码同流程 `id=a2a69ed8-...`，审批卡出现且应用可响应。

## 五、实机运行记录

- **审批 A/B**：命令
  `.venv/Scripts/python .scratch/verify_approval_ab.py --mode old --exe E:/dsh-pet-core/dsh-pet-core.exe`
  与 `--mode new`（DSH 桌面端 44.7.0，桥 `dsh-pet-bridge` v2.0.0，`askApproval:true`，端口 6791）。
  真实 DSH prompt（`Remove-Item -LiteralPath E:/tmp/nonexistent-ab.txt -Force`）→ 桥推
  `approval-request`；旧包 `HUNG at sample 1`，新源码 `samples=90 hung=0 alive=True`。
  旧包 root/新 root 均带 `dsh.channel` 副本，`PET_FRAMESEQ=0` 关闭供给，排除 ffmpeg 干扰。
- **标题栏**：960×700 离屏/真窗抓图 `.scratch/titlebar-max-fix.png`；三个按钮实际
  geometry `min=(581,13,32,32) max=(621,13,32,32) close=(661,13,32,32)`，顺序正确；
  回归测试同时断言布局 index 与 geometry。
- **启动供给**：从安装副本实测两个 `random/` clip 63.5s / 67.2 CPU-s；用户旧安装
  01:48:02 → 01:49:11 的日志窗口（69s）与 `converted=2 deferred=24` 对齐。新逻辑
  日志在用户活跃时只出现「首跑供给等待系统空闲…」，不会派生 ffmpeg。
- **静息 CPU**：四组 bench 命令与输出见 §四；采样为真实 GUI 进程树，非 offscreen。
- **DSH 通道自检**：`DshClient.ping` 返回 `ok=True plugin=dsh-pet-bridge version=2.0.0
  bootId=6efce103... pending=0 askApproval=True`；审批 answer 返回 `ok=True`。
- **产物**：`rel-chat-fix-2026-11-01/dsh-pet-core-setup.exe`，**301,146,242 B**，SHA-256
  `8b9535232528badeda98b920b0e95455909dbb0cb6ccc6eea4be95935b54c46a`（未签名）；
  onedir `dsh-pet-core/dsh-pet-core.exe` 15,856,317 B；`check.json`：version 0.3.0 /
  menu_template=json / strict.problems=[] / shenshen 106 动画（frameseq 23 + webm 83）/
  daily 1200 / 配图 24/24；Inno `Successful compile (117.859 sec)`；打包 onedir 真机
  8 秒：进程存活、窗口 `dsh-pet-core`、`IsHungAppWindow=False`。
- **测试**：见 §六（全量 suite 在最终提交前复跑）。
- **无法自动验证**：Windows 任务管理器里的用户观感只能由本次 bench 的总 CPU% 映射
  （16 核口径）；旧包 AppHang 的 WER 报告未等在事件日志落盘（进程在采样后被 kill，
  以 `IsHungAppWindow` + `SendMessageTimeout` 为准）；DSH 审批卡的真实鼠标点击由
  `/pet/answer` 代替（卡片的 `dsh-allow/dsh-deny` 点击路径已有单测）。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `ruff check pet/ tests/ petcore/ integrations/ examples/` | 全绿（见最终答复） |
| 聚焦 | `pytest -q tests/test_petcore_chat_ui.py tests/test_petcore_chat_dsh_ui.py tests/test_petcore_dsh.py tests/test_petcore_chat_dsh_persist.py tests/test_petcore_ports_thread.py tests/test_petcore_idle_fps.py tests/test_petcore_settings.py tests/test_petcore_window_smoke.py tests/test_petcore_provision_idle.py tests/test_frameseq_clip.py` | 185 passed |
| 全量 | `QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_*.py -q` | **2785 passed, 1 skipped in 232.08s** |
| 桥接 | `node --test tests/test_bridge_*.js` | 60 passed（基线） |
| 构建门 | `python -m petcore --check --strict --out dist/check-final.json` | 构建脚本内执行 |

## 七、已知限制与后续

- 闲置降帧默认开（与主分支默认关不同）：如果用户喜欢 48fps 的待机呼吸，可在「互动与物理 → 动画 → 闲置降帧」关掉；阈值固定 30s，未新增阈值键。
- 首跑供给只在系统空闲时起手：如果用户从不离开电脑，旧素材的迁移会一直推迟（不影响播放，只是继续走旧档位）。这是刻意的「不抢用户 CPU」取舍。
- 旧 `dsh.channel` 扩展仍是第三方数据目录里的旧 mod（会开第二条 SSE）；核心已把端口做成线程安全，不自动停用它。建议用户在「扩展管理」中停用。
- 审批 A/B 的旧包版本/旧 mod 是用户现场的组合；新包的 setup 安装后建议再手动触发一次审批确认（见答复的测试步骤）。
- Windows 任务管理器之外的功耗（GPU/电池）未测；本机没有把 CPU 频率固定，bench 数值受后台负载影响 ±0.1%。

## 八、风险与回滚

- **配置**：只新增 `idle_low_fps_enabled`（本宠，默认 True）；老配置缺键取默认，无需迁移。回滚代码后该键不在 DEFAULTS 里，`Config` 载入时按未知顶层键处理（不会阻止启动，也不会被当成新契约）。
- **行为开关**：闲置降帧可在设置里关闭；供给等空闲没有开关（只改时机），回滚代码即恢复固定 5s。
- **回滚**：`git revert` 本报告对应的提交；DSH 事件环/会话 schema 未改，回滚不会留下不兼容数据。旧的 `dsh.channel` mod 文件未被修改。
- **风险**：端口排队让 `append` 的返回从「同步完成」变成「已排队」（第三方若依赖同步写盘可能看到 400ms 延迟）；`notify` 排队返回 True 表示已受理，不代表已弹。已在 AGENTS 红线与 docstring 写明。
