# PR 报告：主动记忆并发落盘丢写（issue #196 / #204 / #206）

> **基线**：`2786c15`（Merge PR #190，main tip，2026-09-24）
> **分支**：`fix/proactive-memory-locked-atomic-write`　**日期**：2026-10-01
> **范围**：6 个文件（实现 1、测试 2、脚本 1、文档 2）
> **关联**：issue [#196](https://github.com/MerZlin/dsh-pet-indesktop/issues/196)（DB-005 无锁读改写）、
> [#204](https://github.com/MerZlin/dsh-pet-indesktop/issues/204)（DB-006 缺 fsync）、
> [#206](https://github.com/MerZlin/dsh-pet-indesktop/issues/206)（DB-007 docstring 与实现矛盾）

## 一、核心特性

`pet/proactive_memory.py` 原先的 `record()` 是「整文件读 → 插入 → 固定名 tmp → `os.replace`」，
**全程没有锁、没有 fsync、失败静默**。而多开恰恰是本项目的默认路径：`pet/instance_launcher.py`
为每个副窗起独立进程，子槽配置从主配置落种，于是两个进程都会启用识屏、都会写同一份
`<config.dir>/proactive_screen_memory.json`。结果是并发写互相覆盖——本机实测（真三进程，
每进程 25 条）**75 条只剩 3~15 条**，而且字节级看不出异常：文件永远是合法的 JSON，只是内容
被后写者整体替换，日志里一个字的解释都没有。

本次改动把这条路径收口成与仓库既有最严口径一致的一次落盘：

| # | 能力 | 说明 |
|---|---|---|
| 1 | 跨进程锁 | `record()` / `clear()` 的读改写整体进锁（Windows `msvcrt`、POSIX `flock`），锁文件与数据文件同目录、同前缀 |
| 2 | 唯一 tmp 名 | `<name>.json.tmp-<pid>-<tid>`，两个写者不再写串同一个临时文件（口径对齐 `pet/chat/session_store.py`） |
| 3 | 真落盘 | 写后 `flush` + `os.fsync`，替换后父目录 fsync（Windows 无该语义则跳过） |
| 4 | 瞬时冲突重试 | `os.replace` 遇 `PermissionError`（Windows 杀软/索引占用，WinError 5/32）有界退避重试 |
| 5 | 不再静默 | 写失败、目录不可用、损坏回退、锁拿不到——全部记 warning（含路径与异常） |
| 6 | 文档与实现对齐 | 类 docstring 删掉「标题」这一从未落盘的字段（#206，隐私声明的准确性） |

**红线 / 不变量**（本改动不允许被破坏的既有语义）：

- 公开 API 与语义逐字不变：`ProactiveMemory(path, clock=, max_entries=)`、`load()`（时间倒序）、
  `latest()`、`record(process, title, activity)`、`clear()`；`title` 依旧**不落盘**；
  最多 `max_entries` 条、新记录置顶、尾部截断；损坏回退空列表。
- **不新增也不修改任何配置键**（无迁移、无 reload 白名单变更、无 `test_config_schema.py` 快照变更）。
- **不引入 Qt 与 `pet.chat` 依赖**：无 chat 变体（`packaging/pet_entry_no_chat.py` 的
  `excludes=['pet.chat']`）必须继续可用，因此本模块不 import `pet.chat.session_store`。
- 锁拿不到时不丢本次记录（降级为无锁 + warning），与 `pet/proactive_limiter.py`
  「拿不到锁就降级」的既有风格一致，但**降级必须留痕**——这正是 #196 缺失的那一环。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/proactive_memory.py` | +151 / −28 | `_locked()` 跨进程锁；`_temp_path()` 唯一 tmp；`_write_entries()` 落盘（flush+fsync+重试+清理半成品+失败 warning）；`load()` 损坏/结构异常记 warning；`record()`/`clear()` 进锁；类 docstring 删「标题」（#206） |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_proactive_memory_atomic.py` | 新增（10 用例） | 跨进程不丢写（真三进程）、线程读改写串行化（Event 屏障确定性暴露）、四线程并发不丢、tmp 名唯一且带 pid、fsync 被调用、瞬时冲突重试、写失败留痕且旧内容完好且无残留 tmp、损坏回退留痕、类 docstring 不声称记录标题、既有 roundtrip/prune 语义不回归 |
| `tests/helpers/proactive_memory_writer.py` | 新增 | 并发写子进程（真进程边界；带 barrier 文件让三个写者压进同一时间窗，事件式同步而非 sleep 猜时序） |

### 脚本与文档

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `scripts/bench_proactive_memory.py` | 新增 | 修复前后 A/B 基准（内含修复前实现的逐行复刻 + fsync/锁的归因微路径），供后来者用同一条命令复测本报告第四节 |
| `docs/PR-REPORT-PROACTIVE-MEMORY-ATOMIC-2026-10-01.md` | 新增 | 本报告 |
| `docs/INDEX.md` | +1 | 「PR 报告存档」登记（新文档入场规则第 1 条） |

### 未改动（避免评审误以为漏了）

- `pet/proactive.py`：调用点 `ProactiveMemory(memory_path)` / `self.memory.record(...)` 签名未变，故零改动；
- `pet/proactive_limiter.py`：它已有跨进程锁（本次实现与其同族），且频控计数允许偏差、口径不同，未合并两把锁；
- `pet/chat/session_store.py`：它的 `_atomic_write` / `_replace_with_retry` / `_fsync_dir` 是本次对齐的对象，但属 `pet.chat`，被无 chat 变体排除，故**不复用只对齐**（见第三节取舍）；
- `pet/config.py`：在飞 PR #215 正在给主配置加同类重试 helper，未合并前不依赖，避免制造叠放 PR 冲突。

## 三、实现要点

**锁的作用域与降级口径。** 锁覆盖「读 + 改 + 写」整个临界区（不是只锁写），否则两个写者仍会
基于同一份旧快照各自落盘。Windows 用 `msvcrt.locking(LK_NBLCK)` 非阻塞抢锁 + 有界重试
（0.02/0.04/0.08/0.16/0.32/0.64 ≈ 1.26s），POSIX 用 `flock(LOCK_EX)`——与
`pet/proactive_limiter.py::_locked` 同族。**降级口径刻意不同**：频控计数偏差可接受，记忆丢写
不可接受，所以拿不到锁时记 warning 后无锁继续：既保住本次记录，也让「锁没拿到」在日志里可诊断
（实测见第五节：占锁 2s 时 blocked 1.267s、warning 一条、记录不丢）。

**唯一 tmp 名。** 原固定名 `.json.tmp` 的两个后果：两个写者同时 `write_text` 同一个临时文件会
把内容写串；进程被硬杀后残留的固定名 tmp 会被**下一个写者继续复用**。改成
`.json.tmp-<pid>-<tid hex>`（与 `session_store._atomic_write` 同口径）后，残留即使发生也只属于
那一个已死写者。

**落盘序列。** `mkdir(parents=True)` → 写 → `flush` → `os.fsync` → `_replace_with_retry`
（只重试 `PermissionError`，退避 0.05→0.8s，最后一次不捕获、真实失败照常上抛）→ 父目录 fsync
（`os.name == "nt"` 直接返回，Windows 无目录 fsync 语义）→ 失败路径 `finally` 清掉半成品 tmp。
目录 fsync 失败单独记一条「内容已落盘」的 warning，不冒充写失败。

**取舍与备选方案。**
① 复用 `session_store` 的 helper 最省代码，但会破坏无 chat 变体（`excludes=['pet.chat']`），
   故在模块内自带一份并在注释里标注同族来源；
② 把三处原子写（`config` / `slot_manager` / `proactive_memory`）收口成公共工具是更彻底的方案，
   但那属于跨模块重构、且与在飞 PR #215 的 `config.py` helper 撞车，本次不做（见第七节）；
③ 锁文件不复用频控的锁名：两者保护的是不同文件、失败语义也不同，共用一把锁会把
   「识屏频控」与「记忆落盘」的阻塞耦合起来。

## 四、性能分析

**方法（可复现）**：`python scripts/bench_proactive_memory.py 300`
**环境**：Windows（win32）/ Python 3.12.14 / PySide6 6.11.2 / NTFS 临时目录
**样本量**：n=300 次记录；两条路径各自预热 20 条，使文件处于 `max_entries=20` 的稳态大小；
对照物 `_legacy_record()` 是本文件内对**修复前实现**的逐行复刻（固定 `.json.tmp`、无锁、无 fsync、无重试、失败静默），因此不需要回退代码即可复测。

| 路径 | n | mean | p50 | p95 | max | (ms/次) |
|---|---:|---:|---:|---:|---:|---|
| 修复前（无锁 / 固定 tmp / 无 fsync） | 300 | 1.087 | 1.006 | 1.539 | 2.676 | |
| 修复后（锁 + 唯一 tmp + fsync + 重试） | 300 | 3.112 | 3.064 | 3.543 | 5.083 | |
| ├ 归因①：仅 fsync（2KB 文件） | 300 | 1.944 | 1.870 | 2.379 | 5.725 | |
| └ 归因②：仅抢锁 + 放锁 | 300 | 0.155 | 0.134 | 0.275 | 0.691 | |

**净增 +2.025 ms/次（+186%）**，恰好由归因两项解释：fsync 1.944 + 锁 0.155 ≈ 2.10 ms。
（另一次同样 n=300 的运行里 max 出现过 62.667 ms 的 NTFS/杀软抖动尖刺，mean 3.462、p95 3.897 未受影响；该尖刺对应第 4 条重试逻辑要骑过的正是这种瞬时占用。）

**结论（逐条回答）**：

1. **稳态开销变化**：单次落盘 1.09 ms → 3.11 ms（+2.03 ms/次）。但**这条路径的触发频率极低**：
   `proactive_screen` 默认 `enabled=False`；开启后默认 `preset=balanced`
   （`cooldown_minutes=5`、`daily_cap=15`，见 `pet/proactive_limiter.py:25-46`），即每实例
   **每天 ≤15 条**，最激进的 `active` 预设 ≤25 条 → 每天净增 **30~50 ms**；三窗多开约 0.15 s/天。
   相对识屏链路自身的成本（截屏 + dHash + 一次视觉请求）可忽略。
2. **新增路径的绝对成本与触发频率**：成本集中在 fsync（占净增的 96%）；触发频率同第 1 条
   （每条成功识屏回复一次 `record()`）。
3. **新增的系统调用 / 网络 / 磁盘 / 线程**：每条记录 +1 次 `os.fsync`（Windows = `FlushFileBuffers`）、
   +1 次锁文件 `open`/`close`、+1 次 `msvcrt.locking`/`flock` 加解锁；**新增 0 个线程、0 次网络、
   0 个常驻句柄**（锁文件每次进入临界区打开、退出即关；目录 fsync 在 Windows 直接 return，0 syscall）。
4. **内存与缓存增长**：600 次写入 RSS 20.7 → 20.9 MB（+0.19 MB），属分配器抖动量级；
   本模块不持有会增长的缓存（条目上限仍是 `max_entries=20` 条的既有语义）。

## 五、实机运行记录

**1）根因/前提的现场复现（修复前，真三进程并发）**

命令：`python -m pytest -q tests/test_proactive_memory_atomic.py::test_cross_process_record_does_not_lose_entries`（跑在**修复前**的实现上），连续三次运行的真实输出：

```
E  AssertionError: 并发写丢了 60 条：缺失 ['act-w0-0', 'act-w0-1', 'act-w0-10', 'act-w0-12', 'act-w0-13']
E  AssertionError: 并发写丢了 62 条：缺失 ['act-w0-0', 'act-w0-1', 'act-w0-10', 'act-w0-11', 'act-w0-12']
E  AssertionError: 并发写丢了 72 条：缺失 ['act-w0-0', 'act-w0-1', 'act-w0-10', 'act-w0-11', 'act-w0-12']
```

即 3 进程 × 25 条 = **75 条只剩 3~15 条**；文件始终是合法 JSON（这正是它此前不可诊断之处）。
issue #196 里「184/200 丢失」是线程压测数字，本次用**真进程**复现，结论一致且更严

**2）fsync 事实（修复前 vs 修复后，同机同盘）**

探针把 `os.fsync` 包一层计数，各写 5 条记录：

```
{"legacy_fsync_calls_per_5_records": 0, "fixed_fsync_calls_per_5_records": 5}
```

**3）硬杀一致性（各 5 轮，`proc.kill()` 等价 TerminateProcess）**

两种实现下文件始终可解析、条目为稳态 20 条。诚实记录：**两版都可能残留 tmp**
（进程被硬杀不给清理机会），差别在于唯一名残留不会被下一个写者复用；固定名残留会被复用。

```
{"impl": "legacy", "round": 0, "parsed": true, "entries": 20, "leftover_tmp": ["kill-legacy-0.json.tmp"]}
{"impl": "fixed",  "round": 0, "parsed": true, "entries": 20, "leftover_tmp": ["kill-fixed-0.json.tmp-61092-13d8c"]}
{"impl": "fixed",  "round": 2, "parsed": true, "entries": 20, "leftover_tmp": []}
```

**4）失败 / 降级路径的实机观察（边界）**

另一进程把锁持有 2 s 时，本进程 `record()` 的实际行为：**

```
{"warned": ["主动记忆锁被占用，本轮无锁继续: ...\\locked.json.lock"],
 "record_written": ["锁外写"], "blocked_seconds": 1.267}
```

即：阻塞 1.267 s（落在 1.26 s 的有界重试预算内）→ 记一条可诊断的 warning → **本次记录照样落盘**。
这正是「锁不可用时不丢写、但绝不沉默」的既定口径。

**5）无法自动验证的能力与原因（不用沉默代替结论）**

- **真实断电/内核崩溃**（fsync 真正要防的场景）：本机无法制造电源事件 → 用「fsync 调用计数
  0→1/条」+「硬杀后文件始终可解析」两条可复现探针替代，只声称「每次落盘都真的落到内核/磁盘」，
  **不声称**做过断电验证。
- **真实双实例 GUI 端到端**（两个桌宠同时识屏、共用一份记忆文件）：需要两个带识屏白名单的
  GUI 实例并触发视觉请求，本机没有可用的识屏环境 → 用**同一产品代码路径的真三进程探针**
  （`tests/helpers/proactive_memory_writer.py` 直接调 `ProactiveMemory.record`）替代，并注明替代关系。
- **POSIX（flock）分支**：本机是 Windows，该分支只有静态保证 + CI（仓库 CI 覆盖 Linux/macOS）。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests scripts` | `All checks passed!` |
| 聚焦（先红后绿） | `pytest -q tests/test_proactive_memory_atomic.py` | 修复前 **9 failed / 1 passed**（1.16 s）→ 修复后 **10 passed**（2.06 s） |
| 关联回归 | `pytest -q tests/test_proactive.py tests/test_slot_and_memory.py tests/test_pr_report_discipline.py tests/test_architecture.py` | **114 passed** |
| 断言有效性（缺陷注入） | ①删掉 `os.fsync(...)` → 只红 `test_record_writes_through_fsync`（1 failed / 9 passed）；②让 `_locked()` 空转不抢锁 → 只红三个并发用例（3 failed / 7 passed） | 断言能区分新旧实现；注入后文件逐字还原 |
| 全量 | `python -m pytest -q` | 见下方说明 |
| 时序族满载复跑 | 并发用例在同一台机器上多轮复跑（含先红后绿的 9 轮注入/对照组） | 无 flake |

**全量套件的诚实说明（与本次改动无关的一条既有竞态）**

本机（Windows 11 / Python 3.12.14）全量套件 `python -m pytest -q` 的观察：

| # | 配置 | 结果 |
|---|---|---|
| 1 | 本分支（产品改动 + 新测试） | 2966 passed / **1 failed**：`test_session_end_ffmpeg_guard.py::test_control_group_spawns_normally_without_session_end` |
| 2 | 同上，第二次运行 | 同一条再次失败（2966 passed / 1 failed） |
| 3 | 本分支，`--ignore=tests/test_proactive_memory_atomic.py`（只留产品改动） | **2957 passed 全绿** |
| 4 | 克隆环境 + 暂存产品改动（改用 main 的实现跑全套） | **2959 passed 全绿** |
| 5 | 干净 main 工作树（worktree，无本次任何改动） | **2957 passed 全绿** |
| 6 | 在 p 区插入 10 个**空**测试（无视我的测试文件内容） | 复现同一条失败 |

判定依据：该用例 `clip.start()` 内部只做 `thread.start()` 就返回
（`pet/webm_clip.py:1574-1592`），紧接着就断言 reader 线程**已经**调用过 `imageio_ffmpeg.read_frames`
（`tests/test_session_end_ffmpeg_guard.py:184`）——断言本身与线程调度赛跑。实测该用例：
单独跑 **30/30 绿**、与我的文件做短前缀连跑 **20/20 绿**，只有在整套（跑满约 2900 个用例之后）
才会偶发；插入 10 个**空**测试即可复现，说明与本次测试内容无关。
同一次排查中还观察到一次同类失败在 `test_chat_subsystem.py::test_append_message_atomic_across_store_instances`
（同为线程/原子性用例）。**结论：判为既有的时序敏感竞态，建议单独修（把即时断言改成有界等待），
不并入本 PR**；本次会话未能在干净 main 上把它稳定复现（只各跑 1 次），故如实标注而不下断言。


## 七、已知限制与后续

1. **锁拿不到时仍是无锁继续**：保证「不丢本次记录 + 有日志」，不保证强一致。要强一致就得
   阻塞等待，而 `record()` 可能在 GUI 线程被调用（识屏回复路径），故选择有界重试 + 降级留痕。
2. **POSIX 分支本机无法实机验证**：本机是 Windows，`flock(LOCK_EX)` 路径只有静态保证 + CI
   （仓库 CI 覆盖 Linux/macOS）。这一点如实记录，不用沉默代替结论。
3. **硬杀可能残留唯一名 tmp**：`kill -9` 不给清理机会（实测两版都可能残留）。残留无害
   （不会被下一个写者复用），如需彻底清理可在启动路径加一次通配清扫，本次不做。
4. **三处原子写的收口**：`config.py`（PR #215）落地后，可把 `_replace_with_retry` /
   `_fsync_dir` 抽到非 chat 的公共模块，本模块再改为复用。

## 八、风险与回滚

- **影响面**：只影响主动识屏的短期记忆落盘（`proactive_screen` 默认 `enabled=False`，
  即默认关闭；开启后默认 `cooldown_minutes=5`、`daily_cap=15`）。窗口绘制、动画、
   agent 联动、设置页都不经过本模块。
- **配置迁移**：无（不新增/不修改配置键）。
- **回滚**：`git revert` 本提交即可；无残留状态需要清理（唯一副产物是同目录的
  `<name>.json.lock` 空文件，可手动删除，不影响读取）。回滚后退回「无锁 + 固定 tmp + 无 fsync」
  的行为，即 issue #196/#204 复现。
