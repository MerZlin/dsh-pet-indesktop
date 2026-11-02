# -*- coding: utf-8 -*-
"""AI 回复朗读：按句滴灌共享语音通道 + 预合成前锋。

设计要点（与主分支 ``pet/chat_reply_voice.py`` 同源，按 petcore 端口语义改写）：

* **按句滴灌**：``HostPorts.speech`` 只有一条播放 worker；上一句播完
  （``on_finished(SpeechResult)``）才送下一句，结构上不会叠音。
* **预合成前锋**：送出当前句时把下一句交给 ``PrecacheWorker``；worker 只有
  一个在飞任务，新的句子覆盖尚未开始的旧任务，不会为每句创建线程。
* **新回复覆盖旧回复**：``play()`` 先 ``cancel`` 打断上一轮音频，再用
  generation 作废还在飞的链式回调。
* **失败不挡文字**：任一句失败只停朗读链并记日志；文字回答不受影响。
* **默认关**：开关每次动作现读，热开关。

本模块不 import Qt / ``petcore.net`` / ``petcore.api``，只依赖注入的回调。
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

#: 单句硬上限：超过这个长度用户会明显感到“卡”，按就近标点切开。
MAX_SENTENCE_CHARS = 80
#: 一条回复最多念多少句 / 多少字；旧实现整段截 500 字，这里给足但必须有界。
MAX_SENTENCES = 40
MAX_TOTAL_CHARS = 4000

_CODE_BLOCK = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M)
_QUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.M)
_BULLET = re.compile(r"^[ \t]{0,3}(?:[-*+]|\d{1,2}[.)])[ \t]+", re.M)
_TABLE_RULE = re.compile(
    r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)+\|?[ \t]*$", re.M
)
_EMPHASIS = re.compile(r"\*\*|__|~~|(?<!\*)\*(?!\*)|(?<!_)_(?!_)")
_ANGLE_TAG = re.compile(r"<[^>\n]{1,120}>")
_SPACES = re.compile(r"[ \t]{2,}")
#: 中文句末标点直接切；英文句末只在“后面是空白/结尾”时切，避免把 3.14 切开。
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;…])|(?<=[.!?])(?=\s|$)")
#: 硬切时的“就近切”位置：逗号、顿号、冒号、破折号、空格。
_SOFT_BREAK = re.compile(r"[，,、：:\u2014\-—\s]")


def clean_for_speech(text: str) -> str:
    """把一段 markdown 回复清成“能念”的纯文本（与主分支同口径）。"""
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
    """按句末标点切句；单句超过 ``max_chars`` 再按就近标点（或硬切）拆。"""
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
                # 只有标点/装饰字符（例如单独一个“。”或“---”）：不念。
                continue
            for chunk in _hard_split(piece, max_chars):
                sentences.append(chunk)
    return sentences


class PrecacheWorker:
    """单线程、单槽的预合成 worker：新句子覆盖尚未开始的旧句子。

    ``SpeechPort.synthesize`` 是阻塞调用；为每条回复的每句话开一个线程会让
    长回复瞬间出现几十个线程。这里固定一个 daemon 线程，最多一个在飞任务；
    ``clear()`` 丢掉尚未开始的任务，``close()`` 让线程退出（在飞的合成无法
    取消，但结果只会写缓存，不影响新回复）。
    """

    def __init__(self, synthesize: Callable[[str], None]) -> None:
        self._synthesize = synthesize
        self._condition = threading.Condition()
        self._pending: str | None = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._run, name="ai-voice-precache", daemon=True
        )
        self._thread.start()

    def submit(self, sentence: str) -> None:
        with self._condition:
            if self._closed:
                return
            self._pending = sentence
            self._condition.notify()

    def clear(self) -> None:
        with self._condition:
            self._pending = None

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify()

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                sentence = self._pending
                self._pending = None
            try:
                self._synthesize(sentence)
            except Exception as exc:  # noqa: BLE001 - 轮到时在线合成兜底
                logger.info(
                    "ai voice reply precache failed: type=%s", type(exc).__name__
                )


class ReplyVoicePlayer:
    """把一条条 AI 回复拆成句子、串行送进共享语音通道，同时预合成下一句。"""

    def __init__(
        self,
        *,
        speak: Callable[
            [str, Callable[[object], None], Callable[[object], None]], None
        ],
        enabled: Callable[[], bool],
        precache: Callable[[str], None] | None = None,
        cancel: Callable[[], None] | None = None,
        max_chars: int = MAX_SENTENCE_CHARS,
        max_sentences: int = MAX_SENTENCES,
        max_total_chars: int = MAX_TOTAL_CHARS,
    ) -> None:
        self._speak = speak
        self._enabled_source = enabled
        self._precache = precache
        self._cancel = cancel
        self._max_chars = max_chars
        self._max_sentences = max(1, int(max_sentences))
        self._max_total_chars = max(1, int(max_total_chars))
        self._queue: list[str] = []
        self._generation = 0
        self._lock = threading.RLock()

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
        with self._lock:
            return len(self._queue)

    def play(self, text: str) -> bool:
        """收到一条完整回复：打断上一轮、拆句、开始念。返回是否真的开念。"""
        if not self.enabled:
            return False
        sentences = self._limit(split_sentences(text, max_chars=self._max_chars))
        if not sentences:
            return False
        with self._lock:
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
        """清空队列并打断正在播的音频。"""
        with self._lock:
            self._generation += 1
            self._queue = []
        try:
            if self._cancel is not None:
                self._cancel()
        except Exception:
            logger.exception("停止朗读失败")

    def _limit(self, sentences: list[str]) -> list[str]:
        limited: list[str] = []
        total = 0
        for sentence in sentences[: self._max_sentences]:
            remaining = self._max_total_chars - total
            if remaining <= 0:
                break
            if len(sentence) > remaining:
                sentence = sentence[:remaining].rstrip()
            if sentence:
                limited.append(sentence)
                total += len(sentence)
        return limited

    def _advance(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            if not self.enabled:
                self._queue = []
                return
            if not self._queue:
                return
            sentence = self._queue.pop(0)
            next_sentence = self._queue[0] if self._queue else ""
        if next_sentence and self._precache is not None:
            try:
                self._precache(next_sentence)
            except Exception:
                logger.exception("预合成下一句失败：%s", next_sentence[:20])

        def finished(_result: object = None) -> None:
            # 上一句播完才送下一句：共享通道只有一条 worker，逐句交棒天然不叠音。
            self._advance(generation)

        def failed(error) -> None:
            reason = str(getattr(error, "reason", "") or "speech")
            logger.info("ai voice reply failed: reason=%s", reason)
            with self._lock:
                if generation == self._generation:
                    self._queue = []

        try:
            self._speak(sentence, finished, failed)
        except Exception:
            logger.exception("送朗读句失败：%s", sentence[:20])
            with self._lock:
                self._queue = []
