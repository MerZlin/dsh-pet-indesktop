"""助手回复朗读：把 AI 回复按句滴灌进进程内唯一的音频通道。

设计（与 ``docs/PR-REPORT-CHAT-REPLY-VOICE-2026-10-20.md`` 同源）：

* **一个订阅点**：接 ``ChatService.register_global_finished``（快速对话 / 灵动岛对话 /
  完整聊天窗三个入口都经过它），不在三个 UI 里各埋一份——三个入口各埋一份的写法，
  漏一处就是"某个窗口的回复不念"。
* **按句滴灌**：音频通道的待播槽是**深度 1 且新覆盖旧**（为报时设计，见
  ``VoiceChimeService.speak``），因此必须问「现在空闲吗」再发下一句；一次性把三句
  连着 ``speak()`` 进去，前两句会被第三句覆盖，只剩最后一句被念出来。
* **不新起通道**：朗读是这条通道的又一个使用者（报时 / 节日 / 点击自言 / 抽签 /
  待办播报之后），结构上不可能叠音。
* **只念对话，不念歌词**：歌词链与 ``ChatService`` 无交集，本模块也不订阅歌词；
  ``tests/test_chat_reply_voice.py`` 有守卫防止后人误接。
* **默认关**：关着时不建通道、不订阅、不建定时器（``pet/app.py::_chime_wanted``）。

模块顶层**不** import ``pet.chat``：无聊天打包变体里 ``pet.chat`` 不存在，本模块仍须
可 import；订阅那一刻才惰性取 ``ChatService``，取不到就只保留纯文本能力、不订阅。
"""

from __future__ import annotations

import logging
import re
from collections import deque
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

#: 单句硬上限：边念边等合成（每句一次网络往返），超过这个长度用户会明显感到"卡"，
#: 也不会是自然断句。超长的按标点就近切，切不出来才硬切。
MAX_SENTENCE_CHARS = 80

#: 排队重试间隔：通道忙（合成在飞 / 正在出声）时多久再问一次。与报时的 20s tick
#: 无关——朗读要尽快接上下一句，但不能空转。
PUMP_INTERVAL_MS = 250

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
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;…])")
#: 硬切时的"就近切"位置：逗号、顿号、冒号、破折号、空格。
_SOFT_BREAK = re.compile(r"[，,、：:\u2014\-—\s]")


def clean_for_speech(text: str) -> str:
    """把一段 markdown 回复清成"能念"的纯文本。

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


class ChatReplyReader:
    """把一条条 AI 回复拆成句子，串行送进音频通道。

    依赖全部是注入的，方便测试在不建 Qt 通道的前提下验证"滴灌顺序 / 不丢队列 /
    新覆盖旧"：

    * ``speak(sentence) -> bool``：送一句进通道（实现里就是
      ``VoiceChimeService.speak(..., respect_quiet=False)``）；
    * ``channel_idle() -> bool``：通道此刻是否真空闲（``VoiceChimeService.channel_idle``）；
    * ``enabled() -> bool``：每次动作都现读配置（热开关，不必重建对象）；
    * ``schedule(ms, fn)``：延迟回调，默认走 ``QTimer.singleShot``，测试可注入假时钟。
    """

    def __init__(
        self,
        *,
        speak: Callable[[str], bool],
        channel_idle: Callable[[], bool],
        enabled: Callable[[], bool],
        schedule: Callable[[int, Callable[[], None]], None] | None = None,
        interval_ms: int = PUMP_INTERVAL_MS,
        max_chars: int = MAX_SENTENCE_CHARS,
    ) -> None:
        self._speak = speak
        self._channel_idle = channel_idle
        self._enabled_source = enabled
        self._schedule_impl = schedule or self._qt_schedule
        self._interval_ms = interval_ms
        self._max_chars = max_chars
        self._queue: deque[str] = deque()
        self._subscribed = False
        self._pump_scheduled = False
        self._generation = 0

    # ---------------------------------------------------------------- 状态
    @property
    def enabled(self) -> bool:
        """配置现值（热读）。读不到就当关着：朗读是主动打扰型功能。"""
        try:
            return bool(self._enabled_source())
        except Exception:
            logger.exception("读取「助手回复朗读」开关失败，按关闭处理")
            return False

    @property
    def pending(self) -> int:
        """队列里还没送出去的句子数（测试与探针用）。"""
        return len(self._queue)

    @property
    def subscribed(self) -> bool:
        return self._subscribed

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> bool:
        """订阅进程级「回复完成」。返回是否本次真的订阅上了（幂等）。"""
        if self._subscribed:
            return False
        service = self._chat_service()
        if service is None:
            logger.info("没有聊天模块（无聊天打包变体），助手回复朗读不订阅")
            return False
        service.register_global_finished(self.feed)
        self._subscribed = True
        return True

    def stop(self) -> None:
        """退订并清空队列。``generation`` 让还在飞的延迟回调自动作废。"""
        self._generation += 1
        self._pump_scheduled = False
        if self._subscribed:
            service = self._chat_service()
            if service is not None:
                try:
                    service.unregister_global_finished(self.feed)
                except Exception:
                    logger.exception("退订「助手回复朗读」失败")
        self._subscribed = False
        self._queue.clear()

    # ---------------------------------------------------------------- 数据流
    def feed(self, text: str) -> None:
        """收到一条完整回复：拆句、替换队列、开始念。"""
        if not self.enabled:
            return
        sentences = split_sentences(text, max_chars=self._max_chars)
        if not sentences:
            return
        # 新回复覆盖旧队列：上一轮还没念完的内容直接作废（与通道"新覆盖旧"同口径，
        # 免得用户听到的是两轮对话交叉的句子）。
        self._queue.clear()
        self._queue.extend(sentences)
        self.pump()

    def pump(self) -> None:
        """通道空闲就送下一句；忙就排一次重试。"""
        self._pump_scheduled = False
        if not self.enabled:
            self._queue.clear()
            return
        if not self._queue:
            return
        if not self._channel_idle():
            self._schedule_pump()
            return
        sentence = self._queue.popleft()
        try:
            self._speak(sentence)
        except Exception:
            logger.exception("送朗读句失败：%s", sentence[:20])
        if self._queue:
            self._schedule_pump()

    # ---------------------------------------------------------------- 内部
    def _schedule_pump(self) -> None:
        if self._pump_scheduled:
            return
        self._pump_scheduled = True
        generation = self._generation
        self._schedule_impl(self._interval_ms, lambda: self._pump_if_current(generation))

    def _pump_if_current(self, generation: int) -> None:
        if generation != self._generation:
            return
        self.pump()

    @staticmethod
    def _chat_service():
        """惰性取 ``ChatService``；无聊天变体或无 Qt 时返回 None（不订阅）。"""
        try:
            from .chat.service import ChatService
        except Exception:
            return None
        return ChatService

    @staticmethod
    def _qt_schedule(ms: int, fn: Callable[[], None]) -> None:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(int(ms), fn)
