# issue #111：Windows 关机/注销弹 `0xc0000142` —— 会话结束时的 ffmpeg 派生闸门

- 日期：2026-09-12
- 分支：`fix/issue-111-session-end-ffmpeg`
- 对应 issue：[MerZlin/dsh-pet-indesktop#111](https://github.com/MerZlin/dsh-pet-indesktop/issues/111)
- 版本背景：v4.2.0（WebM Chat 变体）实测复现，Windows 11 家庭版 Build 26200

## 1. 现象

每次关机/注销必弹：

> **ffmpeg-win-x86_64-v7.1.exe - Application Error**
> 应用程序无法正常启动(0xc0000142)。请单击"确定"关闭应用程序。

弹窗阻塞关机流程，必须点一次「确定」或等超时系统才能继续。开机与日常使用完全正常。

## 2. 根因

`0xc0000142` = `STATUS_DLL_INIT_FAILED`：进程创建成功了，但在加载 `user32` /
`gdi32` 等 DLL 时初始化失败。

关机时序：系统先给顶层窗口发 `WM_QUERYENDSESSION`，随后开始拆除本次登录会话
（窗口站、桌面堆、CSRSS 等）。**桌宠此前对这条消息零感知**——收到它之后动画链
照常运转，任何一次动画切换/预热都可能 `CreateProcess` 一个新的 ffmpeg 取帧进程；
该进程在已拆除的会话里 DLL 初始化失败，系统于是弹出上述错误对话框。

issue 里的日志证据正好对上：`webm 圈边界宽限期满未续圈，reader 退出` **每几秒一条**
（reader 启停极其频繁），因此「新起进程撞上关机窗口」的概率几乎为 100%。父进程
命令行也确认了该 ffmpeg 就是桌宠的取帧 reader。

## 3. 四条 ffmpeg spawn 路径（全体清单）

| # | 触发 | 代码位置 | 频率 |
|---|------|----------|------|
| 1 | 播放 reader 拉起解码 | `webm_clip._reader_local` → `imageio_ffmpeg.read_frames` | 每次动画切换 / 圈末回收后 fresh spawn |
| 2 | 首帧预解码（冷首帧预热 / 同步首帧） | `webm_clip._decode_first_qimage` → `read_frames` | 每次预测式预热（`predict_prewarm_lead_ms` 触发）、点击冷动画 |
| 3 | 元数据探测 | `webm_clip._ensure_meta` → `count_frames_and_secs` | 首次量取某素材帧数/时长 |
| 4 | ffmpeg exe 探测 | `webm_clip._ensure_ffmpeg_exe` → `imageio_ffmpeg.get_ffmpeg_exe` | 冷缓存下的 `ffmpeg -version` |

（`pet/animation_thumbnail.py` 只用 `imageio_ffmpeg.read_frames` 生成缩略图，不是
进程 spawn 点，未计入。）

## 4. 修复

### 4.1 进程级闸门（`pet/webm_clip.py`）

`_SESSION_ENDING` + `session_ending()` / `set_session_ending()`。置位后永久生效
（进程正在退出，无需复位），四条 spawn 路径全部拒绝：

- `start()` → 返回 `False`（**复用既有「启动被拒」契约**，窗口层已有完整降级/
  重试语义，见 `tests/test_switch_start_failure_window.py`，调用方零改动）；
- `_reader_local()` / `_decode_first_qimage()` / `_reader()` → 直接返回，绝不进入
  `_PopenCapture` 块；`read_frames` 调用点前再复查一次以收紧并发窗口；
- `_ensure_meta()` → 跳过探测、保留默认值（不告警）；
- `_ensure_ffmpeg_exe()` → 跳过 exe 探测。

`pet/library.py` 的预热链同步收口：`MovieLibrary.stop_all_clips()`（只 `stop()`，
不 join、不登记孤儿——关机时那些收尾无收益且拖长清理窗口）、`warm_predicted()`
门禁、`_warm_all_meta_background` 的 `cancelled` 谓词。

### 4.2 会话结束探测（`pet/session_watcher.py`，新模块）

- **权威信号**：应用级原生事件过滤器捕获 `WM_QUERYENDSESSION` / `WM_ENDSESSION`。
  它必须在**会话拆除之前**送达才有意义——这是唯一能赶在窗口期前生效的时机；
- **次生兜底**：Qt 会话框架信号 `commitDataRequest` / `aboutToQuit`；
- 恒返回 `(False, 0)`：只观测、不拦截，**绝不 veto 关机**；
- 只读 `MSG*` 的 `message` 字段用于比对，仅对关心的两个消息号解引用（非
  `WM_*` 消息的 `lParam` 是任意值，当指针解引用会触发访问违规）；
- POSIX 上不装原生过滤器，闸门与信号接线照旧。

### 4.3 编排与窗口收口

- `AppShell.start()` 安装探测器（强引用在实例属性上，不跨实例共享）；
  `AppShell._on_session_end()` 幂等：置闸门 → 逐窗 `match_shutdown()` → 日志留痕；
- `_on_about_to_quit()` 首行 `_mark_session_ending()`，覆盖「已进入退出流程、
  原生消息未被观测到」的路径；
- `PetWindow.match_shutdown()`（在 `WindowFeatureGateMixin` 中，避免继续撑大
  window.py）：**先** `_pause_activity()`（停 reader/timer/预热）**再**置
  `_closing`（防复活），最后摘下碰撞会话。顺序不可颠倒——`_pause_activity()` 在
  `_closing` 置位后是短路 no-op。

刻意**不**在会话结束时做会话保存/写盘/槽位解锁：关机时那些既非必需（多开文件锁
由操作系统在进程退出时释放）又会拉长清理窗口，与「静默、快速、不再派生任何进程」
的目标相反。

## 5. 验证

| 层次 | 证据 |
|---|---|
| spawn 闸门 | `tests/test_session_end_ffmpeg_guard.py`：置位后 `start()`/reader/预热/元数据/exe 探测全部零 spawn；未置位的对照组照常 spawn（证明门是唯一差异） |
| 静态清单护栏 | 同文件按 tokenize 区分代码/注释，断言 webm_clip 三处 spawn 调用点与 exe 探测都在 `session_ending()` 门内；新增 spawn 路径直接变红 |
| Windows 探测层 | 同文件用真实 `ctypes` `MSG` 缓冲区投递消息：两消息 arm、无关消息不 arm、幂等、回调抛异常不让门失守 |
| 窗口层 | `tests/test_switch_start_failure_window.py`：`match_shutdown` 停播当前 clip、收口 timer、迟到帧不推进动画链；`_resume_activity` 不复活 reader |
| 真机 | 见下（需人手执行） |

真机验收（Windows）：

1. 起桌宠，运行 `python scripts/verify_session_end_shutdown.py`（自动定位桌宠窗口并
   投递 `PostMessageW(hwnd, WM_QUERYENDSESSION, 0, 0)`，与系统关机同号同形）；
2. 脚本断言 `%APPDATA%\dsh-pet-standalone-webm-chat\pet-<pid>.log` 出现
   「收到会话结束通知」与「会话结束：已停止全部 ffmpeg reader」；
3. 脚本同时断言投递后 `Get-CimInstance Win32_Process` 里**不再出现新的**
   `ffmpeg-win-x86_64-v7.1.exe`（issue #111 的核心断言）；
4. 真实注销一次，确认不再弹 `0xc0000142`、不阻塞关机。

## 6. 残余风险

- **已在飞的 Popen**：门禁通过与 `CreateProcess` 之间仍有极窄窗口，已经越过门禁
  的派生无法撤回（这是 `WM_QUERYENDSESSION` 到达时机决定的物理下限）；
- **第三方派生**：本修复只覆盖 ffmpeg 系 spawn。`pet/agent_link.py`、
  `pet/harness_launcher.py`、`pet/instance_launcher.py`、`pet/child_pet_cleanup.py`
  等处也会派生 `node`/`pnpm`/自身，理论上同样可能触发 `0xc0000142`；本轮未纳入
  （issue 现象与父进程证据都指向 ffmpeg），如后续出现同类报告再按同一闸门收编；
- **多实例**：每个实例各自安装探测器、各自持有闸门；系统关机时每个实例都会收到
  `WM_QUERYENDSESSION`，不存在「一个实例救了另一个」的假设。
