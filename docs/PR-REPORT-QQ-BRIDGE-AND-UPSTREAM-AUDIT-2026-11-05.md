# QQ 桥接修复 + 两份外部产物审查（2026-11-05）

> **基线**：`eb0f0234`（feat/petcore-vpet-architecture，0.5.0 发布后 L165）　**日期**：2026-11-05
> **范围**：实现 9 个文件、测试 10 个文件（含 1 个新增）、文档 4 个（MOD-FORMAT / LOG / LOG-INDEX / 本报告）+ INDEX 登记
> **关联**：`桌宠5.0完整实录-脱敏版.md` / `桌宠5.0完整实录-脱敏版.docx`（同一份）、
> `petcore-补丁包-20261104.zip`、`petcore-加固版源码-20261104.zip`
>
> 工作树在本次改动前已带有 L166–L168 的未提交改动；下表只统计**本次新增的 hunk**，
> 不把既有未提交内容算进本报告。

## 一、核心结论

用户给来的两份实录把「QQ 桥接插件」的问题归到三个 Bug，外加两份外部产物。逐项核对后：

| 来源 | 结论 | 证据 |
|---|---|---|
| 实录 Bug 1：token 生成不了 / 重启后丢 | **症状真实，根因与修法都不对**。真正原因不是 Windows 凭据管理器同 TargetName 覆盖，而是 QQ 设置页读 `host.credentials`；`CoreHost` 只暴露 `api_credentials`（`petcore/ui/app.py:420`），设置窗代理 `_SettingsHostProxy.__getattr__` 转发后仍是 `None`。设置页因此永远走「钥匙串不可用」分支，点「生成/保存」什么都不写；运行时 `HostPorts.credentials` 是同一个 `api_credentials`，所以 token 其实由运行时正常生成，只是设置页看不见、复制不了。实录的 `secret_store.py` 用明文文件绕过设置页，属于误诊后引入安全降级，**不采纳**。 | 修前 `tests/test_petcore_qq_settings.py` 四条全红；`QqSettingsPage(host)` 的 `webhook_token.text()` 为空（宿主只给 `api_credentials`）。 |
| 实录 Bug 2：401 / 200 不入库 | **裸 `Authorization` 是真缺口，`?token=` 的路径说法不成立**。`petcore/net/webhook.py` 只认 `X-Webhook-Token`、`Authorization: Bearer`、`?token=`；NapCat 的 HTTP 客户端发裸 `Authorization: <token>`，所以 401。另一方面，代码用 `urlsplit(self.path).path` 比较，query 已被剥离——`?token=` 本来就能入库；新增的真实 loopback 回归测试在修前即通过，证明「路径把 query 算进去」是误判。**不需要 8792 代理**。 | 修前 `test_webhook_accepts_raw_and_bearer_authorization` 红；`test_real_webhook_accepts_the_query_token_and_ingests` 修前已绿；修后裸头与查询串都 200 且 `chat.ingested` 增长。 |
| 实录 Bug 3：气泡不显示 | **不是代码缺陷**。`auto_hide_fullscreen` 命中后 `set_visible(False)`，气泡随宿主窗口隐藏，这是用户设置的既定语义（`fullscreen_hide.py` 的模块注释已写明）。本次只补设置页提示与一条 debug 日志，**不**在全屏程序上层强行弹窗。 | `petcore/ui/fullscreen_hide.py:13`「进入全屏：把所有当前可见的桌宠 set_visible(False)（气泡随之收起）」；`petcore/ui/app.py` 的 `say()` 在窗口不可见时返回 False。 |
| `petcore-补丁包-20261104.zip` | **4 个缺陷修复采纳；护栏不采纳** | 4 个缺陷在当前 HEAD 全部复现（见下）。护栏基线按另一棵树生成：本树 `ui/chat/quick.py`（455 行、`QuickChatBubble` 26 方法）是补丁基线之后新增的超线项，类/文件两条棘轮会直接红；测试卫生基线为 0，而本树有 325 处 `time.sleep`、74 处 `sys.modules[...]`；分层规则把 `top -> petcore.contracts.*` 判成反向依赖，而 contracts 正是公开缝，规则本身不适配本仓库。 | `baseline.json`：`class_budget=25`、`file_budget=400`、`test_hygiene=[]`；本树统计 28 个类 / 58 个文件超线，未登记项正是 `ui/chat/quick.py` 与 `QuickChatBubble`。 |
| `petcore-加固版源码-20261104.zip` | **整体不采纳** | 起点是 0.5.0（`d098a5f`），本树已多 5 个提交；它把 `petcore` 拆成 579 个 py（本树 254 个），`diff -rq` 有 308 条差异（含仅一侧存在的模块），`ui/app.py` 从 1963 行重写成 612 行、`pet_window.py` 等大类全拆；不含 `mods/`、`examples/`，`net/webhook.py` 在鉴权处没有任何改动，**对 QQ 链路零修复**。与 L166–L168 的功能改动正面冲突，收益不抵合并风险。只把它与补丁包重合的 4 个缺陷修复拿来用。 | `git rev-list --count d098a5f..HEAD` = 5；`diff -rq` 308 条差异；两树 `net/webhook.py` 的 `_authorized` 逻辑一致。 |
| 两个实录文件 | 内容一致 | 解包 `.docx` 的 `word/document.xml` 后与 `.md` 逐段比对，同一份文档。 |

**红线 / 不变量**

- 密钥仍然只进系统钥匙串；不新增明文/文件式密钥存储。
- `WebhookPort` 仍然只绑 `127.0.0.1`、空 token 拒绝启动、常量时间比较、日志不记 token 与查询串。
- 全屏自动隐藏的语义不变：用户开启后，全屏程序上方不弹气泡；QQ 消息仍然入库。
- 不改变 `KeyringCredentials` 的 `service/username` 布局，已存 API Key 不需要迁移，回滚到旧版本仍可读。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `examples/petcore-mods/im.qq/host/settings_page.py` | +51 / −14 | `_credentials()` 优先取 `api_credentials`（修真正的 token 断点）；新增 `_store_credential()` 写入后 `persisted()` 回读；`_save_token` / `_rotate_webhook_token` 失败时明确报「未持久化」，不再谎报成功；`_refresh_status` 补钥匙串可用性；补四种鉴权形式与全屏气泡提示。 |
| `examples/petcore-mods/im.qq/host/runtime.py` | +16 / −0 | 生成 token 后做 `persisted()` 校验；未持久化时 `logger.warning`（不打印 token 值），已存在的 token 不重复生成。 |
| `petcore/net/webhook.py` | +9 / −4 | `_authorized()` 接受裸 `Authorization: <token>`（NapCat 形态）与 `?access_token=`；`do_POST`/`do_GET` 路径比较容忍一个尾斜杠。 |
| `petcore/api/credentials.py` | +11 / −0 | 新增只读 `backend_name()`，供设置页/`--check` 显示实际后端；不写任何条目。 |
| `petcore/cli.py` | +11 / −0 | `--check` 报告增加 `credentials: {backend, available}`，打包版用户可一眼看到钥匙串后端是否可用。 |
| `petcore/ui/app.py` | +8 / −1 | `_PresentationAdapter.say()` 在窗口被全屏隐藏/设置窗打开而丢弃气泡时记 debug（含 `owner`），不改行为。 |
| `petcore/storage/roots.py` | +13 / −2 | `parse_pointer()` 的 `int(schema)` 包成 `RootsError`；`cleanup_moved_source()` 在 `rmtree` 前要求旧根是绝对路径且至少两段，拒绝相对路径/盘符根。 |
| `petcore/mods/install.py` | +25 / −4 | 新增 `_replace_with_retry()`，四处 `os.replace`（目标→trash、staging→目标、回滚、普通安装）统一走它；只对 `PermissionError` 退避，POSIX 首次即成功。 |
| `petcore/chat/history_buckets.py` | +10 / −8 | 抽 `_stamp()`，`_session_day()` 与排序 key 共用；坏时间戳按 0 排到分区末尾，不再让侧栏整体崩。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_qq_settings.py` | +112（新增） | 宿主只给 `api_credentials` 时能读到 token；生成能写入并提示重启；`persisted=False` 时明确报「未持久化」；访问令牌能写入共享钥匙串。 |
| `tests/test_petcore_webhook.py` | +70 / −0 | 裸 `Authorization`、`Bearer`、`?token=`、`?access_token=`、尾斜杠、错误 token 401。 |
| `tests/test_petcore_qq_mod.py` | +98 / −2 | 真实 `WebhookServer` + `QqRuntime` 的裸头/查询串端到端入库；已存在 token 复用；未持久化时 warning。 |
| `tests/test_petcore_roots.py` | +32 / −0 | 非数字 schema 抛 `RootsError`；相对旧根/盘符根拒绝删除且保留内容。 |
| `tests/test_petcore_history_buckets.py` | +10 / −0 | 坏时间戳与正常记录同在「更早」分区时不抛、坏记录排末尾。 |
| `tests/test_petcore_install.py` | +46 / −0 | `_replace_with_retry` 前两次占用第三次成功；始终失败时异常外传且次数正确。 |
| `tests/test_petcore_api.py` | +6 / −0 | `backend_name()` 返回后端类名；无 keyring 模块时为空串。 |
| `tests/test_petcore_cli.py` | +2 / −0 | `--check` 含 `credentials.backend/available`。 |
| `tests/test_petcore_installer.py` | +10 / −1 | L168 的 ai.chat 已装升级同步行按 `DirExists + not IsTaskSelected` 例外放行，不改变 opt-in 守卫。 |
| `tests/test_petcore_installer_mods_opt_in.py` | +11 / −0 | 同上：既有目录刷新不算「未绑 Task 的新装写盘」。 |

### 故意未改动

- **未采用**实录的 `mods/im.qq/host/secret_store.py`（明文文件凭据）：与「密钥只进钥匙串」的既有边界冲突；真正断点是设置页拿错宿主属性。
- **未采用** 8792 代理：`?token=` 的路径匹配在当前代码已经正确；代理只增加一段本地转发与一个常驻进程。
- **未采用**补丁包的 `guardrails/`：基线/规则与本树不匹配（见上表）。
- **未采用**加固版源码的任何结构拆分：与本树 L166–L168 冲突，且不含 QQ mod。
- **未改** `KeyringCredentials` 的 service/username 布局：keyring 25.7 的 Windows 后端用 `user@service` 复合 TargetName 处理多条目；真实双条目往返探针证明两条都读得回来，不需要迁移已存 API Key。

## 三、实现要点

1. **token 断点**：`QqSettingsPage` 的 `_credentials()` 改成 `api_credentials` 优先、`credentials` 兜底。设置页与运行时因此落在同一个 `KeyringCredentials` 实例上；`_store_credential()` 在 `set()` 之后调 `persisted()` 回读，避免 `KeyringCredentials.set()` 吞掉后端异常时界面仍显示「已生成」。运行时同样校验，未持久化只记 warning，不打印 token。
2. **入站鉴权**：保留原有三种形式，新增裸 `Authorization` 与 `?access_token=`；先取 `X-Webhook-Token`，再取 `Authorization`（`Bearer` 或裸值），最后取查询串。所有路径都走 `hmac.compare_digest`。路径比较用 `rstrip("/")` 归一，POST/GET 一致。
3. **上游 4 个缺陷**：按补丁包的修法落地，但 `mods/install.py` 的 helper 放在本模块（与 `interop/memory.py` 的私有 `_replace_with_retry` 同形，不跨层 import）；`time` 的 import 按字母序放好；`attempts` 归一为 ≥1，失败路径才 sleep。
4. **全屏气泡**：只加提示与 debug 日志。全屏隐藏是用户设置；在别人的全屏游戏上强行弹窗属于行为变更，不在本次范围。

## 四、性能分析

**方法**：本机 Windows 11 / Python 3.12（项目 `.venv`）/ keyring 25.7.0。
命令：`python` 内联脚本（见下），每组样本量写明；`--check` 用
`python -m petcore --check --root .scratch/qq-bridge-audit/check-root`。

| 指标 | 实测 | 归属 |
|---|---|---|
| 裸 `Authorization` 鉴权解析 | 120.8 ns/call（旧 104.6 ns/call，+16.2 ns；1,000,000 次 `timeit`） | 新增，每请求一次 |
| 真实 loopback webhook（裸头） | 300 请求，median **1.17 ms**，p95 **16.81 ms**（本机回环 + `http.server`） | 既有热路径 |
| `persisted()` 回读 | **0.230 ms/call**（真实 Windows 凭据后端，50 次均值） | 新增，仅用户点「生成/保存」时一次 |
| `_stamp()` 坏时间戳 | **56.2 ms / 100,000 次**（0.56 µs/call） | 新增，每次分区几十条 |
| `_replace_with_retry` 成功路径 | 与一次 `os.replace` 相同（多一次函数调用）；仅失败时 sleep 0.05/0.10/0.15/0.20 s，累计 ≤0.5 s | 新增，仅替换安装 |
| `--check` 的 `credentials` 探针 | 整套 `--check` 0.72 s（含角色扫描/菜单/语音）；凭据探针为一次 `get_keyring()` + 可用性判断 | 新增，仅诊断时一次 |

**结论**

1. **稳态开销**：webhook 每请求多 16 ns（占 1.17 ms 的 0.0014%），可忽略；`_stamp` 每条会话多 0.56 µs，60 条会话约 0.034 ms；`persisted()` 只在用户点按钮时发生，0.23 ms 一次。
2. **新增路径成本与频率**：`persisted()` 每次保存/生成一次 `CredRead`；`--check` 每次进程一次后端探测；`os.replace` 重试只在 Windows 占用失败时触发，成功路径零 sleep。
3. **系统调用/网络/磁盘/线程**：没有新增线程、没有新增网络监听；`persisted()` 新增一次本地凭据读取；重试路径新增最多 4 次 `time.sleep`（仅失败时）；`--check` 新增一次 keyring 探测。
4. **内存**：没有新增缓存或长生命周期结构；`_stamp` 是纯函数；设置页只多一个局部布尔。

## 五、实机运行记录

**环境**：Windows 11，项目 `.venv`，`QT_QPA_PLATFORM=offscreen` 仅用于 Qt 测试；webhook/keyring 探针是真实进程与真实系统后端。

### 5.1 真实 loopback webhook（非 mock）

```text
raw Authorization            -> 200 {"ok": true}
Bearer                       -> 200 {"ok": true}
query token                  -> 200 {"ok": true}
query access_token + slash   -> 200 {"ok": true}
wrong raw                    -> 401 {"ok": false, "error": "bad_token"}
handler calls: 4
```

### 5.2 真实 Windows 凭据后端双条目往返

临时 service `dsh-pet-core-selftest-<pid>`，测完即删：

```text
backend: keyring.backends.Windows.WinVaultKeyring available: True
read token: tok-1
read key  : key-2
persisted token: True
persisted key  : True
after delete: None None
```

结论：同一个 service 下两条不同 username 的凭据都能读回，实录「同 TargetName 只能存一条、会被模型 API Key 覆盖」在当前 keyring 25.7 + 本项目封装下**没有复现**；不需要改存储布局，也不需要明文文件。

### 5.3 `--check`

```text
{"backend": "keyring.backends.Windows.WinVaultKeyring", "available": true}
version 0.5.0
```

### 5.4 测试

- 聚焦：`QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_petcore_qq_settings.py
  tests/test_petcore_webhook.py tests/test_petcore_qq_mod.py
  tests/test_petcore_history_buckets.py tests/test_petcore_install.py
  tests/test_petcore_roots.py tests/test_petcore_api.py tests/test_petcore_cli.py`
  → **126 passed in 21.29 s**（修前新增 13 条红；`?token=` 端到端那条修前即绿）。
- `ruff check petcore tests examples/petcore-mods/im.qq` → **All checks passed**。
- 全量：`QT_QPA_PLATFORM=offscreen python -m pytest -q` **在 73% 处原生崩溃**
  （`EXIT:139`，两次复跑同点），崩点为 legacy `tests/test_quick_chat.py::
  test_switch_character_moves_session_and_prompt_to_new_character`；该用例单独跑
  **9 passed**，与本次新增测试组合 **13 passed**。这是本仓库已登记的旧套件顺序性
  环境问题（`docs/PETCORE-LOG.md` 的 **L137** 原话：全量在 66% 处原生崩溃、崩点
  同一用例、单跑通过，按旧套件顺序性环境问题登记），与本次 petcore/QQ 改动无关。
- 按仓库既有门禁口径跑 petcore 子集：
  `pytest -q tests/test_petcore_*.py tests/test_architecture.py
  tests/test_pr_report_discipline.py tests/test_ci_workflows.py`
  → **3559 passed, 1 skipped in 229.68 s（EXIT 0）**。修前有两条
  `tests/test_petcore_installer*.py` 误报：L168 的 ai.chat「已装升级同步」行
  没有 `Tasks:`，但带 `DirExists + not IsTaskSelected`，只刷新既有目录、不会让
  全新安装多装；测试按这个既有安装例外放行，安装器行为未改。

### 5.5 没验证到的

- 本机没有 NapCat/QQ 客户端，**真实 OneBot 网关 → 8790 的整链没有端到端验证**；loopback 覆盖到 HTTP 协议层（鉴权、路径、body、入库），NapCat 侧的实际 header 形态依据用户实录与 OneBot 常见实现，未在本机抓包。
- 未在真实全屏游戏上验证「QQ 消息到达但气泡被全屏隐藏」的完整链路；该行为由 `fullscreen_hide.py` 的既有测试与模块注释覆盖。

### 5.6 构建产物

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 `
  -Clean -Installer -OutputRoot rel-storage-2026-11-05a `
  -SignThumbprint 217337C099EC8624AC481BFC9F3E1664D0A442E1
```

- `rel-storage-2026-11-05a/dsh-pet-core-setup.exe`：**315,747,992 B**，
  SHA-256 `5d9abf14f25d00823a3cefca2ffec7a418efaee379a598747ff56eabe4b42077`，
  签名 `Valid`（`CN=DSH Pet Core Dev`）。
- 构建内 `--check --strict`：`menu_template=json characters=1 builtin_images=24
  daily_lines=1200 strict=True`，退出 0；Inno 编译成功（117.188 s）。
- 未实机安装；`rel-*/` 已在 `.gitignore`，不入库。
