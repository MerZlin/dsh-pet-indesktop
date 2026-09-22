# 代理 / VPN 对桌宠联网功能的影响（2026-09-22）

> **基线**：`0983706`（Merge PR #180）＋ `fix/music-lyric-system-proxy`（PR #181）
> **实测环境**：Windows，系统代理 `http://127.0.0.1:12450`（全局模式 VPN 客户端进程 `core`），
> CPython 3.11.1，全部数字为 2026-09-22 本机单次实测（每项 1 次，含冷连接）
> **缘由**：用户反馈「合并后识别不到网易云的歌词、快进进度也不行了」，真因是**系统代理把歌词取词拖到超时**
> （完整证据链见 [`PR-REPORT-MUSIC-LYRIC-SYSTEM-PROXY-2026-09-22.md`](PR-REPORT-MUSIC-LYRIC-SYSTEM-PROXY-2026-09-22.md)），
> 与相关机制见 [`PR-REPORT-music-lyric-align-2026-09-22.md`](PR-REPORT-music-lyric-align-2026-09-22.md)。

## 一句话结论

桌宠的联网功能**都吃 Windows 的"系统代理"设置**（`urllib.request` 会自动读注册表 / 环境变量，
`getproxies()` 就是它看到的代理）。开着 VPN 时**不能一概而论**：有的功能会**变慢甚至彻底失效**
（歌词取词实测 20~41 秒 → 超时），有的反而**只有代理才能用**（更新清单走 jsdelivr）。
所以正确姿势是**分流 / 规则模式**，并且**保留 localhost 绕过**——不是"全局开"也不是"全局关"。

## 一、受影响功能清单（逐条实测）

| 功能 | 出口代码 | 端点 | 开着系统代理时的表现 | 结论 / 应对 |
|---|---|---|---|---|
| **歌词取词** | `pet/music_lyric.py` | `c.y.qq.com`、`lrclib.net`、`music.163.com` | 三源单次 **41.28 / 22.14 / 20.39 秒**，全部超过 `HTTP_TIMEOUT`（8s）→ 每首未缓存曲目都是 `0行, 耗时 9.00s`；直连 **0.58 / 0.83 / 0.25 秒** | **代码已改为一律直连**（PR #181，`ProxyHandler({})`），用户无需再设置 |
| **TTS 语音合成**（语音报时 / 点击台词朗读 / 节日语音，edge-tts） | `pet/voice_chime_service.py`（`edge_tts.list_voices` 与 `Communicate`）、`pet/self_talk_voice.py` | `speech.platform.bing.com`（Microsoft） | 音色列表端点**直连 2.08s 成功**；走代理 2.26s（也成功）——两者差异不大 | 两边都可用。**不要**为修歌词把它一起绕掉：海外用户/直连不稳的网络需要它走代理 |
| **本机 CosyVoice 语音预缓存**（可选，需自建服务） | `pet/self_talk_voice.py`（`DEFAULT_SERVER = "http://127.0.0.1:9880"`） | **本地** `127.0.0.1:9880` | 若代理把 localhost 也吃了 → `/health` 连不上、预缓存整批失败 | 代理设置里**必须保留** `localhost;127.*` 绕过（Windows 默认的 `ProxyOverride` 就含它，别删） |
| **DSH / Harness 联动** | `pet/dsh_responder.py`（`http://127.0.0.1:<port>`） | **本地** | 同上：localhost 被代理 → 联动请求发不出去 | 同上 |
| **更新检查** | `pet/updater.py` | `api.github.com`、`cdn/fastly/gcore.jsdelivr.net`、`pan.quark.cn` | **jsdelivr 直连失败**（`WinError 10054 远程主机强迫关闭了一个现有的连接`），**走代理 1.45s 成功**；GitHub API 直连 0.69s / 走代理 1.41s 都可用 | 这一类**需要代理**（或把 jsdelivr 域名在分流里指向代理） |
| **余额 / 峰谷提示** | `pet/balance.py` | 用户自配 `base_url`（常见 `api.deepseek.com`） | 取决于端点在国内还是海外 | 按端点分流；国内端点直连即可 |
| **主动识屏（视觉）** | `pet/vision.py` | 用户自配视觉端点（超时下限 60s） | 慢代理下会一直等到超时（用户观感：点了没反应） | 按端点分流；排查时看日志里的请求耗时 |
| **AI 对话** | `pet/chat/providers.py` | 用户自配对话端点 | 同上（海外端点通常需要代理，国内端点直连更快） | 按端点分流 |
| **点击音效** | `pet/click_sound.py` | **不联网**：本地素材 + 本地转码缓存 `sounds_cache/` | 无影响 | 与代理无关。（先前报告里误写成"音效下载"，此处更正） |

## 二、30 秒判断"是不是代理干的"

```bash
# 1. 当前系统代理（ProxyEnable=0x0 或 getproxies 返回 {} 就是没在用）
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" | findstr /i "proxy"
python -c "import urllib.request; print(urllib.request.getproxies())"

# 2. 关掉系统代理，重做同一个操作
#    ——立刻恢复基本就实锤（2026-09-22 实测：关掉后歌词马上回来）

# 3. 想量化某个端点该不该走代理：直连 / 走代理各打一次同样的 GET
python - <<'PY'
import time, urllib.request
def probe(url, proxy):
    op = urllib.request.build_opener(urllib.request.ProxyHandler(
        {} if proxy is None else {"https": proxy}))
    req = urllib.request.Request(url, headers={"User-Agent": "probe"})
    t0 = time.monotonic()
    try:
        with op.open(req, timeout=8) as r:
            return "%.2fs (%d bytes)" % (time.monotonic() - t0, len(r.read()))
    except Exception as e:
        return "%.2fs %s: %s" % (time.monotonic() - t0, type(e).__name__, str(e)[:70])
for url in ["https://c.y.qq.com/soso/fcgi-bin/client_search_cp?w=test&format=json",
            "https://cdn.jsdelivr.net/gh/MerZlin/dsh-pet-indesktop@main/update.json"]:
    print(url[:60], "| 直连:", probe(url, None), "| 走代理:", probe(url, "http://127.0.0.1:12450"))
PY
```

日志关键字（`%APPDATA%\dsh-pet-standalone\pet-<pid>.log`）：

| 想定位的功能 | 日志信号 |
|---|---|
| 歌词取词 | `歌词取词完成: <歌手> - <歌名> -> 0行, 耗时 9.00s`（三源全超时）；PR #181 起还有 `歌词请求失败 <主机>（x.xxs）: TimeoutError/URLError: ...` |
| 歌词是否绕了代理 | PR #181 起每进程一行 `歌词请求直连：已绕过系统代理 http://127.0.0.1:12450`（没这行 = 当前没配系统代理） |
| TTS 音色 | 音色下架/回退相关文案（`voice_chime`），以及合成耗时 |
| 更新检查 | 更新清单请求失败的 URL（jsdelivr 三个镜像依次尝试） |

## 三、推荐配置（按推荐度）

1. **分流 / 规则模式（首选）**：VPN 客户端按域名决定直连还是走代理（国内直连、海外走代理），
   并保留 `localhost;127.*` 绕过。这样每类功能都走它该走的路。
2. **全局模式 + 绕过名单**：必须直连的加进绕过：
   `*.qq.com`、`music.163.com`、`lrclib.net`（歌词已由代码直连，这里是双保险）、
   `*.deepseek.com`（若余额/对话端点是它）、`localhost`、`127.*`。
   **不要**把 `speech.platform.bing.com`、`*.jsdelivr.net`、`api.github.com` 加进绕过名单——
   它们多数情况反而需要代理（jsdelivr 本次实测直连直接失败）。
3. **临时完全关掉系统代理**：只建议用来排障（能立刻验证"是不是代理干的"）。
   代价：更新检查会走不通（jsdelivr 直连失败），TTS 能否直连取决于你的网络。

## 四、代码侧的口径（为什么不做"全局绕过代理"）

- **逐功能决定，不做全局**。歌词源已实测「代理有害」→ 代码里显式直连（`pet/music_lyric.py`
  的 `_build_opener()` = `ProxyHandler({})`，见 PR #181）；其余功能保持"跟随系统代理"，
  因为更新（jsdelivr）实测**只有代理能用**，而 TTS 端点直连/代理都可用但海外网络需要代理。
- 桌宠**不会**改用户的代理设置，也不主动关代理；歌词请求只影响它自己。
- 已知未做：若某用户**只有代理能出网**，歌词取词会失败（其余功能不受影响）。
  可扩展成"直连失败后走代理兜底"，代价是失败路径从 9s 变 ~18s（取舍见 PR 报告 §五）。

## 五、本次实测原始输出（2026-09-22）

```
# 单源对照（代理开启时）
qq（c.y.qq.com，2 次请求）  走系统代理 41.28s → 39 行   直连 0.58s → 39 行
lrclib.net                 走系统代理 22.14s → 35 行   直连 0.83s / 3.47s / 1.28s → 35 行
music.163.com              走系统代理 20.39s            直连 0.25s

# 真实网易云会话（par: 蝴蝶 - 陶喆, position=None, app_id=cloudmusic.exe）
修复前 fetch_lyrics: 9.01s → None（0 行）
修复后 fetch_lyrics: 0.88s → 53 行

# 其他端点（ProxyEnable=0，显式对比直连/走代理）
TTS 音色列表(Microsoft)  直连 2.08s (170998 bytes)   走代理 2.26s (170998 bytes)
jsdelivr 更新清单        直连 0.22s URLError WinError 10054   走代理 1.45s (1976 bytes)
GitHub API               直连 0.69s (52124 bytes)    走代理 1.41s (52124 bytes)
```

> 说明：数字为单次取样，代表"量级与成败"，不是基准测试；复核请用 §二的探针命令重跑。
