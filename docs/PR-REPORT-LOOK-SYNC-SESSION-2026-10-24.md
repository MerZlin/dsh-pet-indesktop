# PR 报告：识屏问答「无聊天窗」兜底同步崩溃与前台元数据误报（issue #366）

> **基线**：`34d810b1`（upstream/main，v4.4.0 的 update.json 提交）
> **分支**：`fix/issue-366-look-sync-crash`　**日期**：2026-10-24
> **范围**：4 个文件（实现 2、测试 2）+ 本报告与 INDEX 登记
> **关联**：[issue #366](https://github.com/MerZlin/dsh-pet-indesktop/issues/366)
> （v4.3.4 实机日志 `AttributeError: 'PetInstance' object has no attribute
> '_identity_config'`，聊天窗从未打开时识屏问答同步必失败）

## 一、核心特性

修复 issue #366 报告的两个问题，都在「看看屏幕/识屏」链路上：

| # | 能力 | 说明 |
|---|---|---|
| 1 | 兜底同步不再崩（P0） | 聊天窗从未打开时，「看看屏幕/主动识屏」的问答**真正写进**当前宠角色的会话（此前必抛 AttributeError 被 `except` 吞成 ERROR 日志，会话一行没写） |
| 2 | 前台元数据不再误导（P1） | 点击/右键桌宠触发识屏时，桌宠自己抢到前台——随截图喂给模型的「前台窗口：dsh-pet…」现在置空，`user_text` 回退为纯 `[看看屏幕]` |

**红线 / 不变量**：会话按角色隔离的语义不变——`character` 必须取**每宠路由视图**
（主宠 = slot0、子宠 = slot-N），且与会话目录（`SessionStore(self.config.dir,
instance_id)`）同一视图；聊天窗已打开路径（`append_look_sync`）零改动。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/app.py` | +7 / −4 | `PetInstance.sync_look_to_chat` 兜底分支（app.py:1245-1253）：取角色从 `self._identity_config().get(...)` 改回 `self.config.get(...)`。根因：`_identity_config` 是 `AppShell` 的方法（app.py:3688），`PetInstance` 没有——v4.3.4（a9c42ac6）把 AppShell 语境的写法搬进了 PetInstance。而 `PetInstance.config` 本来就是每宠路由视图（构造点 app.py:1471 主宠 `SlotRoutedConfig(…,0)`、app.py:4408 子宠 `route_settings_config(…)`），每宠键直接可读，那次改动想防的「读到全局面出厂默认」对 PetInstance 不成立。注释同步改写，写明为什么这里是 `self.config`、不能搬 `_identity_config()` |
| `pet/vision.py` | +8 / −2 | `foreground_app_info()`：前台 pid == 本进程 pid 时返回空串（其余分支不动）。该函数只被三个识屏元数据调用点使用（`window.py:3504`、`overlay_shell.py:3448`、`screen_capture/vision_bridge.py:100`），其它前台消费方（全屏检测、proactive 门控、设置页）走的都是 `foreground_window_info()` 字典版，不受影响。另补顶层 `import os` |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_look_sync_session_fallback.py` | +78（新文件） | ① 聊天窗从未打开（`chat_window=None`）时兜底同步真实落盘（真 PetInstance + 真 SessionStore + 真 slot0 路由视图）；② 角色必须取每宠档案选定的角色（`look-cat`，非出厂默认）——防止回归成「读全局面串进默认角色会话」（a9c42ac6 想修的那族缺陷） |
| `tests/test_proactive.py` | +18 / −0 | `foreground_app_info` 前台 = 本进程 pid 时返回空串（issue #366 次级问题）；紧邻的既有向后兼容用例（pid 6789 的假前台）继续守护正常路径 |

### 未改动（看起来相关但故意没动）

- `pet/proactive/__init__.py`：主动识屏自己的元数据从 `foreground_window_info()`
  字典拼出（白名单门控同源），且由闲置观察者触发、不经过点击，没有「桌宠自己
  抢前台」的伪影；不属本次范围。
- `AppShell._identity_config()` / `_chat_identity_config()`（app.py:3668/3688）：
  进程级身份取源的语义（岛名/托盘勾选/`pet_name`）与其在 AppShell 上的全部调用点
  均正确，不动。
- 两个 look worker（`window.py` / `overlay_shell.py`）与 `vision_bridge.py`：
  `user_text` 的 `if app_info` 回退分支本就把空元数据退成纯 `[看看屏幕]`，
  无需改调用点。

## 三、实现要点

- **为什么是 `self.config` 而不是给 PetInstance 加 `_identity_config` 转发**：
  会话库按 `instance_id` 隔离（`SessionStore(self.config.dir, …)`），角色取源必须
  与会话目录同一视图才自洽。`AppShell._identity_config()` 在 overlay 拓扑下取
  **主 sprite** 的视图、legacy 下取 slot0 视图——对子宠实例（legacy 多窗）会把
  slot0 的角色写进 slot-N 的会话目录，身份与目录不同源。`PetInstance.config` 恰好
  就是「该宠此刻真正在用的那份视图」（两个构造点可证），也是 v4.3.4 之前的原行为。
- **为什么 pid 判定就够（不做进程名第二道）**：桌宠全部窗口同进程（多进程多宠层
  4.4b 已退役），pid 命中即全覆盖；进程名兜底在开发态 `sys.executable` 是
  python.exe，会把用户其它 python 窗口误伤。fps_monitor 的「不测自己」双道判定
  是采集排除策略，不适用这里。

## 四、性能分析

**方法（可复现）**：实机探针（真实进程、真实 Win32、真实磁盘，脚本全文见文末
附录；临时目录隔离，不碰真实配置）　环境：Win11 10.0.22631 x64 / Python 3.11.9 /
PySide6 6.11.2

| 指标 | 实测 | 归属（热路径 / 新增 / 既有） |
|---|---|---|
| `os.getpid()` 单次 | 0.043 µs（n=1,000,000） | 新增（pid 比较） |
| `foreground_app_info()` 真实单调用 | 0.09 ms（n=2000，含 Win32 前台查询全链路） | 既有 + 新增比较 |
| 兜底同步单次（含会话落盘提交） | 7.29 ms | 既有（本次把「必抛异常+ERROR 日志」变成正常写盘） |

**结论**：① 稳态开销无变化——兜底分支少了一次方法跳转（原 `self._identity_config()`
两层路由，且必抛异常走 `logging.exception` 格式化栈回溯，比正常路径更贵）；② 新增
路径绝对成本 = 一次 pid 比较 + `os.getpid()` = 0.043 µs，触发频率 = 每次识屏触发一次
（人尺度，分钟~小时级）；③ 无新系统调用（GetCurrentProcessId 为用户态读 PEB，实测
0.043 µs 佐证）、无网络/磁盘/线程变化；④ 无内存与缓存增长。

## 五、实机运行记录

本机（JanG 的 Win11 x64，Python 3.11.9 / PySide6 6.11.2 venv）真实进程探针，
`PYTHONPATH=<repo> ./.venv/Scripts/python.exe %TEMP%/issue366_probe.py`，真实输出：

```
[修复1] 兜底同步 OK：写入 2 条消息到角色 probe-cat 会话，单次耗时 7.29 ms，无 ERROR 日志
[修复2] 真实前台 pid=22420（本进程 pid=31404，不同→正常返回）
[修复2] foreground_app_info() -> 'ZCode.exe | ZCode'
[修复2] 前台=桌宠自身（pid=本进程）-> ''（不喂误导元数据）
[性能] os.getpid() 单次 0.043 µs（新增比较的成本主体，触发频率=每次识屏一次）
[性能] foreground_app_info() 真实单调用 0.09 ms（含 Win32 前台窗口查询全链路，n=2000）
[性能] ERROR 日志总数：0（期望 0）
PROBE ALL OK
```

- **根因现场复现**：修复前同机跑新增用例，逐字复现 issue #366 的栈：
  `AttributeError: 'PetInstance' object has no attribute '_identity_config'`
  （`pet\app.py` `sync_look_to_chat`），且会话断言失败=一行没写。
- **用户可见行为确认**：探针用**真 PetInstance 方法 + 真 SessionStore 异步写盘 +
  真 slot0 档案**（temp 目录）验证「不打开聊天窗 → 识屏问答出现在该角色会话里、
  日志零 ERROR」；「前台=桌宠自己」分支在真实 Win32 查询结果上改 pid 复验返回空串。
- **边界与失败路径**：`chat_window is None` 前提、自身 pid 分支均有探针断言；
  `enable_chat=False` / 空回复的早退守卫（app.py:1235）未改动，不在本 PR 语义内。
- **无法自动验证的部分**：完整 GUI 端到端（真实点宠 → 视觉 API 真调用 → 气泡 +
  会话回看）需要已配置的视觉 Provider（真实 API 计费）+ 人机交互触发，不能在
  探针里替代。已给的排查证据：改动发生的两个接缝（sync 兜底分支、
  `foreground_app_info`）都被探针以真实组件打过；「触发 → `on_look_synced` →
  `PetInstance.sync_look_to_chat`」的接线由既有用例守护
  （`tests/test_overlay_chat_proactive.py:518`、`tests/test_sprite_menu_parity.py:929`）。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests scripts` | All checks passed |
| 聚焦（先红） | 修复前 `pytest tests/test_look_sync_session_fallback.py tests/test_proactive.py::…skips_pet_own_process` | 3 failed（含逐字复现的 AttributeError） |
| 聚焦（后绿） | 同上 | 12 passed |
| 相关族 | `pytest tests/test_overlay_chat_proactive.py tests/test_sprite_menu_parity.py tests/test_single_process_shared.py tests/test_chat_window_lifecycle.py tests/test_island_chat_identity.py tests/test_chat_subsystem.py` | 231 passed, 1 skipped（20.59 s） |
| 受影响时序族满载 3 遍 | `pytest tests/test_look_sync_session_fallback.py tests/test_chat_window_lifecycle.py` ×3 | 20 passed ×3（2.83 / 2.91 / 8.17 s；族含 SessionStore 异步写盘线程） |
| 全量 | `QT_QPA_PLATFORM=offscreen python -m pytest -q` | **9057 passed, 25 skipped, 15 warnings（1240.49 s）** |
| 全量（最终 HEAD 复跑，含本报告） | `.venv/Scripts/python.exe -m pytest -q`（本机 Windows 真实环境，未设 offscreen） | **9059 passed, 25 skipped, 15 warnings（1212.26 s）**，0 failed；比首跑多 2 条——本报告被 `tests/test_pr_report_discipline.py:119/133` 两条 `parametrize("report", GOVERNED)` 用例覆盖（首跑时报告文件尚未落盘），`--collect-only` 计 9084 = 9059 + 25 |
| 断言有效性 | 修复前红即天然注入缺陷重跑 | 同一批用例修复前 3 failed → 修复后 12 passed |
| 架构红线 | `tests/test_architecture.py`（含于全量） | 通过（全量 0 failed） |

## 七、已知限制与后续

- 元数据修复是**丢弃误导信息**，不是恢复「点击前真正的前台窗口」：点击前前台要
  在菜单打开时提前采样，接线复杂且时机脆弱（issue 报告人的备选方案），本次不做；
  模型仍拿到完整截图本身。
- 子宠（legacy 多窗）的兜底同步按各自 slot 视图写各自会话目录——这是修复刻意保持
  的自洽语义；若未来共享会话规范（§二）要求子宠识屏并入 slot0 会话，需连会话目录
  路由一起改，单独改角色取源会重新制造身份/目录不同源。

## 八、风险与回滚

- 无新增配置键、无迁移、无落盘格式变化（写入的会话就是普通会话文件——这正是修复
  目的）。
- 影响面：仅识屏问答的无窗兜底分支 + 识屏元数据字符串；聊天窗已打开路径与其余
  前台消费方零改动（§二的「未改动」清单）。
- 回滚：`git revert` 单提交即可，无残留状态（已写入的会话文件本就可被旧版读取）。

## 附录：性能/实机探针脚本

即 §四/§五 数据的来源，`PYTHONPATH=<repo> ./.venv/Scripts/python.exe
issue366_probe.py`（工作目录任意，临时目录隔离）：

```python
import logging, os, shutil, sys, tempfile, time
from types import SimpleNamespace

logging.basicConfig(level=logging.ERROR)
ERRSeen = []
class _Capture(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            ERRSeen.append(record.getMessage())
logging.getLogger().addHandler(_Capture())

from pet import pet_profiles
from pet.app import PetInstance
from pet.chat.session_store import SessionStore
from pet.config import Config
from pet.settings_slot_config import route_settings_config

base = tempfile.mkdtemp(prefix="issue366-probe-")
try:
    view = route_settings_config(Config(base=base, instance_id="slot-0"))
    inst = PetInstance(SimpleNamespace(enable_chat=True), view)
    assert inst.chat_window is None
    assert pet_profiles.write_profile_settings(
        view.dir, 0, {"character": "probe-cat"}).status == "ok"
    t0 = time.perf_counter()
    inst.sync_look_to_chat("[看看屏幕]", "探针：屏幕上是代码编辑器。")
    dt_ms = (time.perf_counter() - t0) * 1000
    store = SessionStore(view.dir, view.instance_id)
    sessions = store.list("probe-cat")
    assert sessions
    msgs = [(m.role, m.content) for m in sessions[0].messages]
    assert msgs == [("user", "[看看屏幕]"), ("assistant", "探针：屏幕上是代码编辑器。")]
    print(f"[修复1] 兜底同步 OK：写入 {len(msgs)} 条消息，单次 {dt_ms:.2f} ms，"
          f"ERROR 数 {len(ERRSeen)}")
finally:
    shutil.rmtree(base, ignore_errors=True)

from pet import vision
info = vision.foreground_window_info()
app_info = vision.foreground_app_info()
print(f"[修复2] 真实前台 pid={info.get('pid')}（本进程 pid={os.getpid()}）")
print(f"[修复2] foreground_app_info() -> {app_info!r}")
assert info.get("pid") != os.getpid()
saved = vision.foreground_window_info
vision.foreground_window_info = lambda: {**info, "pid": os.getpid()}
try:
    assert vision.foreground_app_info() == ""
    print("[修复2] 前台=桌宠自身（pid=本进程）-> ''")
finally:
    vision.foreground_window_info = saved

N = 1_000_000
t0 = time.perf_counter()
for _ in range(N):
    os.getpid()
print(f"[性能] os.getpid() 单次 {(time.perf_counter()-t0)/N*1e6:.3f} µs（n={N}）")
N2 = 2000
t0 = time.perf_counter()
for _ in range(N2):
    vision.foreground_app_info()
print(f"[性能] foreground_app_info() 真实单调用 "
      f"{(time.perf_counter()-t0)/N2*1000:.2f} ms（n={N2}）")
assert not ERRSeen
print("PROBE ALL OK")
```
