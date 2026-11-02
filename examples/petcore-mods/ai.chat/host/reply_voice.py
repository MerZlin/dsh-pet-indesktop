# -*- coding: utf-8 -*-
"""AI 回复朗读：按句滴灌共享语音通道 + 预合成前锋（petcore 移植版）。

与主分支 ``pet/chat_reply_voice.py`` 同一套设计，但按 petcore 的端口语义改写：

* **按句滴灌**：``HostPorts.speech``（``CoreSpeechPort``）只有一个 worker
  （合成 → 播放 → 下一句），句子一个接一个 ``speak()`` 进去、上一句播完
  （``on_finished``）才送下一句，结构上不可能叠音。
* **预合成前锋**：送出当前句的同时把下一句交给
  ``SpeechPort.synthesize()``（只写缓存、不出声，由调用方放到后台线程），
  下一句轮到时命中缓存、不再等联网合成——句与句之间没有"边等边念"的停顿。
  预合成失败静默退化：该句轮到时照旧在线合成，行为退回逐句合成。
* **新回复覆盖旧回复**：``play()`` 先 ``cancel_owner`` 打断上一轮正在播的音频，
  再用 generation 作废还在飞的链式回调——不会出现两轮回复的句子交叉。
* **失败不挡文字**：任一句合成/播放失败（或免打扰/通道不可用）只停朗读链、
  记一条日志，文字回答与下一轮回复不受影响。
* **默认关**：开关（``ai_voice_reply``）每次动作都现读配置，热开关。

本模块不 import Qt、不 import ``petcore.net``/``petcore.api``，只依赖注入的
回调，测试可以在不建音频设备、不联网的前提下验证滴灌顺序与预合成行为。
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

#: 单句硬上限：边念边等合成（每句一次网络往返），超过这个长度用户会明显感到"卡"，
#: 也不会是自然断句。超长的按标点就近切，切不出来才硬切。
MAX_SENTENCE_CHARS = 80

_CODE_BLOCK = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M)
_QUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.M)
_BULLET = re.compile(r"^[ \t]{0,3}(?:[-*+]|\d{1,2}[.)])[ \t]+", re.M)
_TABLE_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)+\|?[ \t]*$", re.M)
_EMPHASIS = re.compile(r"\*\*|__|~~|(?<!\*)\*(?!\*)|(?<!_)_(?!_)")
_ANGLE_TAG = re.compile(r"<[^>\n]{1,120}>")
_SPACES = re.compile(r"[ \t]{2,}")
#: 句末切分：中文标点直接切；英文句号只在「后面跟空白」时切（避免切开 1.5、e.g 这类）。
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;…])|(?<=\.)(?=\s)")
#: 硬切时的"就近切"位置：逗号、顿号、冒号、破折号、空格。
_SOFT_BREAK = re.compile(r"[，,、：:\u2014\-—\s]")


def clean_for_speech(text: str) -> str:
    """把一段 markdown 回复清成"能念"的纯文本（与主分支同口径）。

    去代码块（留「代码略」占位：不念代码本身，但要让听的人知道这里跳过了东西）、
    图片、链接目标（保留链接文字）、标题/引用/列表记号、表格分隔线、强调记号、
    行内反引号、尖括号标签；保留正文里的中文标点，供 ``split_sentences`` 断句。
    """
    if not text:
        return ""
    result = _CODE_BLOCK.sub(" 代码略 ", text)
    result = _IMAGE.sub(" ", result)
    result = _LINK.sub(r"\1", result)
    result = _INLINE_CODE.sub(r"\1", result)
    result = _TABLE_RULE.sub("", result)
    result = _HEADING.sub("", result)
    result = _QUOTE.sub("", result)
    result = _BULLET.sub("", result)
    result = _EMPHASIS.sub("", result)
    result = _ANGLE_TAG.sub(" ", result)
    lines = []
    for line in result.splitlines():
        collapsed = _SPACES.sub(" ", line).strip()
        if collapsed:
            lines.append(collapsed)
    return "\n".join(lines).strip()


def _hard_split(piece: str, max_chars: int) -> Iterable[str]:
    """把超长的一段按就近标点切开，切不出来才硬切。"""
    while len(piece) > max_chars:
        window = piece[:max_chars]
        cut = None
        for match in _SOFT_BREAK.finditer(window):
            cut = match.end()
        if cut is None or cut < max_chars // 3:
            cut = max_chars
        chunk = piece[:cut].strip()
        if chunk:
            yield chunk
        piece = piece[cut:].strip()
    if piece:
        yield piece


def split_sentences(text: str, *, max_chars: int = MAX_SENTENCE_CHARS) -> list[str]:
    """按句末标点切句；单句超过 ``max_chars`` 再按就近标点（或硬切）拆。

    输入应当是 ``clean_for_speech`` 处理过的文本；纯空白与纯标点的碎片会被丢掉，
    免得把空串送进合成、白跑一次网络往返。
    """
    cleaned = clean_for_speech(text)
    if not cleaned:
        return []
    sentences: list[str] = []
    for raw in _SENTENCE_SPLIT.split(cleaned):
        for paragraph in raw.splitlines():
            piece = paragraph.strip()
            if not piece:
                continue
            if not any(ch.isalnum() for ch in piece):
                # 只有标点/装饰字符（例如单独一个"。"或"---"）：不念。
                continue
            for chunk in _hard_split(piece, max_chars):
                sentences.append(chunk)
    return sentences


class ReplyVoicePlayer:
    """把一条条 AI 回复拆成句子、串行送进共享语音通道，同时预合成下一句。

    依赖全部是注入的，方便测试在不建音频通道、不联网的前提下验证
    「滴灌顺序 / 不叠音 / 新覆盖旧 / 预合成前锋」：

    * ``speak(sentence, on_finished, on_error)``：送一句进通道（实现里就是
      ``SpeechPort.speak(..., owner=OWNER)``，``on_finished`` 在**播放完成**后触发，
      ``on_error`` 在合成/播放失败或通道不可用（免打扰等）时触发）；
    * ``precache(sentence)``：把一句**提前合成进缓存、不出声**。实现负责放到
      后台线程（``SpeechPort.synthesize`` 是阻塞的）；失败静默退化——该句轮到
      时照旧在线合成。传 ``None`` 完全退回旧行为（无预合成）；
    * ``enabled()``：每次动作都现读配置（热开关，不必重建对象）；
    * ``cancel()``：打断上一轮还在播/排队的音频（实现里就是
      ``SpeechPort.cancel_owner(OWNER)``）。
    """

    def __init__(
        self,
        *,
        speak: Callable[[str, Callable[[object], None], Callable[[object], None]], None],
        enabled: Callable[[], bool],
        precache: Callable[[str], None] | None = None,
        cancel: Callable[[], None] | None = None,
        max_chars: int = MAX_SENTENCE_CHARS,
    ) -> None:
        self._speak = speak
        self._enabled_source = enabled
        self._precache = precache
        self._cancel = cancel
        self._max_chars = max_chars
        self._queue: list[str] = []
        self._generation = 0

    # ---------------------------------------------------------------- 状态
    @property
    def enabled(self) -> bool:
        """配置现值（热读）。读不到就当关着：朗读是主动打扰型功能。"""
        try:
            return bool(self._enabled_source())
        except Exception:
            logger.exception("读取「朗读回复」开关失败，按关闭处理")
            return False

    @property
    def pending(self) -> int:
        """队列里还没送出去的句子数（测试与探针用）。"""
        return len(self._queue)

    # ---------------------------------------------------------------- 数据流
    def play(self, text: str) -> bool:
        """收到一条完整回复：打断上一轮、拆句、开始念。返回是否真的开念。"""
        if not self.enabled:
            return False
        sentences = split_sentences(text, max_chars=self._max_chars)
        if not sentences:
            return False
        # 新回复覆盖旧队列：上一轮还没念完的内容直接作废（与共享通道的
        # owner 取消同口径，免得用户听到两轮对话交叉的句子）。
        self._generation += 1
        generation = self._generation
        self._queue = list(sentences)
        try:
            if self._cancel is not None:
                self._cancel()
        except Exception:
            logger.exception("打断上一轮朗读失败")
        self._advance(generation)
        return True

    def stop(self) -> None:
        """清空队列并打断正在播的那一句（``generation`` 让在飞的回调作废）。"""
        self._generation += 1
        self._queue = []
        if self._cancel is not None:
            try:
                self._cancel()
            except Exception:
                logger.exception("打断当前朗读失败")

    # ---------------------------------------------------------------- 内部
    def _advance(self, generation: int) -> None:
        if generation != self._generation:
            return
        if not self.enabled:
            self._queue = []
            return
        if not self._queue:
            return
        sentence = self._queue.pop(0)
        # 预合成前锋：只要还有下一句就试一次。失败（通道忙 / 后端不可用）是正常
        # 情况——不重试，该句轮到时缓存未命中会照旧在线合成，行为退化为逐句合成；
        # 只有异常才记日志。
        if self._queue and self._precache is not None:
            try:
                self._precache(self._queue[0])
            except Exception:
                logger.exception("预合成下一句失败：%s", self._queue[0][:20])

        def finished(result: object = None) -> None:
            # 契约：``on_finished: Callable[[SpeechResult], None]``（petcore/contracts/speech.py），
            # 真实通道会把播放结果传进来。这里必须收下这个参数——零参回调在真实端口下会抛
            # TypeError，被通道的 ``_callback`` 吞掉，表现为「多句回复只念第一句」。
            # 上一句播完才送下一句：共享通道只有一条 worker，逐句交棒天然不叠音。
            if generation == self._generation:
                self._advance(generation)

        def failed(error) -> None:
            # 一句失败（合成/播放失败、免打扰、通道不可用）就停整条链：
            # 继续送只会得到同样的失败，而且用户已经在看文字回答。
            reason = str(getattr(error, "reason", "") or "speech")
            logger.info("ai voice reply failed: reason=%s", reason)
            if generation == self._generation:
                self._queue = []

        try:
            self._speak(sentence, finished, failed)
        except Exception:
            # 送句本身抛异常（端口坏掉）：停链并记日志，文字回答不受影响。
            logger.exception("送朗读句失败：%s", sentence[:20])
            self._queue = []
