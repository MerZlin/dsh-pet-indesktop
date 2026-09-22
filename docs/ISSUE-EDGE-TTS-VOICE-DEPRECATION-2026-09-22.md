# ISSUE：edge 合成「没声音」——音色被微软下架（2026-09-22）

- 现象（用户报告）：**「edge tts 语音合成失败」**
- 影响：语音报时/外部播报在配上已下架音色时**完全没声音**，且缓存目录里堆了 **0 字节 mp3**
- 结论：**两条独立原因叠加**，都不是网络问题
- 修复提交：本次 PR（`fix/edge-tts-robustness`）

---

## 一、证据链（先复现，再下结论）

| 步骤 | 结果 |
|---|---|
| 直接跑 `edge_tts.Communicate(<她的音色>).save()` | `NoAudioReceived: No audio was received. Please verify that your parameters are correct.` |
| `edge_tts.list_voices()` | **成功**，322 款 → 网络与 TLS 都没问题 |
| 换成 `zh-CN-XiaoxiaoNeural` | **成功**（18,720 字节） |
| 校验音色表 | 她配置的 `zh-CN-XiaohanNeural` **已不在在线表里**；内置清单 31 款里有 **10 款已下线** |
| 缓存目录 | 一串 **0 字节 mp3**，与失败时刻一一对应 |
| 连发 vs 间隔 | en-US 那批**连发全失败**；**间隔 6 秒逐个重试全部成功** |

下线的 10 款（全部 zh-CN）：晓涵 `Xiaohan`、晓辰 `Xiaochen`、晓梦 `Xiaomeng`、
晓墨 `Xiaomo`、晓秋 `Xiaoqiu`、晓睿 `Xiaorui`、晓双 `Xiaoshuang`、晓萱 `Xiaoxuan`、
晓颜 `Xiaoyan`、晓悠 `Xiaoyou`。

> 关键认识：这类失败**不会抛给用户可读的错误**——`edge_tts` 在服务端没回音频时只抛
> `NoAudioReceived`，而缓存路径先建文件再写，于是失败留下 0 字节文件；下一次命中判定
> 只看 `exists()`，就把这声「静音」当成了已有缓存，永远不再重试。

## 二、根因

1. **厂家会下架音色**，而用户配置里存的是「当时有效」的值。配上已下架的音色 → 空音频。
   内置清单本身也是快照，会随厂家增删而失真。
2. **连发请求会被偶发拒绝**（表现为同一异常），于是「偶发失败 + 0 字节缓存 + 不再重试」
   三者叠加，用户侧看起来就是「功能时好时坏，最后彻底没声」。

## 三、修复

| 位置 | 改动 |
|---|---|
| `pet/voice_chime.py` | 按在线音色表**重建 `VOICE_OPTIONS`**（删 10 款下线音色、补在线新音色，中英 36 款）；新增 `DEPRECATED_VOICE_LABELS` 与 `voice_label()`，让提示说中文名 |
| `pet/voice_chime_service.py` | 新增 `refresh_voice_list()` / `known_voices()`（在线表缓存，TTL 6h，**只在后台合成线程联网**）与 `resolve_voice()`：配置音色不在表里 → 改用默认音色 + 一句说明（每进程只弹一次气泡） |
| `pet/voice_chime_service.py` | `_TTSWorker`：**失败重试 `EDGE_RETRY_TIMES=2`、间隔 1.5s**；主音色几轮都失败 → **退默认音色再试**；产出 0 字节视为失败；失败产物**即时删除** |
| `pet/voice_chime_service.py` | 命中判定改为 `_cache_hit()`：文件存在**且非空**（`_fire` 与 `_maybe_precache` 都走它） |
| `pet/voice_chime_settings.py` | 配置值不在清单里时下拉项标注「不在清单里：<值>」；音色行提示写明「微软会下架音色，选到已不存在的会自动改用默认音色并提示一次」 |

## 四、验证

- 新增 `tests/test_voice_chime_edge_voice.py`（14 例，**全部不打网络**，`edge_tts` 用替身）：
  内置清单不含下线音色、`voice_label` 说人话、在线表缓存与失败保旧、`resolve_voice`
  兜底与沉默条件、worker 重试成功 / 退默认音色 / 0 字节算失败 / 缺库降级码、
  `_cache_hit` 非空判定、提示只弹一次。
- 既有 `tests/test_voice_chime.py` 的音色数量下界按现实校准（中文 21 → 14，下界 12），
  并注明原因。
- 实机复核：用她配置的那款已下架音色跑，现在会**自动改用默认音色并合成出音频**
  （22,752 字节），不再是一声静音。

## 五、残留风险与后续

- 在线音色表缓存 6 小时；期间新下架的音色仍会先失败一次，但会被重试/兜底接住（最多
  多花一次重试的延迟）。
- `voice_label()` 的「已下线」映射是人工维护的：新下线的音色会以 id 形式出现在提示里
  （信息不丢，只是不够亲切）。若要长期免维护，可考虑把「本次替换」的说明改为异步拉表后
  再补一条日志。
- 同一个 `NoAudioReceived` 也可能来自网络中间设备对 WebSocket 的干扰；那种情况下重试
  仍然是最小代价的兜底，但根因不在此 PR 覆盖范围内。
