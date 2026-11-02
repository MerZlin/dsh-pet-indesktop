# PR 报告：AI 回复朗读按句滴灌 + 预合成前锋（语音部分）

> **基线**：`d098a5f`（主分支 feat/petcore-vpet-architecture 上的「致谢 + ai.agent」提交）
> **分支**：`feat/ai-chat-voice-reply-only`　**日期**：2026-11-02
> **范围**：3 个文件（实现 2、测试 1；文档另计）
> **关联**：原 PR #424 的语音部分拆出来单独提；审查意见见 PR #424 评论（语音 P0 + 建议项）

## 一、核心特性

把「一条 AI 回复整段丢给语音通道」改成**按句滴灌 + 预合成前锋**：

| # | 能力 | 说明 |
|---|---|---|
| 1 | 按句滴灌 | 回复拆句，上一句**播完**才送下一句（共享通道单 worker，结构上不可能叠音） |
| 2 | 预合成前锋 | 送出当前句时，后台 `SpeechPort.synthesize()` 预热下一句缓存；命中缓存时句间无等网停顿 |
| 3 | 新覆盖旧 | 新回复先 `cancel_slot(slot)`（无则回退 `cancel_owner`）打断旧链，generation 作废在飞回调 |
| 4 | 失败不挡文字 | 任一句合成/播放失败只停朗读链、记日志，文字回答与下一轮不受影响 |
| 5 | 默认关 | `ai_voice_reply` 每次动作现读配置，热开关 |

**本次同时修掉审查指出的 P0**：`on_finished` 契约是 `Callable[[SpeechResult], None]`，
原实现是零参回调 → 真实端口下抛 `TypeError` 被 `_callback` 吞掉 → **多句回复只念第一句**。
附带：`stop()` 真正打断当前音频；英文句号断句；朗读按 `slot` 挂载与取消。

**红线 / 不变量**：开关关闭时行为与基线逐位相同；不改 `petcore/` 核心、不改设置页既有行。

## 二、修改文件说明

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `examples/petcore-mods/ai.chat/host/reply_voice.py` | **+239 / −0**（新增） | 新模块 `ReplyVoicePlayer`：`clean_for_speech`（去 markdown/代码块）、`split_sentences`（中文标点 + 英文句号后跟空白才切、超长按就近标点硬切）、`play/stop/_advance`（链式交棒、预合成、generation 作废）；`finished(result)` 收下契约参数 |
| `examples/petcore-mods/ai.chat/host/runtime.py` | **+70 / −18** | 删掉旧的 `_speech_text` + 整段 `speak`；新增 `_speak_reply`（建/复用 player）、`_voice_speak`（带 `slot`）、`_voice_precache`（后台线程 `synthesize`）、`_voice_cancel`（优先 `cancel_slot`，回退 `cancel_owner`）；`stop()` 里补 `player.stop()`；去掉不再使用的 `import re` |
| `tests/test_petcore_ai_reply_voice.py` | **+435 / −0**（新增） | 25 条：拆句/清洗 5、播放器纯逻辑 12、运行时接线 4、**真实契约回归 4**（`SpeechResult` 链式、`stop()` 打断、英文断句、runtime 传 slot 且优先 `cancel_slot`）、**真端口端到端 1**（`CoreSpeechPort` + `play_tracked` 回执，三句必须全念） |

### 未改动（故意不动）

- `petcore/` 核心与 `settings_page.py`：`ai_voice_reply` 开关基线已有，本次不新增设置项
- `mod.json` 版本号：留在 1.4.0，避免与上游版本号改动冲突（需要时由维护者统一 bump）
- 身份接力记忆（`relay_store.py` 等）：**不在本 PR 内**，按审查意见拆出，另行讨论

## 三、实现要点

- **为什么用回调链而不是轮询**：petcore 语音通道有 `on_finished`（真实播放结束）契约，
  链式交棒比主分支的 `channel_idle()` 轮询更准，也不占线程。
- **预合成只在「还有下一句」时做一次**：失败（通道忙/后端不可用）静默退化，
  该句轮到时缓存未命中会照旧在线合成，行为退回逐句合成。
- **打断用 slot 而不是 owner**：`cancel_owner(OWNER)` 会取消所有宠物的朗读；
  `cancel_slot(slot)` 只停本宠物，多宠场景不会互相掐断。
- **英文断句**：`(?<=\.)(?=\s)` —— 英文句号后跟空白才切，避开 `1.5`、`e.g`。

## 四、性能分析

**环境**：Python 3.12.14（`robot-body/dsh-pet-src/.venv`），`QT_QPA_PLATFORM=offscreen`，
本机 Windows；语音后端用**假合成器 + 假播放后端**（`RecordingSynth` / `_ImmediateTrackedAudio`），不联网、不碰音频设备。

| 指标 | 实测 |
|---|---|
| 本文件 25 条用例总耗时 | **0.91 s**（`pytest -q`） |
| 真端口端到端（3 句，含合成+播放回执+预合成线程） | **0.04 s**（`--durations=5`） |
| 新增线程 | 每句预合成 1 个短命 daemon 线程（`ai-voice-precache`），句间即退 |
| 新增系统调用/网络 | 无新增网络；每句仍是一次既有 `synthesize`（缓存命中则 0 次） |
| 稳态开销 | 开关关闭时零额外开销（`_speak_reply` 首行即返回）；开启时每句仅字符串切分 + 一次回调 |
| 内存 | 队列最多持有本回复剩余句子（≤ MAX_HISTORY 条文本），无累积结构 |

**取舍**：预合成走「每句一个线程」而非单线程池——线程生命周期短（一次合成），
省掉池的关闭语义；若审查认为线程数需收敛，可改单线程队列（代价是多一条常驻线程）。

## 五、实机运行记录

本机**没有可用的 petcore 打包版语音后端**（真机 SAPI/edge-tts 通道未配置），
所以按「真端口 + 假播放后端」验证行为契约，并保留红→绿证据：

1. **红**：把 `finished` 临时改回零参，跑真端口端到端用例：
   ```
   ERROR petcore.ui.speech_channel:speech_channel.py:194 speech callback failed
   TypeError: ReplyVoicePlayer._advance.<locals>.finished() takes 0 positional arguments but 1 was given
   FAILED tests/test_petcore_ai_reply_voice.py::test_real_speech_port_plays_every_sentence
   ```
   现象与审查描述一致：链停在第 1 句。
2. **绿**：恢复 `finished(result)` 后同用例通过（3 句全念）。

命令与结果（均为本机真实输出）：

```
$ python -m pytest tests/test_petcore_ai_reply_voice.py -q
25 passed in 0.91s

$ python -m ruff check examples/petcore-mods/ai.chat/host/reply_voice.py \
    examples/petcore-mods/ai.chat/host/runtime.py tests/test_petcore_ai_reply_voice.py
All checks passed!
```

## 六、未验证 / 边界

- **听感（句间停顿）未在真机语音通道验收**：本机无 petcore 语音后端可跑；主分支同方案的
  听感曾实测（「基本没有停顿」），但那是主分支轮询实现，不能当作本实现已验证。
- **多宠并发「只停本槽」未实测**：按 `cancel_slot` 契约实现，测试只覆盖「传 slot + 优先 cancel_slot」的接线。
- **英文断句**只覆盖句号后跟空白的常规写法；缩写（`e.g.`）会被切一刀，属已知取舍。
