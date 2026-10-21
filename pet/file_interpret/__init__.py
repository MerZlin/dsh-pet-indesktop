# -*- coding: utf-8 -*-
"""拖文件解读：吃完文件后询问是否交给 AI 解析，心跳进度冒泡，摘要落会话。

边界：本模块绝不修改被拖入的文件；PR1 只直读文本类文件进 prompt（图片/
PDF 等二进制文档暂不解读，MinerU 后端另批交付）。控制器全程挂 GUI 线程——
ChatService 的信号链本身是 QueuedConnection（worker→service 已跨线程排队），
这里不需要自建 worker 线程；pet.chat 的导入必须延迟到方法内（no-chat 打包
变体 excludes=['pet.chat']，顶层导入会让设置/建窗路径整体炸）。
"""
from __future__ import annotations

import copy
import logging
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer

from ..catalog import DEFAULT_CHARACTER
from ..config import _bool_or_default, _float_or_default
from .eater import _ScanBudget

logger = logging.getLogger(__name__)

CONFIRM_ALERT_ID = "file_interpret:confirm"

# 文本类后缀：聊天附件白名单（pet/chat/widgets.py _TEXT_EXTENSIONS）∪ 常见代码/配置后缀。
_TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".json", ".csv", ".log", ".yaml", ".yml", ".xml",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".sql",
    ".sh", ".bat", ".ps1", ".ini", ".toml", ".cfg", ".conf",
    ".java", ".c", ".h", ".cpp", ".go", ".rs", ".rb",
})

# 单次解读的文本字符预算（与聊天附件 MAX_TEXT_TOTAL_CHARS 同源口径）。
_MAX_TEXT_CHARS = 200_000

_INTERVAL_DEFAULT_SECONDS = 15.0
# 控制器侧防呆区间；产品区间 [5,120] 由 Config 归一化保证（测试可用更小值驱动定时器）。
_INTERVAL_FLOOR_SECONDS = 0.05
_INTERVAL_CEIL_SECONDS = 3600.0

_INTERPRET_INSTRUCTION = (
    "请解读以上文件内容：先用两三句话概括它是什么、讲什么，再列出关键信息点；"
    "如果内容读不懂或像乱码，就直说。"
)

_PROGRESS_BUBBLE_MS = 3500

# 收集触预算却一个候选都没收到时的提示：如实说明"没看完"，不许谎称读不了（R05）。
_BUDGET_EXHAUSTED_HINT = "拖进来的文件太多，一次看不过来，少拖几个再试～"


def _progress_interval_seconds(cfg: Any) -> float:
    data = cfg.get("file_interpret") if cfg is not None else None
    raw = data.get("progress_interval_seconds") if isinstance(data, dict) else None
    return _float_or_default(raw, _INTERVAL_DEFAULT_SECONDS, _INTERVAL_FLOOR_SECONDS, _INTERVAL_CEIL_SECONDS)


def _interpret_enabled(cfg: Any) -> bool:
    data = cfg.get("file_interpret") if cfg is not None else None
    if not isinstance(data, dict):
        return True
    return _bool_or_default(data.get("enabled", True), True)


# ---------------------------------------------------------------------------
# 纯函数（headless 可测）
# ---------------------------------------------------------------------------


def is_interpretable_file(path: Any) -> bool:
    """PR1：仅文本类后缀可解读（目录/图片/二进制文档不支持）。"""
    try:
        return Path(path).suffix.lower() in _TEXT_EXTENSIONS
    except (TypeError, ValueError):
        return False


def collect_interpretable(paths: Any, budget: _ScanBudget | None = None) -> list[Path]:
    """过滤出可解读的本地文件：去重、剔目录与不存在的路径，**且在扫描预算内**。

    ``budget`` 复用投喂扫描那份 ``_ScanBudget``（不另抄一份口径）：它在每个真正
    要探盘的候选上记一次账（逐项查钟 + 文件数上限）。这里的 ``is_file()`` 与投喂
    侧一样是 GUI 线程上的同步 syscall——投喂侧一直有预算（B03），解读询问链原先
    没有，拖进一大批散装文本/慢盘路径时测量停下来了、这条链照样卡死界面（R05）。
    后缀不符或重复的候选**不记账**：它们不产生 syscall，记进去只会让一堆 .bin 把
    文本文件的额度吃光。触预算即停并置 ``budget.truncated``，调用方必须据此如实
    告诉用户"只收了前 N 个"；``budget`` 缺省自建，生产路径永远带预算。
    """
    if budget is None:
        budget = _ScanBudget()
    result: list[Path] = []
    seen: set[str] = set()
    for raw in paths or []:
        try:
            path = Path(str(raw))
        except (TypeError, ValueError):
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if not is_interpretable_file(path):
            continue
        if not budget.account():
            break
        if path.is_file():
            result.append(path)
    return result


def extract_text(paths: list[Path], max_chars: int = _MAX_TEXT_CHARS) -> tuple[str, list[str]]:
    """顺序读取文本内容，共享字符预算；超预算截断、零预算整文件跳过。

    返回 (拼装内容, 因预算耗尽或读不出被跳过的文件名列表)。读文件失败不中断
    整批（其余文件照常解读）。**有界读取**：每个文件只读剩余预算的字符数
    （``fh.read(remaining)``），绝不整文件读入——拖几百 MB 的日志时
    ``read_text`` 会把 GUI 线程卡死在解码上（#150 性能评估反馈）。
    """
    remaining = max(0, int(max_chars))
    parts: list[str] = []
    skipped: list[str] = []
    for path in paths:
        if remaining <= 0:
            skipped.append(path.name)
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                text = fh.read(remaining)
                truncated = bool(fh.read(1))
        except OSError:
            skipped.append(path.name)
            continue
        if truncated:
            text += "\n…（已截断，超出单次解读预算）"
            remaining = 0
        else:
            remaining -= len(text)
        parts.append(f"【文件：{path.name}】\n```\n{text}\n```")
    return "\n\n".join(parts), skipped


def build_interpret_user_text(content: str, names: list[str], skipped: list[str] | None = None) -> str:
    """组装发给模型的 user 消息：文件清单 + 内容 + 解读指令。"""
    lines = [f"本次拖入的文件：{'、'.join(names) or '（无）'}"]
    if skipped:
        lines.append(f"以下文件超出预算，本次未读取：{'、'.join(skipped)}")
    lines.append("")
    lines.append(content)
    lines.append("")
    lines.append(_INTERPRET_INSTRUCTION)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 控制器
# ---------------------------------------------------------------------------


class FileInterpretController(QObject):
    """拖文件解读控制器：确认 → 建会话 → 请求 → 心跳进度 → 摘要。

    只消费 win 的公共面：cfg / show_alert / resolve_alert / show_bubble，
    不触碰 PetWindow 私有成员（架构红线）。
    """

    def __init__(self, win, *, service_factory=None, store_factory=None, prompt_builder=None):
        super().__init__(win)
        self._win = win
        self._service_factory = service_factory
        self._store_factory = store_factory
        self._prompt_builder = prompt_builder
        self._service: Any = None
        self._store: Any = None
        self._state = "idle"  # idle / awaiting（确认气泡挂起）/ running（请求中）
        self._pending: list[Path] = []
        self._request_id: str | None = None
        self._chars = 0
        self._started_at = 0.0
        self._title = ""
        self._session: Any = None
        self._heartbeat = QTimer(self)
        self._heartbeat.timeout.connect(self._on_heartbeat)

    # ---------------------------------------------------------- offer 接缝
    def offer(self, paths: Any) -> None:
        """file_eater 吃完后的解读询问入口（由 install_file_interpreter 注入接线）。

        收集走扫描预算（见 ``collect_interpretable``）：两条投喂链
        （``sprite_feeding`` / legacy ``file_eater``）都从这个入口进来，所以预算对
        两条链一起生效。
        """
        if not _interpret_enabled(self._win.cfg):
            return
        if self._state == "running":
            self._bubble("正在解读上一个文件，先等等它～")
            return
        budget = _ScanBudget()
        eligible = collect_interpretable(paths, budget)
        if not eligible:
            self._bubble(_BUDGET_EXHAUSTED_HINT if budget.truncated
                         else "这些文件我读不了：目前只认文本类文件哦")
            return
        self._pending = eligible
        self._state = "awaiting"
        subtitle = "内容将发送给当前配置的 AI 模型进行解读；文件本身不会被修改或上传"
        if budget.truncated:
            subtitle += f"（文件太多，只收了前 {len(eligible)} 个）"
        names = "、".join(p.name for p in eligible[:3])
        if len(eligible) > 3:
            names += "…"
        self._win.show_alert(
            f"要解读刚吃掉的 {names} 吗？",
            subtitle=subtitle,
            sticky=True,
            buttons=[("解读", self._on_confirm), ("不用了", self._on_decline)],
            alert_id=CONFIRM_ALERT_ID,
            priority=1,
            alert_type="file_interpret",
        )

    def _on_confirm(self) -> None:
        self._win.resolve_alert(CONFIRM_ALERT_ID)
        self._start(list(self._pending))

    def _on_decline(self) -> None:
        self._win.resolve_alert(CONFIRM_ALERT_ID)
        self._state = "idle"
        self._pending = []

    # ---------------------------------------------------------- 主链路
    def _start(self, paths: list[Path]) -> None:
        try:
            from ..chat.models import ChatMessage
            from ..chat.prompt import PromptBuilder
            from ..chat.service import ChatService
            from ..chat.session_store import SessionStore
        except ModuleNotFoundError:
            logger.warning("file_interpret：pet.chat 缺失（no-chat 变体），解读不可用")
            self._state = "idle"
            self._bubble("这个版本没有 AI 对话模块，解读不可用")
            return

        settings = self._win.cfg.chat_settings()
        provider = copy.copy(settings.active_config)
        # 文件内容已随消息给出，不走联网检索（省一次检索请求和等待）
        provider.web_search = None
        provider.api_key = self._win.cfg.resolve_api_key(provider)
        if not str(provider.api_key or "").strip():
            self._state = "idle"
            self._bubble("还没配置 AI 模型的 API Key，先到设置里配好吧")
            return

        if self._service is None:
            # 工厂无参调用 + setParent：与测试替身（无 kwargs 构造）和真
            # ChatService(parent=None) 都兼容，生命周期仍随窗口销毁。
            self._service = (self._service_factory or ChatService)()
            self._service.setParent(self)
            self._service.started.connect(self._on_started)
            self._service.delta.connect(self._on_delta)
            self._service.finished.connect(self._on_finished)
            self._service.error.connect(self._on_error)
            self._service.stopped.connect(self._on_stopped)
        if self._store is None:
            self._store = (self._store_factory or SessionStore)(
                self._win.cfg.dir, getattr(self._win.cfg, "instance_id", "")
            )
        if self._prompt_builder is None:
            self._prompt_builder = PromptBuilder(
                Path(__file__).resolve().parent.parent.parent / "assets" / "characters"
            )

        character_id = str(self._win.cfg.get("character", DEFAULT_CHARACTER))
        system_prompt = self._prompt_builder.effective_system_prompt(settings, character_id)
        session = self._store.create(character_id, settings.active_provider, system_prompt)
        session.custom_title = (
            f"文件解读：{paths[0].name}" if len(paths) == 1 else f"文件解读：{len(paths)} 个文件"
        )
        self._store.save(session)

        content, skipped = extract_text(paths)
        user_text = build_interpret_user_text(content, [p.name for p in paths], skipped)
        synced, _absorbed = self._store.append_message(session, ChatMessage("user", user_text))
        if synced is None:
            session.messages.append(ChatMessage("user", user_text))
            self._store.save(session)
            self._session = session
        else:
            self._session = synced

        messages = self._prompt_builder.build_messages(
            settings, character_id, self._session.messages[:-1], user_text
        )
        self._request_id = self._service.send(messages, provider)

        self._state = "running"
        self._chars = 0
        self._started_at = time.monotonic()
        self._title = paths[0].name if len(paths) == 1 else f"{len(paths)} 个文件"
        self._heartbeat.start(int(_progress_interval_seconds(self._win.cfg) * 1000))
        self._bubble(f"开始解读《{self._title}》～读完了告诉你")

    # ---------------------------------------------------------- 请求回调
    def _on_started(self, request_id: str) -> None:
        if request_id != self._request_id:
            return

    def _on_delta(self, request_id: str, text: str) -> None:
        if request_id != self._request_id:
            return
        self._chars += len(str(text))

    def _on_finished(self, request_id: str, text: str) -> None:
        if request_id != self._request_id:
            return
        self._request_id = None
        self._heartbeat.stop()
        reply = str(text)
        from ..chat.models import ChatMessage

        synced, _absorbed = self._store.append_message(self._session, ChatMessage("assistant", reply))
        if synced is None:
            self._session.messages.append(ChatMessage("assistant", reply))
            self._store.save(self._session)
        else:
            self._session = synced
        self._state = "idle"
        duration = int(max(6000, min(20000, 4000 + len(reply) * 150)))
        self._win.show_bubble(f"解读完成：{reply}", duration_ms=duration)

    def _on_error(self, request_id: str, message: str) -> None:
        if request_id != self._request_id:
            return
        self._request_id = None
        self._heartbeat.stop()
        self._state = "idle"
        self._win.show_bubble(f"解读失败了：{message}", duration_ms=6000)

    def _on_stopped(self, request_id: str) -> None:
        if request_id != self._request_id:
            return
        self._request_id = None
        self._heartbeat.stop()
        self._state = "idle"
        self._bubble("解读已停止")

    # ---------------------------------------------------------- 心跳
    def _on_heartbeat(self) -> None:
        elapsed = max(0, int(time.monotonic() - self._started_at))
        self._bubble(
            f"正在解读《{self._title}》…（已读 {self._chars} 字 / {elapsed} 秒）",
            _PROGRESS_BUBBLE_MS,
        )

    # ---------------------------------------------------------- 工具
    def _bubble(self, text: str, duration_ms: int = 3200) -> None:
        self._win.show_bubble(text, duration_ms=duration_ms)
