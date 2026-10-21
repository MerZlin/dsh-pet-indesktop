# -*- coding: utf-8 -*-
"""档 B：回复后自动提炼记忆（静默，默认开）。

每轮助手回复完成（``ChatService._finished``，三个对话前端共用的地方）后，
后台线程做一次「记忆提炼」：最近 N 轮用户+助手文本（合计 ≤2000 字符，
N = ``chat_memory_extract_interval``，默认 5）+ 现有记忆（≤1200 字符）
→ 同一 provider 的轻量非流式小请求 → 严格 JSON 操作列表
（``[{"op":"ADD|UPDATE|FORGET","text":"..."}]``）→ ``ChatMemory.apply_ops``
本地合并（新条目 source="auto"，与档 A 的 manual 区分）。

**静默是硬要求**：无气泡、无系统通知、不打断/不延迟正常回复；提炼失败只写
日志；任何异常绝不抛出到对话路径（``schedule_extraction`` 与后台线程入口
各自兜底）。

护栏（全部在调度侧、发请求之前）：
  ① 用户文本太短（有效字符 <4，标点/emoji 不算有效字符）跳过；
  ② 30 秒限频（按记忆文件路径，上次提炼不足 30s 跳过）；
  ③ ``chat_memory_enabled`` / ``chat_memory_auto_extract`` 任一关 = 不发；
  ④ chat 模块未激活（功能模块化 ``is_active`` 既有口径）= 不发。
合并侧护栏在 ``ChatMemory.apply_ops``：按文本归一去重（与档 A 同源）、
FORGET 未命中忽略、UPDATE 未命中按 ADD。

provider 调用复用既有网络层：优先 ``OpenAICompatibleProvider._complete_json``
（联网检索同款的非流式路径），没有该方法就 drain ``stream()``——不新建网络层。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from .. import feature_activation as _fa
from .memory import ChatMemory, render_injection
from .providers import thinking_payload

log = logging.getLogger(__name__)

#: 护栏①：用户文本有效字符下限（``\w``：字母/数字/汉字；标点、emoji 不计）。
USER_TEXT_MIN_EFFECTIVE_CHARS = 4
#: 护栏②：同一记忆文件两次提炼的最小间隔（成本控制，进程内口径）。
EXTRACT_INTERVAL_SECONDS = 30.0
#: 本轮用户 / 助手文本各自截断上限；拼接后的近期对话整体再截到总预算。
_DIALOGUE_PART_CHARS = 1000
_DIALOGUE_TOTAL_CHARS = 2000
#: 现有记忆注入提炼 prompt 的上限（render_injection 自带硬上限 1200）。
#: 提炼响应阅读上限：超出按无操作处理（解析预算）。
_RESPONSE_CHAR_LIMIT = 4000
#: 单次合并的操作数 / 单条操作文本上限（超预算截断）。
_MAX_OPS = 8
_OP_TEXT_CHARS = 200

_VALID_OPS = ("ADD", "UPDATE", "FORGET")
_EFFECTIVE_RE = re.compile(r"\w", re.UNICODE)

#: 限频状态：记忆文件路径 → 上次提炼的 monotonic 时间（进程内，不入盘）。
_last_run: dict[str, float] = {}
_turn_count: dict[str, int] = {}
#: 最近 N 轮待提炼对话缓冲：轮次闸放行前的轮次不丢（审查实测「每 5 轮只提
#: 第 5 轮、前 4 轮被丢」的修复），到点合并成一份近期对话一起提炼。
_pending_turns: dict[str, list[tuple[str, str]]] = {}
_last_run_lock = threading.Lock()

#: 「每 N 轮回复提炼一次」的默认 N（共创者定稿 5：成本≈1/5，记忆延迟无感）
DEFAULT_EXTRACT_EVERY_TURNS = 5


def _extract_every_turns(config) -> int:
    """提炼频率（每 N 轮一次）：非法值回落默认，夹到 [1, 20]。"""
    try:
        n = int(config.get("chat_memory_extract_interval", DEFAULT_EXTRACT_EVERY_TURNS))
    except (TypeError, ValueError):
        return DEFAULT_EXTRACT_EVERY_TURNS
    return max(1, min(20, n))

_EXTRACT_SYSTEM = (
    "你是记忆整理助手。从「用户和桌宠的最近几轮对话」中挑出值得长期记住的关于用户的事实"
    "（偏好、习惯、明确说过的个人信息、约定），输出严格的 JSON 操作列表。\n"
    "规则：\n"
    "1. 只输出 JSON 数组本身，不要输出任何其它文字、解释或 Markdown 代码围栏。\n"
    "2. 每条操作形如 {\"op\":\"ADD\",\"text\":\"...\"}；op 只能是 ADD / UPDATE / FORGET。\n"
    "3. 只记关于「用户」的长期事实；寒暄、闲聊、一次性请求、关于桌宠的事实都不记。\n"
    "4. text 是一句简短的第一人称陈述（与已有记忆风格一致，如「我不吃辣」），不超过 50 字。\n"
    "5. 已有记忆里已有同义事实时不要重复 ADD；事实有变化用 UPDATE；用户明确表示"
    "「别记了 / 忘掉」用 FORGET（text 填要删掉的那条已有记忆）。\n"
    "6. 没有值得记住的内容时，输出 []。"
)

_EXTRACT_USER = (
    "【近期对话】\n{dialogue}\n\n"
    "【已有记忆】\n{existing}\n\n"
    "请输出记忆操作 JSON 数组。"
)


def parse_ops(raw: str) -> list[dict[str, str]]:
    """容错解析提炼输出为操作列表；解析失败 / 超预算按无操作（[]）处理。

    先整串 ``json.loads``，失败再截取首个 ``[`` 到末个 ``]``（模型在 JSON 前后
    包了废话的形态）；逐项校验：必须是 dict、op 在 ADD/UPDATE/FORGET 内、text
    非空（strip 后，截到单条上限）；操作总数截到 _MAX_OPS。
    """
    text = str(raw or "").strip()[:_RESPONSE_CHAR_LIMIT]
    if not text:
        return []
    candidates = [text]
    start, end = text.find("["), text.rfind("]")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if not isinstance(data, list):
            continue
        ops = []
        for item in data[:_MAX_OPS]:
            if not isinstance(item, dict):
                continue
            op = str(item.get("op", "")).strip().upper()
            op_text = str(item.get("text", "")).strip()[:_OP_TEXT_CHARS]
            if op in _VALID_OPS and op_text:
                ops.append({"op": op, "text": op_text})
        return ops
    return []


def _effective_chars(text: str) -> int:
    """有效字符数：\\w（字母/数字/汉字）计数；纯标点/emoji/空白为 0。"""
    return len(_EFFECTIVE_RE.findall(str(text or "")))


def schedule_extraction(
    *,
    config,
    memory_path: Path | str,
    user_text: str,
    reply_text: str,
    provider,
    provider_config,
) -> bool:
    """护栏全过后在**后台线程**安排一次记忆提炼；返回这次有没有真的安排上。

    本函数只做微秒级的同步护栏检查，真正的读盘 / 网络 / 合并全在后台线程——
    对话路径不被提炼阻塞。任何异常只写日志，绝不抛给对话路径（静默硬要求）。
    """
    try:
        if not _fa.is_active("chat"):
            return False
        if not bool(config.get("chat_memory_enabled", True)):
            return False
        if not bool(config.get("chat_memory_auto_extract", True)):
            return False
        user_text = str(user_text or "").strip()
        reply_text = str(reply_text or "").strip()
        if _effective_chars(user_text) < USER_TEXT_MIN_EFFECTIVE_CHARS:
            return False
        if not reply_text:
            return False
        path = Path(memory_path)
        key = str(path)
        now = time.monotonic()
        with _last_run_lock:
            # 轮次闸：每 N 轮回复才提炼一次（默认 5）——成本约为每轮提炼的 1/N。
            # 闸内的轮次进缓冲不丢，到点把最近 N 轮合在一起提（修复前只提
            # 当轮、前 N-1 轮被静默丢弃）。
            count = _turn_count.get(key, 0) + 1
            _turn_count[key] = count
            n = _extract_every_turns(config)
            buf = _pending_turns.setdefault(key, [])
            buf.append((user_text, reply_text))
            del buf[:-n]
            if count % n != 0:
                return False
            if now - _last_run.get(key, -1e18) < EXTRACT_INTERVAL_SECONDS:
                # 限频未过：本轮留在缓冲里等下一闸窗——但 del buf[:-n] 的裁剪
                # 仍在执行，闸窗内聊得比 N 轮多时更早轮次会被挤掉（成本取舍，
                # 不是「全部留住一起提」；k3 终审 F3）
                return False
            _last_run[key] = now
            turns = list(buf)
            buf.clear()
        thread = threading.Thread(
            target=_run_safely,
            args=(path, turns, provider, provider_config),
            daemon=True,
            name="chat-memory-extract",
        )
        thread.start()
        return True
    except Exception:
        log.exception("记忆提炼调度失败（已静默）: %s", memory_path)
        return False


def _run_safely(path: Path, turns: list[tuple[str, str]], provider, provider_config) -> None:
    """后台线程入口：提炼失败只写日志，异常绝不穿出线程。"""
    try:
        _run(path, turns, provider, provider_config)
    except Exception:
        log.exception("记忆提炼失败（已静默）: %s", path)


def _dialogue_transcript(turns: list[tuple[str, str]]) -> str:
    """把最近 N 轮拼成一份近期对话：每轮用户/助手各自截断，整体再按总预算
    左截（留最新）。空轮在入缓冲前已被调度侧过滤，这里不重复检查。"""
    parts = []
    for index, (user, reply) in enumerate(turns, 1):
        user, reply = user[-_DIALOGUE_PART_CHARS:], reply[-_DIALOGUE_PART_CHARS:]
        parts.append(f"第{index}轮\n用户：{user}\n桌宠：{reply}")
    return "\n\n".join(parts)[-_DIALOGUE_TOTAL_CHARS:]


def _run(path: Path, turns: list[tuple[str, str]], provider, provider_config) -> None:
    memory = ChatMemory(path)
    existing = render_injection(memory.load())  # 自带 ≤1200 字符硬上限
    prompt = _EXTRACT_USER.format(
        dialogue=_dialogue_transcript(turns),
        existing=existing or "（空）",
    )
    messages = [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    raw = _complete(provider, messages, provider_config)
    ops = parse_ops(raw)
    if ops:
        memory.apply_ops(ops, source="auto")


def _complete(provider, messages: list[dict[str, Any]], config) -> str:
    """轻量非流式调用，复用既有网络层（不新建）：优先 provider 的
    ``_complete_json``（联网检索同款），没有就 drain ``stream()``。"""
    complete_json = getattr(provider, "_complete_json", None)
    if callable(complete_json):
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "stream": False,
            "max_tokens": 512,
            "temperature": 0.2,
        }
        payload.update(thinking_payload(config, force_disable=True))
        message = complete_json(payload, config, None)
        content = message.get("content") if isinstance(message, dict) else ""
        return str(content or "")[:_RESPONSE_CHAR_LIMIT]
    cancel = threading.Event()
    parts: list[str] = []
    used = 0
    for chunk in provider.stream(messages, config, cancel):
        if not isinstance(chunk, str):
            continue  # StreamEvent（reasoning/status）：提炼只看正文
        parts.append(chunk)
        used += len(chunk)
        if used >= _RESPONSE_CHAR_LIMIT:
            cancel.set()
            break
    return "".join(parts)[:_RESPONSE_CHAR_LIMIT]
