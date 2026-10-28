# 联网失败不再谎报「网络不可达」 + 音乐暂停语义（2026-10-28 第三批）

> **基线**：`49aca9be`（`docs(petcore): 记录 tool.ai-browser 实测与 mod 接口边界`）
> **分支**：`feat/petcore-vpet-architecture`　**日期**：2026-10-28
> **范围**：本批 12 个文件（实现 6、测试 4、文档 4），连同工作树里前两批（L111–L119）与 L110 遗留的
> `tool.ai-browser` 一组一起提交
> **关联**：[`PETCORE-LOG.md`](PETCORE-LOG.md) L120–L121、
> [`PR-REPORT-AI-WEBSEARCH-GIF-API-DEFAULTS-2026-10-28.md`](PR-REPORT-AI-WEBSEARCH-GIF-API-DEFAULTS-2026-10-28.md)、
> [`PR-REPORT-WINDOW-LAYERS-FULLSCREEN-MUSIC-RENAME-2026-10-28.md`](PR-REPORT-WINDOW-LAYERS-FULLSCREEN-MUSIC-RENAME-2026-10-28.md)

## 一、核心特性

用户报两个问题，一个「刚启动才坏」、一个「暂停出两种错」：

| # | 用户看到的现象 | 真正的原因 | 修完的变化 |
|---|---|---|---|
| 1 | 刚打开桌宠时「AI 对话」「看看屏幕」都报**网络不可达**；去设置点一次「测试连接」再关掉就正常 | 失败原因**被说谎式兜底**：`ai.chat` 把任何没有 `.reason` 的异常都写成 `network`；同时失败路径一条日志都不留，所以「是不是网络」无从判断。真机探针排除了路由污染（启动窗口内没有任何 `urlopen`，`force_direct` 全程 False） | 未知异常显示「内部错误（不是网络问题，详情见日志）」并留一条可定位的日志；服务/Key 没配会在发请求**之前**就说清；非 API 主机的请求不再能改写进程级路由结论 |
| 2 | 暂停后冒「歌词获取失败」，并且唱歌动画还播一小会 | ① 明确暂停也走 5 秒宽限才停唱；② 暂停期间 SMTC 掉线 → 窗口标题兜底换了曲目身份 → `_begin_track()` 清空歌词与取词状态，恢复后重新联网取词、失败就冒泡还白吃一次配额 | 暂停**下一拍就停唱**；暂停不取词、不清歌词、不报错、不消耗配额；降级身份按同一首处理；宽限只留给 `absent` |

**红线 / 不变量**

- 稳定分类 `auth/rate_limit/network/timeout/tls/protocol` 一字未改；`internal` 只在拿不到 `.reason` 时出现。
- 日志只打异常**类型名**与 service id，不打 API Key、URL、响应体、异常文本。
- 只有 API 主机（`api/chat.py`、`api/probe.py`、`account/service.py`）可以默认 `pollute=True`；其它域一律 `pollute=False`，由源码守卫兜住。
- 音乐侧：`absent` 仍按 `music_sing_grace_seconds` + `ABSENT_CONFIRM_TICKS` 释放（切歌抖动不闪断）；播放中取词失败仍「只报一次 + 60s 冷却 + 每曲 ≤3 次」；上一批的 2^n 补播与展示会话不受影响。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/net/errors.py` | +9 / −1 | 新增 `INTERNAL="internal"` 与文案「内部错误（不是网络问题，详情见日志）」；docstring 说明它不是链路问题 |
| `examples/petcore-mods/ai.chat/host/runtime.py` | ≈ +119 / −41 | 四类失败统一 `_failure_reason()`（无 `.reason` → `internal`，只记一条 `type/reason/service/cause` 日志）；`AiChatFailure` 包住截图问答与动作回退；新增 `_require_service()`（无服务直接报，不发请求） |
| `examples/petcore-mods/media.music/host/runtime.py` | ≈ +67 / −16 | `_silence_limit(paused)=0`；暂停分支不 `_begin_track`、不动歌词/取词；`_track_aliases` 认降级身份；取词失败只在播放中冒泡、暂停窗内退配额 |
| `examples/petcore-mods/media.music/host/settings_page.py` | +1 / −1 | 宽限 hint 改成「暂停会立即停唱，宽限只给切歌抖动/播放器被关掉」 |
| `petcore/net/market.py` | +2 / −2 | 市场索引与下载改用 `pollute=False`（第三方 CDN 的成败不代表整个进程该固定直连） |
| `petcore/update/service.py` | +2 / −1 | 更新检查同上 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_ai_startup.py`（新建 451 行） | 新文件 | 15 例：四类失败回 `internal`、已知分类不变、无服务不发请求、包装异常记 `cause`、`metadata()` KeyError 的真实路径、keyring 降级不抛 |
| `tests/test_petcore_music_mod.py` | +196 / −4 | 暂停下一拍释放、暂停保留歌词、降级身份不换歌、真换歌恢复后接管、`absent` 仍按宽限、暂停窗内失败静默且不占配额 |
| `tests/test_petcore_http_safety.py` | +45 / −0 | 源码守卫：除三个 API 主机外，`urlopen` 必须显式 `pollute=False` |

### 未改动（避免评审误判）

- `petcore/ui/chat/window.py`：错误气泡文案与状态栏不动，只换「进来的 reason」。
- `petcore/api/probe.py`：测试连接本身不改；它走的是同一台 API 主机，允许默认 `pollute`。
- 播放中（非暂停）身份降级仍按换歌处理——刻意保留，上一批的换歌/重新武装用例依赖它。

## 三、实现要点

1. **分类诚实的落点在自己这侧**：`NetworkError.reason` 是唯一契约；`ai.chat` 是唯一把它翻译成用户文案的地方，所以修在那，而不是在 UI 里猜。
2. **发请求前的闸**：`_require_service()` 让「没有 API 服务」不再以一次假链路失败的形式出现（`api.calls == 0` 有用例钉住）。
3. **路由语义**：`urlopen(pollute=…)` 的默认值仍是 True（API 主机需要它学到「这台 API 要不要代理」），其它域显式关掉；源码守卫防止新调用点悄悄把结论钉死。
4. **暂停 vs 消失**：暂停是用户明确表达，一秒都不该等；`absent` 是「看不到」，必须留宽限与确认拍，否则切歌瞬间会闪断重连。
5. **降级身份的识别**：`Track.key()` 由 (标题, 艺人) 组成，SMTC 掉线后艺人可能为空。暂停期间把「同名但 key 不同」的身份收进 `_track_aliases`，恢复后逐拍仍认作同一首；只有播放中身份变化才算换歌。

## 四、性能分析

**方法（可复现）**：`.scratch/music-pause-fix/bench_pause_path.py`（每项 20 万次 × 5 轮取中位）
环境：Windows 11 / Python 3.12.10 / PySide6 6.12.0；采样线程在 1 Hz 之外不额外占用。

| 指标 | 改前 | 改后 | 归属 |
|---|---|---|---|
| `pause_tick_ns`（本地暂停拍逻辑） | 525.9 ns | 205.2 ns | 既有路径（更快：暂停先 return，不读配置） |
| `degraded_pause_tick_ns`（降级身份那拍） | 256.6 ns | 177.9 ns | 新增别名集合查询，净减少 |
| `silence_limit_ns` | 368.2 ns | 47.7 ns | 既有（paused 早返回） |
| 真实采样一拍（`sampling.collect`） | — | 中位 8.4–12.4 ms，max 155–177 ms | 既有，未改 |

**联网侧**：`_failure_reason()` 是异常对象上的一次属性读 + 可能的一次 `logger.warning`（每秒最多几次，
且只在失败时）；`_require_service()` 在已有服务时是一次 `list(api.services())`（原来 `_service_id()`
已经付过这个成本）。市场/更新加 `pollute=False` 只多一个关键字参数，**不改变尝试次数**（仍最多两次）。

**结论（逐条）**：① 稳态开销没有变化，音乐侧还少了约 320 ns/拍；② 新增路径成本是亚微秒级、触发频率
是「每拍一次」与「失败时一次日志」；③ **没有**新的系统调用/网络/磁盘/线程；④ 内存只多一个
`set[str]` 与 `_track_aliases`（换歌/忘记曲目即清空）。

## 五、实机运行记录

本机真实环境（Windows 11 / Python 3.12.10 / PySide6 6.12.0），不是 CI。

### 5.1 联网首启（真机探针，`.scratch/probe_ai_startup.py` / `.scratch/probe_startup_urlopen.py`）

```
force_direct_at_start : False        system_proxies: {}
credentials_first_read: keyring_available=True(141ms)  api1 present=True len=35 (0ms)
service_lookup        : metadata ok:api1 ;  metadata("no-such-service") → KeyError, .reason=None
ssl_context           : worker 线程 ok=True verify_mode=2（0–47ms）
chat_first            : ok=True 844ms end_reason=done incomplete=False force_direct=False
probe(测试连接)        : ok HTTP 200 219ms
chat_after_probe      : ok=True 1328ms end_reason=done force_direct=False
urlopen_calls         : 3 次，全部 force_direct_after=False
30 秒真实启动（11 个扩展）：urlopen_calls: []   force_direct: false
```

结论：**本机复现不了**「首启才坏」；四条排查（`_force_direct`、worker 线程 SSL、首启 Key、服务解析）
都没有异常，且启动窗口内没有任何请求会碰 `_force_direct`。用户日志里也没有出现过
「网络请求改走直连…」这一行。

### 5.2 音乐暂停（真实播放器处于暂停）

`scripts/music_probe.py --seconds 4`：

```
判定：暂停 · 来源=系统媒体会话 · 进程=cloudmusic.exe · 曲目=《开不了口》—周杰伦 · 置信度=有音频证据
```

`.scratch/music-pause-fix/real_pause_probe.py`（真实 `sampling.collect` + `recognizer.decide` 喂进
`MusicRuntime`，只有「上一拍在播」的判定与取词是替身）：13 项断言，改前 6/13 或 7/13，
改后 **13/13**；期间「暂停一拍 `released`」0 → 1、`track_key` 由 `('开不了口','')` 回到
`('开不了口','周杰伦')`、恢复后取词次数 2 → 1。

### 5.3 门禁

```
$ .venv/Scripts/ruff check pet/ tests/ petcore/ examples/petcore-mods/
All checks passed!
$ .venv/Scripts/python -m petcore --check --strict
diagnostics: []  strict: {'enabled': True, 'problems': []}
$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest -q
（见 §5.4 最终一轮）
```

### 5.4 最终门禁与产物

```
$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest -q
10840 passed, 19 skipped, 18 warnings in 1613.41s (0:26:53)
$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_docs.py tests/test_pr_report_discipline.py -q
325 passed
```

未签名安装包（`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 -Clean
-Installer -OutputRoot dist-unsigned-2026-10-28b`，不传 `-SignThumbprint`）：

| 项 | 值 |
|---|---|
| 产物 | `dist-unsigned-2026-10-28b/dsh-pet-core-setup.exe`（磁盘 289 MB） |
| sha256 | `9b137357d49a130dac3cc5703c87ee845b854c4a8e0919ac52c4167026df3f40` |
| 签名 | `NotSigned` |
| 打包体冒烟 | `--check --strict` 退出码 0，`diagnostics: []`、`strict.problems: []`、1 个角色、`menu_template=json` |

## 六、未验证项与风险

- **任务 1 的端到端仍未在用户机器复现**：本机无系统代理、首启即成功。更新后若再出问题，日志会有
  `ai call failed: type=… reason=… service=… cause=…`：`cause=URLError/ConnectionRefusedError` 表示
  「代理/加速器还没就绪」，`cause=-` 且 `reason=internal` 表示服务或 Key 缺失。**没有**给 chat 加
  通用重试（那会掩盖真实故障）；若确认是加速器晚启动，正确做法是给启动窗口一次有界重试或延后首请求。
- **任务 2 没有真实「播放中」采样**：本机播放器此刻是暂停状态；「播放 → 暂停」边沿用合成判定 +
  真实曲目身份模拟。播放中身份降级（非暂停）仍按换歌处理（刻意保留）。
- 市场/更新加 `pollute=False` 后，它们**不再**能纠正进程级路由结论；若某台机器只能通过代理访问
  jsdelivr 而又没有其它请求学到这一点，更新检查会在每次进程内各试一次代理→直连（与改前一致，
  改前也只是「学过一次」）。
