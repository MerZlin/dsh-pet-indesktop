# -*- coding: utf-8 -*-
"""AI 碎碎念：自言自语文本从固定句池改走 LLM 现场生成（按用户填写的性格）。

产品语义（2026-11 用户令）：碎碎念不再每次从 ``self_talk_texts`` 固定句池抽
一句，而是触发时后台调一次**聊天子系统当前激活模型**的非流式小请求，按设置
页填写的性格描述（``self_talk_ai_personality``）现场生成一句随机台词；生成
成功才替换固定句——没配模型 / 请求失败 / 超时 / 输出不可用一律回落固定句池。
碎碎念是装饰性功能，任何异常都不该让它从桌面上消失。

复用与边界：
* 网络/鉴权全部复用 ``pet.chat.providers.OpenAICompatibleProvider``（不新建
  网络层，与 memory_extract 同一口径）；provider 与密钥读
  ``Config.chat_settings()`` 的激活项（``Config.resolve_api_key`` 兼容 keyring）。
* no-chat 变体（打包剔除 ``pet.chat``）整条能力自动不存在：``pet.chat`` 的
  import 全部延迟在函数内做，ImportError 按"未配置"处理，绝不影响固定句池。
* 线程纪律：生成走 QThread worker（``ChatService._Worker`` 同款），成功/失败
  经桥接 QObject 的 queued signal 回 UI 线程——绝不在 GUI 线程发请求，也绝不
  让回调在 worker 线程里碰气泡（桥接 receiver 不在主线程时 PySide6 会在
  **worker 线程**里直接执行普通回调，这是本模块单独造 ``_Bridge`` 的原因）。
* 进程收口：worker 强引用登记在模块级 ``_LIVE_WORKERS``（宿主先死也不让
  QThread 中途被回收），``cancel_all()`` 挂在 ``AppShell._on_about_to_quit``。
"""

from __future__ import annotations

import logging
import random
import re
import threading
from collections import deque

from PySide6.QtCore import QObject, QThread, Signal

log = logging.getLogger(__name__)

#: 生成等待上限（秒）：worker 侧 HTTP 超时取 min(模型配置, 这个值)——碎碎念
#: 宁可回落固定句也不让用户对着一只沉默的宠物等 60 秒。
AI_TIMEOUT_SECONDS = 12.0
#: 单句生成 token 预算：一句碎碎念足够，思考链也不许展开。
AI_MAX_TOKENS = 120
#: 台词显示长度硬上限（模型不听话时的兜底，超出部分直接截掉）。
LINE_CHAR_LIMIT = 80
#: 防重复窗口：最多带多少句「最近说过」进 prompt。
HISTORY_LIMIT = 6
#: 性格描述单条上限（config 清洗同款数字，这里只兜运行时直塞的路径）。
PERSONALITY_CHAR_LIMIT = 2000

#: 随机主题池：给模型一个「这次说点啥」的种子，配合时间/最近说过共同保证
#: 每次生成的随机性（纯靠 temperature 抖动在不同请求间常撞句）。
_THEMES = (
    "今天的工作或学习",
    "天气和季节",
    "想吃的东西",
    "现在的困意或精神头",
    "屏幕里的内容（代码/文档/网页都行）",
    "刚才冒出来的一个小念头",
    "时间过得真快",
    "想出去玩或想摸鱼",
    "夸夸自己",
    "主人在忙什么",
    "肚子饿不饿",
    "刚刚听到或看到的一点小事",
    "无所事事的发呆",
    "对主人的关心",
    "最近的一个小期待",
    "此刻周围的环境",
)

_DEFAULT_PERSONALITY = "活泼可爱、话痨、有点粘人的桌面小宠物"

_SYSTEM_TEMPLATE = (
    "你是用户桌面上的桌宠，现在要自言自语一句碎碎念。严格按下面的性格设定说话：\n"
    "{personality}\n"
    "\n"
    "输出要求：\n"
    "1. 只输出这一句话本身——不要引号、不要旁白、不要解释、不要任何前缀。\n"
    "2. 一句话，10~30 个字，口语化，可以带符合性格的语气词或口癖。\n"
    "3. 从【随机主题】里挑一个（或由此自由联想）；不要与【最近说过】的句子相同，"
    "也不要只换一两个字。\n"
    "4. 这句话是你说给自己听的，不是对用户发言；除非主题自然需要，不要提问。"
)

_USER_TEMPLATE = (
    "【当前时间】{moment}\n"
    "【随机主题】{theme}\n"
    "【最近说过（不要重复）】\n"
    "{recent}\n"
    "{trigger_note}"
    "\n"
    "请直接输出这一句碎碎念。"
)

_CLICK_NOTE = "（补充：主人刚刚戳了你一下，你可以对被戳这件事有反应。）\n"

_MOMENT_PERIODS = ((6, "凌晨"), (12, "上午"), (14, "中午"), (18, "下午"), (24, "晚上"))

_PREFIX_RE = re.compile(r"^(碎碎念|台词|自言自语|独白)[：:]\s*")
_QUOTE_CHARS = "`\"'“”‘’「」『』 \t"


def ai_enabled(cfg) -> bool:
    """AI 碎碎念开关（运行时读、不缓存，与 self_talk_image_chance 同口径）。"""
    try:
        return bool(cfg.get("self_talk_ai_enabled", False))
    except Exception:
        return False


def ai_personality(cfg) -> str:
    """用户填写的性格描述；留空视为未配置（调用方据此回落固定句池）。"""
    try:
        return str(cfg.get("self_talk_ai_personality") or "").strip()[:PERSONALITY_CHAR_LIMIT]
    except Exception:
        return ""


def sanitize_line(raw: str) -> str:
    """把模型输出清洗成一句可显示的碎碎念；不可用返回空串。

    模型常见不听话形态：裹引号/反引号、加「碎碎念：」前缀、输出多行（正文+
    解释）、收尾带空格——逐个剥掉，只留第一行并截到长度上限。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    text = text.splitlines()[0].strip() if "\n" in text.strip() else text.strip()
    text = text.strip(_QUOTE_CHARS)
    text = _PREFIX_RE.sub("", text).strip()
    return text[:LINE_CHAR_LIMIT]


def _moment_text(now=None) -> str:
    """当前时刻的人读形态（周几 + 时段 + HH:MM），给模型的时间锚点。"""
    import datetime as _dt

    now = now or _dt.datetime.now()
    period = next(label for upper, label in _MOMENT_PERIODS if now.hour < upper)
    return f"周{'一二三四五六日'[now.weekday()]} {period} {now:%H:%M}"


def build_messages(personality: str, history, *, trigger: str = "timer", now=None) -> list[dict]:
    """组装碎碎念生成请求（纯函数：随机性靠主题池 + 时间 + 随机主题选择）。"""
    personality = str(personality or "").strip()[:PERSONALITY_CHAR_LIMIT] or _DEFAULT_PERSONALITY
    recent_lines = [str(item).strip() for item in list(history)[-HISTORY_LIMIT:] if str(item).strip()]
    recent = "\n".join(f"- {item}" for item in recent_lines) if recent_lines else "（无）"
    return [
        {"role": "system", "content": _SYSTEM_TEMPLATE.format(personality=personality)},
        {
            "role": "user",
            "content": _USER_TEMPLATE.format(
                moment=_moment_text(now),
                theme=random.choice(_THEMES),
                recent=recent,
                trigger_note=_CLICK_NOTE if trigger == "click" else "",
            ),
        },
    ]


def _resolve_root_config(host):
    """找出能读 chat 设置的**真 Config**（chat 设置是全局键，不在每宠视图上）。

    宿主形态各异：PetWindow 的 ``cfg`` 本身就是真 Config（有 ``chat_settings``）；
    overlay 壳的 ``cfg`` 是主宠每宠视图、子 sprite 宿主的 ``cfg`` 是 slot 快照视图
    （``_SpriteConfig``）——两者都**没有** ``chat_settings``，直接拿去读会被
    :meth:`_provider_and_config` 的兜底吞掉并静默返回 None，表现成「AI 碎碎念
    永远不触发」（2026-11-07 实机回归的根因）。真 Config：壳上是 ``_config``，
    子宠宿主经 ``_shell`` 可达；都找不到就退回 ``host.cfg``（调用方自行兜底）。
    """
    candidates: list = []
    for attr in ("cfg", "_config", "config"):
        candidates.append(getattr(host, attr, None))
    shell = getattr(host, "_shell", None)
    if shell is not None:
        for attr in ("_config", "config", "cfg"):
            candidates.append(getattr(shell, attr, None))
    for candidate in candidates:
        if candidate is not None and callable(getattr(candidate, "chat_settings", None)):
            return candidate
    return getattr(host, "cfg", None)


def _provider_and_config(config):
    """取聊天子系统激活模型的 (provider, 请求配置)；不可用返回 None。

    返回的 ``ProviderConfig`` 是 ``chat_settings()`` 现场从配置字典新建的副本，
    按碎碎念场景改写（压超时/压 token/关思考/关联网）不影响聊天窗自己那份。
    """
    try:
        from .chat.models import ProviderConfig as _ProviderConfig  # noqa: F401  探测 no-chat 变体
        from .chat.providers import OpenAICompatibleProvider
    except Exception:  # no-chat 变体：pet.chat 被打包剔除，能力自动缺席
        return None
    try:
        settings = config.chat_settings()
        if settings is None or not getattr(settings, "enabled", False):
            return None
        pcfg = settings.active_config
    except Exception:
        # 这条静默 None 曾把「宿主视图没有 chat_settings」藏成
        # 「AI 碎碎念永远不触发」——至少留 debug 级痕迹（失败回落仍是硬要求）。
        log.debug("chat 设置读取失败：config=%r", type(config).__name__, exc_info=True)
        return None
    if not str(getattr(pcfg, "base_url", "") or "").strip():
        return None
    if not str(getattr(pcfg, "model", "") or "").strip():
        return None
    try:
        pcfg.api_key = config.resolve_api_key(pcfg)
    except Exception:
        pass  # keyring 缺失等：继续用 provider 自带的明文 key（历史兼容路径）
    pcfg.timeout = min(float(pcfg.timeout or 60.0), AI_TIMEOUT_SECONDS)
    pcfg.max_tokens = min(int(pcfg.max_tokens or 2048), AI_MAX_TOKENS)
    pcfg.thinking = "disabled"  # 碎碎念不值得付思考链延迟（DeepSeek V4 默认开思考）
    pcfg.web_search = None  # 自言自语不联网
    return OpenAICompatibleProvider(), pcfg


class _HostState:
    """每宿主一份的生成状态（惰性挂在宿主属性上，不进 config）。"""

    __slots__ = ("worker", "bridge", "history")

    def __init__(self) -> None:
        self.worker = None
        self.bridge = None
        self.history: deque = deque(maxlen=HISTORY_LIMIT)


def _state(host) -> _HostState:
    state = getattr(host, "_self_talk_ai_state", None)
    if state is None:
        state = host._self_talk_ai_state = _HostState()
    return state


class _Bridge(QObject):
    """把 worker 的完成/失败信号接回 UI 线程的最小接收器。

    receiver 必须是主线程创建的 QObject：信号到普通 Python 回调若无 QObject
    接收方，PySide6 会在发信号的 worker 线程里直接执行——气泡与宿主状态只能
    主线程碰。桥接对象强引用挂在宿主状态上，随宿主一起消亡。
    """

    done = Signal(str)
    failed = Signal()

    def __init__(self, on_done, on_failed) -> None:
        super().__init__()
        self._on_done = on_done
        self._on_failed = on_failed
        self.done.connect(self._dispatch_done)
        self.failed.connect(self._dispatch_failed)

    def _dispatch_done(self, text: str) -> None:
        try:
            self._on_done(str(text))
        except Exception:
            log.exception("AI 碎碎念落地失败")

    def _dispatch_failed(self) -> None:
        try:
            self._on_failed()
        except Exception:
            log.exception("AI 碎碎念失败回落异常")


class _Worker(QThread):
    """单次碎碎念生成的后台线程（ChatService._Worker 的极简版）。"""

    completed = Signal(str)
    failed = Signal()

    def __init__(self, provider, pcfg, messages) -> None:
        super().__init__()
        self._provider = provider
        self._pcfg = pcfg
        self._messages = messages
        self._cancel = threading.Event()
        self._responses: list = []

    def cancel(self) -> None:
        self._cancel.set()
        for resp in list(self._responses):
            try:
                resp.close()
            except Exception:
                pass

    def run(self) -> None:
        try:
            import inspect

            from .chat.providers import StreamEvent

            stream_kwargs: dict = {}
            sig = inspect.signature(self._provider.stream)
            if "response_holder" in sig.parameters:
                stream_kwargs["response_holder"] = self._responses
            parts: list[str] = []
            for text in self._provider.stream(self._messages, self._pcfg, self._cancel, **stream_kwargs):
                if self._cancel.is_set():
                    return
                if isinstance(text, StreamEvent):
                    continue  # 思考过程/联网进度：碎碎念不展示也不等它
                parts.append(text)
            if self._cancel.is_set():
                return
            result = sanitize_line("".join(parts))
            if result:
                self.completed.emit(result)
            else:
                self.failed.emit()
        except Exception:
            if not self._cancel.is_set():
                log.exception("AI 碎碎念生成失败")
                self.failed.emit()


#: 在飞 worker 强引用：宿主先死也不让 QThread 对象中途被回收
#: （"QThread: Destroyed while thread is still running" 是硬崩，不是警告）。
_LIVE_WORKERS: set = set()
_LIVE_WORKERS_LOCK = threading.Lock()


def cancel_all() -> None:
    """进程收口（AppShell._on_about_to_quit 调）：取消全部在飞生成。幂等。"""
    with _LIVE_WORKERS_LOCK:
        workers = list(_LIVE_WORKERS)
    for worker in workers:
        try:
            worker.cancel()
        except Exception:
            pass


def _release_worker(host, worker) -> None:
    state = _state(host)
    if state.worker is worker:
        state.worker = None
        state.bridge = None
    with _LIVE_WORKERS_LOCK:
        _LIVE_WORKERS.discard(worker)
    try:
        worker.deleteLater()
    except RuntimeError:
        pass


def _display(host, text: str, fallback_text: str, speak_after: bool) -> bool:
    """UI 线程落地：气泡空闲就显示 AI 句，否则丢弃（周期定时器自会排下一轮）。

    宿主形态差异全部走鸭子类型：三个宿主（PetWindow / 两处 overlay sprite
    宿主）都有 ``_show_self_talk_text`` 与 ``_bubble_blocked``，AI 模块不区分。
    """
    try:
        blocked_check = getattr(host, "_bubble_blocked", None)
        if callable(blocked_check) and blocked_check():
            return False
        show = getattr(host, "_show_self_talk_text", None)
        if not callable(show):
            return False
        line = sanitize_line(text) or sanitize_line(fallback_text)
        if not line:
            return False
        if not show(line):
            return False
        host._last_self_talk_text = line
        _state(host).history.append(line)
        if speak_after:
            from .window_alerts import speak_click_self_talk

            speak_click_self_talk(host, line)
        return True
    except RuntimeError:
        return False  # 宿主 Qt 对象已销毁：这次落地自然作废
    except Exception:
        log.exception("AI 碎碎念落地失败")
        return False


def maybe_show_ai_self_talk(host, *, fallback_text: str, speak_after: bool = False) -> bool:
    """文本自言自语的 AI 接管口：接管成功返回 True（文本稍后落地）。

    三个宿主的随机自言自语文本尾部都调这里：返回 True 表示本次由 AI 生成，
    ``host._show_self_talk_text`` 稍后被回调；返回 False 表示维持原状——调用方
    立即显示 ``fallback_text``（固定句池抽中的那句）。

    接管条件：开关开 + 性格非空 + 聊天子系统可用且激活模型已配置 + 当前宿主
    没有在飞生成（生成中再次触发直接回固定句，不排队——防请求堆积）。
    """
    cfg = getattr(host, "cfg", None)
    if cfg is None:
        return False
    personality = ai_personality(cfg)
    if not ai_enabled(cfg) or not personality:
        return False
    state = _state(host)
    if state.worker is not None and state.worker.isRunning():
        return False
    resolved = _provider_and_config(_resolve_root_config(host))
    if resolved is None:
        return False
    provider, pcfg = resolved
    trigger = "click" if speak_after else "timer"
    messages = build_messages(personality, state.history, trigger=trigger)

    worker = _Worker(provider, pcfg, messages)
    bridge = _Bridge(
        on_done=lambda text: _display(host, text, fallback_text, speak_after),
        on_failed=lambda: _display(host, "", fallback_text, speak_after),
    )
    state.worker = worker
    state.bridge = bridge
    worker.completed.connect(bridge.done)
    worker.failed.connect(bridge.failed)
    worker.finished.connect(lambda w=worker: _release_worker(host, w))
    with _LIVE_WORKERS_LOCK:
        _LIVE_WORKERS.add(worker)
    worker.start()
    return True
