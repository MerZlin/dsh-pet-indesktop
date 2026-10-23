# PR 报告：唱歌门峰值采样挪到后台探针（修 tick 250ms 长帧）

> **基线**：`34d810b1`（upstream `main`；fork `Milo-Hart/dsh-pet-indesktop` 与上游同 sha）
> **分支**：`fix/peak-probe-off-main-thread`　**日期**：`2026-10-23`
> **范围**：9 个文件（实现 4、测试 3、文档 2）
> **关联**：issue #339《v4.3.4 桌宠 tick 间隔 p99 长期停在 250ms（隐藏窗口仍复现）》

## 一、核心特性

**现象**：开启「音乐自动唱歌」后，桌宠 tick 间隔 `p99` 从 31ms 跳到 **250ms**、`max`
250–266ms；关掉唱歌立刻回到 31ms（issue #339 里已贴三组 5 分钟对照）。窗口用
`ShowWindow(SW_HIDE)` 藏起来仍然复现，所以与渲染 / 合成 / 透明置顶无关。

**根因（本机已复现，非推断）**：`pet/music/detect.py::is_music_playing()` 每秒在
**Qt 主线程**（`_music_sing_timer`，1s 节拍）调一次 `meter.GetPeakValue()`（pycaw
COM / WASAPI）。本机实测发现关键事实：**连续调用只要 0.04–0.18ms，但只要调用间隔
≥0.25s，每次就会阻塞 212–277ms**——设备空闲后每次调用都在等一次设备唤醒
（间隙实验见 §四）。桌宠的 1s 轮询正好落在冷启动区间，于是**每一拍**都是一次
250ms 长帧。这也解释了 issue 里「时好时坏」：唱歌开关只在检测到音乐时激活，
它一开，250ms 就来。

**修复**：把峰值采样改成和 `now_playing` 的 SMTC 会话探针同一套形状——**常驻后台
线程采样，主线程只读缓存**。主线程每拍 300ns，230ms 的 COM 留在后台线程里。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 峰值探针常驻后台线程 | 启动即采一拍，之后 1s 一拍；COM apartment 在线程内 `CoInitialize`/`CoUninitialize`，meter 对象只在该线程使用 |
| 2 | 主线程只读缓存 | `peak_value()` 加锁读一个 float（median 300ns），零 COM；`is_music_playing()` 据此判阈值再叠加会话否决 |
| 3 | 缓存过期 fail-safe | 3s 没有新鲜值按"没有声音"处理 → 唱歌退出；**不拿旧峰值误唱**，探针停摆时最多多唱 3s |
| 4 | 空闲自行退出 | 8s 没人问 → 线程退出并清缓存；唱歌功能关掉后不留常驻轮询 |
| 5 | meter 失败节流 | 建不出来时按 10s 间隔重试（每拍 `Activate` 会累积 COM 句柄，仓库已有同类教训），设备热插拔后仍能自己恢复 |

**红线 / 不变量**（不允许被破坏的既有语义）：

- 判定口径不变：**峰值 > 0.02 且当前会话未被否决**才唱；没有 SMTC 会话时只看峰值（旧行为）；
- **主线程绝不调 `GetPeakValue` / 不查 SMTC**：`is_music_playing()` 在唱歌定时器槽里跑；
- 首拍没有采样结果时返回 `False`（宁可晚 1 秒唱，也不在主线程读 COM）；
- 非 Windows 恒 `False`，且不拉起探针线程；
- `window_alerts.check_music_sing` 的宽限期退出逻辑**一行没动**（既有 7 条"只桩
  `is_music_playing`"的唱歌用例保持绿）。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/music/detect.py` | +199 / −17 | 模块 docstring 补峰值探针口径（为什么 COM 不能在主线程）；新增 `PEAK_*` 常量（间隔 1s / 过期 3s / 空闲退出 8s / meter 重试 10s）与 `peak_value()`（主线程只读缓存）、`refresh_peak_once()`（同步采一拍，探针与测试共用）、`_peak_loop()`（探针线程，启动即采、空闲退出、异常不退出）、`_ensure_peak_probe()`（幂等拉起）、`reset_peak_probe()`（测试清理）；`_get_meter()` 加失败节流并注明"只在探针线程调用"；`is_music_playing()` 改为读缓存 |
| `pet/window.py` | +2 / −1 | 只改定时器旁注释：说明 1s 轮询不再付 COM（主线程读缓存）；`setInterval(1000)` 不变，行数 4568→4569（预算 4671 内） |
| `pet/overlay_shell.py` | +2 / −1 | 同上（sprite 拓扑的注释），`setInterval(1000)` 不变 |
| `pet/window_alerts.py` | +2 / −2 | `check_music_sing` 里"未激活时收口"的注释改准：现在不拉起的是**峰值探针**，不再是"每秒一次 pycaw COM"；逻辑不变 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_music_peak_probe.py`（新增） | +232 | 10 例：**探针线程与生命周期契约**——调用线程零 COM、缓存热读零 COM、空闲自退出、采样异常不打死线程且仍能退出、退出后可重启、缓存过期按没有声音、静音不唱、会话否决压过峰值、非 Windows 短路不起线程、常量有限且次序合理 |
| `tests/test_firefox_media_session.py` | +2 / −0 | 该用例原本直接期待 `is_music_playing()` 同步读假 meter；现在先用 `refresh_peak_once()` 喂一拍（等价探针跑过一轮），断言不变 |
| `tests/test_now_playing_media_type.py` | +8 / −1 | `_loud_system()` 统一喂一拍峰值（所有唱歌门用例走这里）；清理夹具加 `reset_peak_probe()` 防跨用例串味 |

### 未改动（明确说明，避免误以为漏了）

- **定时器频率**：没有把 `setInterval(1000)` 改成 3000。降频只减少卡顿次数、还把唱歌
  响应拖慢；主线程已经零 COM，频率不需要动。
- **`window_alerts.check_music_sing` 的判定与宽限期**：逻辑没动，只改注释。
- **配置 / 设置页**：没有新增键，不涉及 schema、迁移与 UI。
- **`now_playing`**：SMTC 探针原本就是后台线程，本次不碰。

## 三、实现要点

1. **为什么不是"降频"或"整个 check_music_sing 挪线程"**：降频（1s→3s）只是把 250ms
   卡顿从每分钟 60 次减到 20 次，用户仍然感觉得到，而且"识别到音乐后开唱"会慢 2 秒。
   把 `check_music_sing` 整个挪后台要跨线程碰 `_switch`（Qt 对象不能跨线程），还得
   为唱歌状态加信号；而峰值是唯一会阻塞的调用，只挪它一处，改动面最小、语义最稳。
2. **缓存"峰值"而不是缓存"判定"**：与 SMTC 探针同一条理由——判定还取决于设置里的
   浏览器开关与会话否决；缓存事实，开关改动下一拍读取时立刻生效，规则只有一份实现。
3. **COM 线程模型**：pycaw 的 `AudioUtilities.GetSpeakers()` 依赖调用线程已初始化
   COM apartment，所以 `_peak_loop` 里先 `comtypes.CoInitialize()`（退出时
   `CoUninitialize`），`_get_meter()` 建出来的接口只在该线程用。主线程一次都不碰。
4. **首拍为什么返回 `False`**：`peak_value()` 不允许同步兜底（那正是要修的 bug）。
   代价是"打开唱歌"到"第一次开唱"最多多 1 秒；用法上唱歌状态本来就依赖下一次轮询，
   用户侧几乎无感（`start_music_sing_polling` 的 `singleShot(0)` 白跑一拍，无害）。
5. **与既有探针同构**：常量命名（间隔 / 过期 / 空闲退出 / 重试）、空闲线程退出、
   `reset_*_probe()` 测试入口，全部对齐 `now_playing` 的会话探针，评审可以横向对照。

## 四、性能分析

**环境**：Windows 11 25H2（构建 26200）/ Ryzen 7 H 255 / 23.3GB / Python 3.12.14 /
pycaw 20260927 / comtypes 1.4.17 / PySide6 6.12.0 / pytest 9.1.1。本机有真实音频设备。

**方法**：三个独立脚本（均在本机真实 COM 上跑，不 mock）：

1. `bench_frames.py`：模拟 16ms 主循环 20 秒，对照组不调 COM、实验组每秒插一次
   `meter.GetPeakValue()`——复刻桌宠的唱歌轮询节奏；
2. `bench_fixed.py`：把 COM 挪到后台线程，主线程每拍只读缓存，再跑 20 秒；
3. `bench_gap.py` / `bench_ns.py`：量化"调用间隔 → 阻塞"关系与缓存热读成本。

| 指标 | 实测 | 归属 |
|---|---|---|
| 旧路径：`GetPeakValue()`（间隔 1s，n=19） | **p50 227.45ms / max 238.74ms / 19 次全部 >100ms** | 既有热路径（主线程） |
| 旧路径：主循环帧间隔（n=972，20s） | p50 16.48ms / **p99 227.46ms** / max 238.76ms / **19 帧 >100ms** | **用户可见卡顿** |
| 对照：同一循环不调 COM（n=1227，20s） | p50 16.3ms / p99 16.7ms / max 17.3ms / **0 帧 >100ms** | — |
| 新路径：后台线程每次 COM（n=17，20s） | p50 235.57ms / max 237.34ms | **新增**，每 1s 一次，不在 UI 线程 |
| 新路径：主循环帧间隔（n=1220，20s） | p50 16.47ms / **p99 16.62ms** / max 16.89ms / **0 帧 >100ms** | **修复后** |
| `peak_value()` 缓存热读（n=2000） | min 200ns / **median 300ns** / p99 500ns | **新增**，主线程 1s 一拍 |
| `is_music_playing()` 缓存热（n=1000） | min 300ns / **median 300ns** / p99 600ns | 既有热路径（改后） |
| `refresh_peak_once()` 单次（设备热、连续调用，n=20） | p50 57µs / max 112µs | **新增**，后台 |

**根因证据（调用间隔 → 单次阻塞）**：

```
gap=1.0 s   → [231.2, 234.4, 212.3, 225.3, 227.3, 227.5, 230.7, 231.2]  ms
gap=0.5 s   → [231.4, 231.5, 231.5, 229.1, 232.9, 232.4, 230.6, 227.7]  ms
gap=0.25 s  → [259.7, 257.8, 261.5, 259.6, 262.2, 263.5, 262.8, 276.8]  ms
gap=0.1 s   → [0.3, 0.3, 0.2, 0.3, 0.3, 0.2, 0.2, 0.3]                  ms
gap=0.0 s   → [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]                  ms
```

→ **连续 500 次测 `GetPeakValue()` 只有 0.04–0.18ms（设备一直热），完全看不出问题**；
按产品节奏（1s）测，每次都是 210–280ms。这条也写进「已知限制」：不同音频设备/驱动
可能不出现这个唤醒开销，所以**不是所有用户都会掉帧**（issue 里有用户复现、也有人没有）。

**逐项回答**：

- **稳态开销**：主线程从"每秒一次 230ms 阻塞"降到 **300ns/拍**（约 −99.999%）；后台
  多一条 daemon 线程，每 1s 付一次 235ms 的 COM——它阻塞的只是自己。
- **新增路径成本与触发频率**：探针启动即采一拍，之后 1s 一拍；与旧实现是**同一个
  COM 调用**，只是换了线程，调用量没有增加。采样间隔与桌宠既有轮询同拍，不额外提速。
- **新的系统调用 / 网络 / 磁盘 / 线程**：新增 **1 条 daemon 线程**（仅唱歌功能开启
  且被消费时存活，8s 无人问自动退出）与同一套 WASAPI 调用；无网络、无磁盘。
- **内存**：脚本进程 RSS 25.2MB（未单独测线程 USS 增量，诚实登记）。线程栈是保留
  地址空间，实测不随运行时长增长（探针是常驻单线程，不每拍新建）。
- **缓存**：只多一个 float + 一个时间戳，无增长型缓存。

## 五、实机运行记录

**1）根因现场复现（本机真实音频设备，非 mock）**

```
$ .venv/Scripts/python.exe bench_frames.py
GetPeakValue() x500: min=0.040ms median=0.044ms p95=0.070ms p99=0.118ms max=0.175ms
>50ms 次数 = 0                         # ← 连续调用测不出问题
不调 COM    帧数=1227  p50=16.3ms  p99=16.7ms  max=17.3ms  >100ms=0
每秒一次 COM 帧数= 977  p50=16.4ms  p99=227.0ms max=240.5ms  >100ms=19
```

→ 与 issue #339 的 `p99=250ms / max=250–265ms` 同量级、同形态（每拍一次长帧）。

**2）修复后对照（同机、同脚本节奏，20 秒）**

```
=== 旧路径：主线程每秒一次 GetPeakValue ===
GetPeakValue() 本身的耗时    n=  19 p50= 227.45ms p99= 238.74ms max= 238.74ms >100ms=19
主循环帧间隔                 n= 972 p50=  16.48ms p99= 227.46ms max= 238.76ms >100ms=19
=== 新路径：COM 在后台线程（主线程零 COM） ===
后台线程每拍 COM 耗时        n=  17 p50= 235.57ms p99= 237.34ms max= 237.34ms >100ms=17
主循环帧间隔                 n=1220 p50=  16.47ms p99=  16.62ms max=  16.89ms >100ms=0
```

**3）调用线程归属（红 → 绿）**

```
旧实现（HEAD）：caller = MainThread    com_threads = ['MainThread']         VERDICT: RED
新实现：        caller = MainThread    com_threads = ['audio-peak-probe']   VERDICT: GREEN
```

**4）无法自动验证的部分（如实登记）**：**打包版桌宠的体感验收没做**。本机装的是
v4.3.4 的 PyInstaller 产物（`E:\dsh-pet\...`），不能热替换 Python 源码；源码态启动
完整桌宠需要 rapidocr / onnxruntime 等重依赖（本机测试 venv 按需只装了聚焦所需），
所以本次只到"源码模块 + 真实 COM"这一层。合并发版后按 issue #339 的办法复测：
开着唱歌连续采 `tick 间隔` 5 分钟，期望 `p99=31ms`。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests scripts` | **All checks passed!** |
| 聚焦 | `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_music_peak_probe.py tests/test_music_detect.py tests/test_firefox_media_session.py tests/test_now_playing_media_type.py tests/test_now_playing_session_probe.py tests/test_music_sing_grace.py tests/test_music_sing_timer.py tests/test_overlay_music_sing.py tests/test_architecture.py -q` | **123 passed** |
| 架构红线 | `python -m pytest tests/test_architecture.py -q` | 16 passed |
| 断言有效性（消融） | 把 `peak_value()` 临时改回"主线程同步读 COM"后重跑契约文件 | **6 failed, 4 passed**（含契约 1/2），还原后 10 passed |
| 全量 | 未跑 | **未执行**：本机测试 venv 只装了聚焦所需依赖（PySide6-Essentials / pycaw / comtypes / pytest / ruff），快速识屏与打包链路依赖未装；如实登记，合并前由 CI 兜底 |

红→绿证据：契约用例 `test_com_call_happens_on_probe_thread_not_the_caller` 在旧实现上
调用线程自己出现在 COM 记录里（`['MainThread']`），新实现只剩 `['audio-peak-probe']`；
消融版（同步读）让 6 条契约用例变红，证明这些断言能判别退化，而不是"改完总是绿"。

## 七、已知限制与后续

1. **设备/驱动相关**：间隙实验显示 ≥0.25s 的调用间隔才会触发 210–280ms 唤醒等待。
   本机（Ryzen 7 H 255 / 板载音频）稳定复现；不同声卡/驱动可能没有这个开销——这也
   解释了 issue 里为什么有人复现、有人没有。本报告不复称"所有用户都掉帧"。
2. **未深挖到 WASAPI 内部**：只确认"空闲后每次调用等一次设备唤醒"这一层，没有继续
   追到音频引擎的具体状态机（不属于本修复的必要条件）。
3. **首拍延迟 1 秒**：组件边界，接受。
4. **过期窗口 3 秒**：探针若意外停摆，唱歌最多再持续 3 秒才退出（fail-safe 方向是
   "少唱"，不是"误唱"）。
5. **全量测试未在本地执行**（见 §六），合并前依赖 CI。

## 八、风险与回滚

- **影响面**：仅唱歌功能开启时多一条后台线程（每拍一次 COM）；功能关闭后 8 秒内线程
  自行退出并清缓存。设置、配置、存档格式零变更。
- **回滚**：`git revert` 本 PR 即可；没有新增配置键、没有落盘数据、没有迁移，回滚后
  行为与 `34d810b1` 完全一致。
- **并发**：探针线程与主线程只通过一把锁交换"float + 时间戳"，无回调、无 Qt 对象
  跨线程；探针线程是 daemon，进程退出不需要额外收尾。
