# Harness web 启动认证链接修复报告

> 基线：`34d810b1eadd98d00e7cb9b3b51428a45b5bf03e`（`main`）。
> 分支：`fix/harness-web-token-capture`；日期：2026-10-22。
> 范围：实现 1 个、测试 1 个、文档 2 个，共 4 个文件。
> 关联：PR #82、issue #10；既有服务目标语义见
> [Harness 服务目标报告](PR-REPORT-HARNESS-SERVICE-TARGET-2026-10-06.md)，
> 进程归属规则见 [Harness 归属报告](PR-REPORT-HARNESS-OWNERSHIP-2026-09-29.md)。
> 本文记录当前分支的最终实现与验证结果。

## 一、问题、原因与证据

通过桌宠启动 `dsh web --no-open` 后，旧实现丢弃 stdout，再打开服务裸地址。
没有现存认证 cookie 的浏览器会收到 401，需要用户另行完成鉴权。

真实 dsh `0.2.1-rc.2` 启动后，stdout 包含 `http://127.0.0.1:<port>/?token=…`。
无 cookie 请求 `/` 返回 **401**；请求认证链接返回 **303**，响应包含
`Max-Age=2592000; HttpOnly; SameSite=Strict`；携带该 cookie 请求 `/` 返回 **200**。
这些是本次真实进程的协议观察，token 和 cookie 的值均不进入报告。

端口监听与 token 公告的时机不同，不能在首次探通端口时立刻放弃认证链接。
本次冷启动从调用到系统浏览器接收认证链接耗时见「实机运行记录」；总等待预算
设为 240 秒，首次探通端口后的 token 宽限为 60 秒。

## 二、修改文件说明

增删行数以相对上述基线的 `git diff --numstat` 为准，新文件以新增行数计。

| 文件 | 增删 | 改了什么、为什么 |
|---|---:|---|
| `pet/harness_launcher.py` | +170 / −14 | `_spawn` 保存 stdout 并及时关闭父进程句柄；按完整行、字节偏移增量捕获最后一条 token 公告；新启动和已有实例校验 authority，复用时检查首个 HTTP 状态；缺失或失效时回退裸地址；同步模块与函数契约。解决认证链接被丢弃的问题。 |
| `tests/test_harness_launcher.py` | +243 / −3 | 新增 11 个用例，覆盖半行、重复公告、无日志、authority 不匹配、失效 token、非法 URL、首个响应状态和启动入口；3 个既有用例隔离本机日志，防止真实文件与网络影响结果。 |
| `docs/PR-REPORT-HARNESS-WEB-TOKEN-2026-10-22.md` | +187 / −0（新增） | 记录改动、性能方法、实机协议、菜单点击验收与验证边界，满足交付证据规则。 |
| `docs/INDEX.md` | +3 / −1 | 登记本报告，并在服务目标报告条目中补充后续认证行为的入口。 |

无删除文件。`run.bat`、`app.py`、配置键、依赖和安装流程均沿用基线。

## 三、改动与取舍

1. stdout 写入桌宠数据目录的 `harness-web-stdout.log`，每次 `_spawn` 以 `wb`
   截断。沿用既有数据目录解析，不另存 token 文件。日志创建失败回落 `DEVNULL`，
   服务仍可启动；`Popen` 成功或失败，父侧日志句柄均在 `finally` 关闭。
2. 二进制读取新增字节，以最后一个完整换行推进偏移，避免 Windows 换行转换
   及半条 URL。一次读到多条公告时使用最后一条。读取普通日志当前已有的内容，
   不等待常驻子进程关闭 stdout。
3. 新启动沿用既有 daemon 等待线程和 0.5 秒轮询。捕获同 authority 的认证链接
   即打开；端口已通但 60 秒没有可用链接则打开裸地址；240 秒到期仅在端口仍通时
   打开裸地址。端口始终不通时不打开无效页面。
4. 已有实例只在用户显式要求 web 页面时读取日志。同 authority 的 token 先用
   `http.client` 发一次超时 2 秒的 GET；只判断首个响应 `<400`，不跟随重定向、
   不读取响应体、不保留 cookie。预探失败或 URL 无法解析则回退裸地址。
5. `auto/service` 强制不开浏览器的路径、旧版 dsh 自己开页面的归属、桌面端选择、
   隐藏窗口标志、增强 PATH 与自拉起进程登记和收尾规则沿用基线。

## 四、性能分析

环境：Windows 11 `10.0.26100`、Python `3.12.10`、PySide6 `6.12.0`、8 个逻辑 CPU。
以下测量在真实文件系统上运行；扫描样本预热 200 次，每组计时 3000 次，
使用 `perf_counter_ns`，计时单位为微秒；内存单独使用 `tracemalloc`。

从仓库根目录运行以下 PowerShell 命令即可复测扫描与内存，临时文件在退出时清理：

```powershell
@'
from pathlib import Path
import statistics, tempfile, time, tracemalloc
from pet import harness_launcher as h
original = h._stdout_log_path
with tempfile.TemporaryDirectory(prefix="dsh-token-bench-") as folder:
    empty, full = Path(folder) / "empty.log", Path(folder) / "64k.log"
    empty.write_bytes(b"")
    token = b"dsh web: http://127.0.0.1:38080/?token=benchmark-only\n"
    full.write_bytes(b"x\n" * ((65536 - len(token)) // 2) + token)
    for name, path, offset in (("empty", empty, 0), ("64k", full, 0),
                               ("at_end", full, full.stat().st_size)):
        h._stdout_log_path = lambda path=path: path
        for _ in range(200):
            h._scan_stdout_for_token(offset)
        samples = []
        for _ in range(3000):
            start = time.perf_counter_ns()
            h._scan_stdout_for_token(offset)
            samples.append((time.perf_counter_ns() - start) / 1000)
        samples.sort()
        print(name, path.stat().st_size, statistics.median(samples), samples[2850])
    h._stdout_log_path = lambda: full
    tracemalloc.start()
    for _ in range(3000):
        h._scan_stdout_for_token(0)
    print("retained, peak bytes:", tracemalloc.get_traced_memory())
    tracemalloc.stop()
h._stdout_log_path = original
'@ | .venv\Scripts\python.exe -
```

| 路径 | 样本量 | 中位数 | p95 |
|---|---:|---:|---:|
| 空日志扫描 | 3000 | 291.1 µs | 551.0 µs |
| 约 64 KiB、含完整 token 行，从偏移 0 扫描 | 3000 | 385.2 µs | 727.7 µs |
| 同一日志已消费完、没有新增字节 | 3000 | 300.0 µs | 575.4 µs |

64 KiB 日志连续扫描 3000 次，Python 跟踪的保留内存 **2006 字节**，
峰值 **135242 字节**。这是函数的 Python 分配测量，不代表整个桌宠的 RSS。

复用预探另用如下命令逐端口测 1 次，真实未监听的 1/9 端口分别返回 `False`，
耗时 **2021.0 ms / 2007.1 ms**：

```powershell
.venv\Scripts\python.exe -c "import time; from pet import harness_launcher as h; start=time.perf_counter(); print(h._token_url_alive('http://127.0.0.1:9/?token=benchmark-only'), (time.perf_counter()-start)*1000)"
```

- **稳态**：等待线程完成后，启动器新增轮询为 0 次/秒；没有常驻续期任务。
  子进程仍将 stdout 直接写入文件，写入频率和体积由 dsh 的输出决定。
- **新增路径与频率**：仅等待 web 就绪时每 0.5 秒扫描一次，单次扫描成本见上表；
  每次显式复用最多 1 次本机 HTTP GET。2 秒是 socket 操作超时，不能当作
  整个启动调用的严格总时限；调用仍包含既有端口探测与浏览器启动成本。
- **I/O 与线程**：新增日志创建、写入，以及等待期间的 open/seek/read/close；
  新增按需的回环 HTTP 请求。不增加线程，沿用原等待线程，不读取网络响应体。
- **内存与磁盘增长**：不新增 token/cookie 缓存。内存随一次读到的未消费日志量
  增长，约 64 KiB 样本的分配实测如上；复用从偏移 0 读取日志。日志无轮转，
  可随单个 dsh 实例的持续输出增长，下次 spawn 才截断。不能把它描述为固定内存
  或固定磁盘上限。

## 五、实机运行记录

真实环境使用全局安装的 `dsh`（`@deepseek-ai/dsh 0.2.1-rc.2`）、Node
`22.21.0`、npm `10.9.4`。验收端口为 `38182`，日志置于独立的验收数据目录，
避免覆盖其他运行中实例的 stdout。测试调用真实 `launch_harness`，经过真实
命令探测、`Popen`、文件写入、TCP 和系统浏览器；没有用 HTTP 假服务代替 dsh。

关键命令均在仓库根目录执行，验收宿主保持运行直到等待线程调用浏览器，
避免短命 `python -c` 先退出、daemon 线程来不及完成的假验收：

```python
from pet import harness_launcher as h
h.launch_harness(38182, target="web", open_browser=True)
h.launch_harness(38182, target="service", open_browser=True)
h.launch_harness(38182, target="desktop", open_browser=False)
h.stop_self_launched_harness()
```

| 场景 | 真实输出或观察 |
|---|---|
| 冷启动 | `('started', 'http://127.0.0.1:38182')`；35.110 秒后 `webbrowser.open` 收到同 authority 的 `/?token=…`，真实系统浏览器返回 `True`。 |
| 无 cookie / 认证 / 带 cookie | `401 / 303 / 200`；`Max-Age=2592000`、`HttpOnly=True`、`SameSite=Strict`。Cookie 仅由验收 HTTP 客户端临时持有，用于核验协议。 |
| 再次打开 web | `('already', 'http://127.0.0.1:38182')`；递送同一有效认证链接，自拉起子进程数没有增加。 |
| authority 不同 | `_authenticated_reuse_url('http://127.0.0.1:38183')` 返回原裸地址，不向旧实例递送 token。 |
| 错误 token | `_token_url_alive('http://127.0.0.1:38182/?token=invalid-acceptance-token')` 返回 `False`。 |
| service 复用 | `('already', 'http://127.0.0.1:38182')`；浏览器调用数没有增加，即使传入 `open_browser=True`。 |
| desktop 未安装 | `not-found`，日志明确说明未检测到安装且不回落 web。 |
| 停止与旧 token | `stopped_count=1`、`port_closed=True`；旧 token 复用回退裸地址。 |
| service 再启动 | `('started', 'http://127.0.0.1:38182')`；真实端口开始监听，`browser_calls_added=0`；随后按当前宿主登记安全收尾，`port_closed=True`。 |
| 菜单启动 | 实机点击桌宠菜单「启动 dsh web 界面」，确认服务启动并打开认证页面，菜单入口行为符合预期。 |

用户可见行为验证覆盖系统浏览器真实接受认证链接、真实服务的 cookie 认证闭环和桌宠菜单实机点击启动。
本次未做浏览器页面截图确认、打包产物及 macOS/Linux 真机验收。没有 token 公告的回退另以隔离时钟运行真实等待闭包：60 秒宽限打开裸地址；
把总预算缩短到 2 秒时仍在端口就绪条件下回退；端口始终不通时不打开页面。
这些是确定性边界检查，不是旧版 dsh 的真机验收。

## 六、测试与验证

运行时设置 `$env:QT_QPA_PLATFORM = 'offscreen'`。以下为当前分支的最终复核结果。

| 门 | 命令 / 方法 | 结果 |
|---|---|---|
| 聚焦及相邻回归 | `QT_QPA_PLATFORM=offscreen` 下运行 Harness launcher、lifecycle、ownership 与报告纪律测试 | 349 passed，22.36 秒。 |
| 60 秒边界 | 隔离时钟执行真实等待闭包：无公告、59.5 秒到达 token、缩短总预算、端口始终未就绪 | 60.0 秒回退；59.5 秒 token 被捕获；2 秒总预算时正常回退；端口未就绪时不打开浏览器。 |
| 全量 | `$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; .venv\Scripts\python.exe -m pytest -q` | **9079 passed, 13 skipped, 18 warnings, 0 failed；2485.33 秒（41 分 25 秒）。** |
| 受影响时序族高负载 3 轮 | 8 个忙循环进程期间连续运行 `tests/test_harness_launcher.py` | 每轮 44 passed；13.85 / 18.29 / 224.87 秒；采样 CPU 100.0%，全部负载进程已退出。 |
| 静态检查 | `.venv\Scripts\python.exe -m ruff check --no-cache pet tests scripts` | 通过。 |
| 编译 | `.venv\Scripts\python.exe -m compileall -q pet packaging scripts` | 通过。 |
| 报告纪律 | `.venv\Scripts\python.exe -m pytest -q tests/test_pr_report_discipline.py` | 231 passed。 |
| 补丁检查 | `git diff --check` 与分支 diff 检查 | 通过。 |

报告格式依据 [PR 报告模板](PR-REPORT-TEMPLATE.md) 和
[开发交接 §8](DEV-HANDOVER.md#八开发流程规范)，入口见 [文档索引](INDEX.md)。

## 七、风险与注意

- token 原文会进入本地 stdout 日志，浏览器打开认证 URL。分享日志前应去除 token；
  启动器不保留 cookie，cookie 的生成和过期继续由 dsh 管理。
- 日志读取、解析或复用预探失败会回退裸地址，用户仍可能看到原有鉴权页面。
  旧版没有公告时会增加等待；60/240 秒是兼容性取舍。
- 本次没有增加配置键或第三方依赖。认证 URL 仅接收与选定服务 authority
  相同的公告；HTTP 预探不跟随重定向，外部自行启动且没有匹配日志的实例仍走裸地址。
- 回滚本 PR 提交即可恢复 stdout 丢弃与裸地址打开流程，无配置迁移；已有 stdout
  日志作为本地残留文件保留。浏览器已有 cookie 的生命周期仍由 dsh 决定。
