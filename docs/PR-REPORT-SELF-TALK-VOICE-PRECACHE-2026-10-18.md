# 台词语音不再「一个文件都不生成」——改用内置 TTS 注册表

> 分支 `fix/self-talk-voice-precache`，基线 `a19157f`。关掉 #305。

## 〇、现象与根因

issue #305 标题只有一句「台词语音并没有生成」。机主补充的关键线索是：
**在桌宠设置里点「试听」是有声音的**。

这说明 TTS 后端与网络都正常——因为「试听」走的是
`VoiceChimeService.say_now` → `pet/tts` 注册表（edge / sapi / doubao / mimo）。
而「台词语音」是**另一条完全独立的链路**：`pet/self_talk_voice.py` 绕过整个注册表，
直连用户机器上自建的 CosyVoice 服务（`DEFAULT_SERVER = "http://127.0.0.1:9880"`）。
服务不在线时它只打一条 `logger.info("台词语音预缓存跳过：本地 TTS 服务不可用")`
然后 return——**用户侧表现就是「开关打开了、没有报错、一个 wav 都没有」。**

三个叠加断点，任一个都足以让功能完全不工作：

1. **依赖缺失且静默降级**：要求机主自己跑一台 CosyVoice。设置页文案自己都写着
   「需要本机 CosyVoice 语音服务在运行；没装本地语音服务时保持关闭即可」。
2. **独立设置进程保存不触发**：`settings_process_isolation` 默认 True，设置页几乎
   总在独立进程存盘；主进程走 `_apply_external_config_change`，而该方法里**没有任何**
   `precache_self_talk_voice` 调用（全文只有「进程内设置对话框关闭」与「启动 +9s」
   两处）。→ 打开开关并保存，本次运行不生成任何东西。
3. **名单读错配置层**：`self_talk_texts` 是**每宠键**，但预缓存读的是主配置 →
   slot≠0 的宠即使在别的键上生成了，点击时也命中不到缓存，悄悄回退在线合成
   （音色变成另一个人）。

另外两个次要缺陷一并修：写入不原子（截断的非空 wav 会被当命中，播出来是半句/爆音）、
缓存键有两份实现（`self_talk_voice.cache_path` 与 `app.self_talk_voice_file` 各写一遍
`md5(text)[:16] + ".wav"`，无交叉测试）。

**顺带修掉一个让「离线语音」彻底不可用的既有缺陷**：`pet/tts/sapi.py` 没有做
**线程级** COM 初始化。COM 是按线程初始化的，GUI 线程被 Qt 初始化过、合成跑在
`_TTSWorker` 后台线程——那里 `CreateObject` 直接抛
`WinError -2147221008 尚未调用 CoInitialize`。实测：

```
main_voices = 3
thread failed: 本地语音合成失败：[WinError -2147221008] 尚未调用 CoInitialize。
```

这解释了为什么「试听在主线程是好的」而「后台合成（报时预合成 / 台词预缓存）一律
失败」。不修它，本条修复在选 SAPI 的机器上仍然是 0 产出。

## 一、核心特性

1. **复用注册表**：台词预缓存与「试听」走同一条 `voice_chime.synth_attempts` 链
   （后端 + 音色 + 回退 + preflight），「试听有声音」因此**结构性等价于**
   「台词语音能生成」，而不是又一处会漂移的第二真相。
2. **自建 CosyVoice 降为可选覆盖**：`DSH_PET_TTS_URL`（或显式 `server`）存在时仍先走它，
   健康检查失败就**落回注册表**并留 warning。不新增任何用户可见设置键。
3. **外部存盘即触发**：`_apply_external_config_change` 里补一次
   `precache_self_talk_voice("外部保存设置后")`——这是独立设置进程保存的唯一通道。
4. **每宠作用域**：legacy 取 `PetInstance.config`，overlay 取壳的 `_sprite_config(sprite)`；
   不另造读取口径。
5. **原子落盘**：同目录唯一临时文件 → 校验 → `config.atomic_replace_with_retry`
   （Windows 上目标文件正被播放器打开时裸 `os.replace` 会 `WinError 5`）。
6. **命中判定收紧**：非空 + wav 头部自洽（RIFF 偏移 4 声明的长度 ≤ 实际字节数）；
   临时文件以 `.tmp-` 前缀，所以按 `<md5>.*` 枚举时不会撞上半成品。
7. **SAPI 线程 COM**：`_ensure_com()` 在 `_voice_objects()` 与 `synth()` 入口保证
   当前线程进过 COM 初始化。

## 二、修改文件说明

| 文件 | 增/删 | 改了什么 + 为什么 |
|---|---|---|
| `pet/self_talk_voice.py` | +261 / −72 | 链路改为注册表；`cache_path` 加扩展名参数（edge/doubao 产 mp3、sapi/mimo 产 wav）；`_has_audio` 校验 wav 头；`cached_audio` 按 `<md5>.*` 枚举；`collect_all_lines` 合并多宠名单去重；`_publish` 原子写；`_attempts` 复用 `voice_chime.normalize_chime_config` + `synth_attempts`；`run_precache` 自建服务→注册表回退；`enabled` 公开；`start_precache` 透传每宠视图。删除无生产调用者的 `missing_lines` |
| `pet/app.py` | +42 / −21 | `self_talk_voice_file` 委托 `cached_audio`（消除两份 md5 实现）；`precache_self_talk_voice` 先过 `enabled()` 门；新增 `_self_talk_voice_configs`（legacy / overlay 两条拓扑各自的每宠视图）；`_apply_external_config_change` 触发一次；删除因此不再使用的 `hashlib` 顶层 import |
| `pet/settings_interaction.py` | +2 / −2 | 设置页文案不再承诺「需要本机 CosyVoice」，改成「与语音报时同一个后端，在本机预先生成」 |
| `pet/tts/sapi.py` | +16 / −0 | 新增 `_ensure_com()`（线程级 `comtypes.CoInitialize()`），在 `_voice_objects()` 与 `synth()` 入口调用 |
| `tests/test_self_talk_voice_precache.py` | +395 / −69 | 16 → 32 条用例 |
| `tests/test_tts_sapi_thread.py` | 新增 | 3 条线程 COM 回归（跨平台 `importorskip`） |
| `tests/test_voice_chime_service.py` | +13 / −2 | 预缓存夹具改写真 wav（旧伪头会被新判定正确拒绝） |
| `tests/test_settings_process_isolation.py` / `tests/test_requested_regressions.py` | +5 / −0 | 裸 `AppShell.__new__` 桩补 `config`（真实壳恒有该属性） |

## 三、性能分析

- **点击路径不变**：`self_talk_voice_file` 只用标准库做目录枚举。实测 2 个文件时
  **0.33 ms/次**，60 个文件时 **~2.0 ms/次**（目录枚举 + 排序）。点击是用户动作级，
  不进 tick。
- **惰性 import 是刻意的**：`import pet.tts` 实测 **0.351 s**，而 `self_talk_voice`
  随 `pet.app` 在启动路径上被导入。合成链（tts / voice_chime / config）全部推入后台
  线程，**启动路径零额外成本**。
- **预缓存本身**：边缘后端真机 1 句 **3.92 s**（联网），SAPI 离线 2 句 **0.76 s**；
  重复跑已补齐时 **1 ms** 返回 `up-to-date`。跑在既有后台线程里，不阻塞 GUI。
- **外部存盘触发**：一次 `start_precache` **0.85 ms**（已补齐时线程立刻返回）。
- **无新增线程、定时器、网络请求（除既有合成链）、无内存增长路径**。
- SAPI 修复的增量：一次 `CoInitialize` 是微秒级，单线程一次。

## 四、实机运行记录

**旧行为下的红**（先写用例、未改产品代码，`tests/test_self_talk_voice_precache.py`）：

```
23 failed, 9 passed in 18.64s
FAILED ... test_producer_output_is_what_the_click_path_plays
  AssertionError: 不该回退在线合成            # ← 正是 issue #305 的现象
FAILED ... test_run_precache_reports_missing_backend_instead_of_silence
FAILED ... test_local_service_failure_falls_back_to_the_registry
FAILED ... test_external_settings_save_starts_the_precache
FAILED ... test_collect_all_lines_reads_each_pet_own_slot_scope
...
```

改后：**32 passed in 3.63s**。

**SAPI 线程 COM 的红**（`git stash` 掉 `pet/tts/sapi.py`）：

```
3 failed in 1.43s
  FAILED test_ensure_com_initializes_the_calling_thread
  FAILED test_sapi_voice_probe_works_on_a_background_thread
  FAILED test_sapi_synth_writes_a_wav_from_a_background_thread
  E  {'error': 'TtsUnavailable: 本地语音合成失败：[WinError -2147221008] 尚未调用 CoInitialize。'}
```

修后：**3 passed in 2.85s**，真合成产出 `RIFF....WAVE`、60872 字节。

**真机链路证据**（非 mock）：

- 默认 edge 后端、经 `AppShell.precache_self_talk_voice` 的真后台线程：3.92 s 合成 1 句 →
  `self_talk_voice/7155f701840b9a89.mp3`（14688 B），`self_talk_voice_file` 命中它。
- SAPI（离线）：2 句 0.76 s → 127426 B / 96128 B，头部 `RIFF....WAVE`；重复跑 1 ms `up-to-date`。
- 后台线程探针（SAPI 修复后）：`{'thread_voices': 3}`、真合成 `{'size': 60872, 'head': b'RIFF...WAVE'}`。

门禁：

- 指定 7 个族（台账 / 点击朗读 / 报时服务 / 设置进程隔离 / 抽签 / 配置 schema / 架构）：**319 passed**。
- 相邻族（同样走 `_apply_external_config_change` / `_modern_settings_finished`）：
  **246 passed, 1 skipped**。
- `ruff check pet/ tests/` → `All checks passed!`
- 整仓全量：`8169 passed, 15 skipped, 1 failed`，唯一红是
  `tests/test_proactive.py::...test_capture_window_rect_coordinates_and_clamping`——
  纯净基线上同样红（本机虚拟屏原点为负 `-1920`，用例写死正坐标假设），与本改动无交集。

## 五、已知限制与风险（如实）

1. **换音色/换后端不会让已有文件失效**：缓存键只有文本（外部脚本契约 + 点击路径必须
   只用标准库，不能为取 flavor 去 import `pet.tts` / 读钥匙串）。换音色后已缓存的句子
   仍播旧音色，只有新句子用新音色。要修得引入 flavor 进键或 sidecar 清单，成本明显
   高于本 issue 范围。
2. **同一句同时存在 `<md5>.mp3` 与 `<md5>.wav` 时按文件名排序决定**（只有换过后端才会
   出现，两者都是这句话的合法音频）。
3. **非 wav 产物的截断无法判定**（只判非空）；这类文件只可能来自旧的非原子写入或
   外部脚本。
4. **overlay 分支只有结构级用例**（假壳提供 `_all_sprites` / `_sprite_config`），没有在
   overlay 真机上跑过；legacy 分支走的是真实 `PetInstance.config`。
5. **MiMo / 豆包未做端到端实机验证**（需要机主凭据）。验证到的是 edge（真机联网）
   与 SAPI（真机离线）。
6. **SAPI 的 `CoInitialize` 不做配对 `CoUninitialize`**：调用方要么是常驻 GUI 线程，
   要么是短命 worker 线程（退出时系统回收）。同线程重复调用是引用计数语义，多调无害。
   若将来有长时间运行的自建线程反复调用，需要补配对。
7. `pet/config.py` 里 `self_talk_voice_precache_enabled` 的默认值注释仍写着「需本机 TTS
   服务」——本轮该文件被别的线占用，注释已过时，留给下一次顺手改。
