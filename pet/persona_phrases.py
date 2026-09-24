# -*- coding: utf-8 -*-
"""表达风格台词渲染（data-over-code：内置文案全部外置为 JSON 预设）。

内置表达风格（dialogue_mode=legacy / whale_maid）的文案不写死在代码里，而是
各自对应仓库数据文件 ``pet/persona_presets/<mode>.json``：模块导入（启动）时
加载一次；:func:`reload_builtin_presets` 供测试与未来「重设/恢复内置」设置
入口重新读盘（当前无生产调用方）。custom 模式的台词属于用户数据，持久化在
config.json 的 ``dialogue_phrases`` 键（按 agent_key 分层的统一预设），同样
不落任何代码文件。
"""

from __future__ import annotations

from collections import defaultdict
import json
import logging
from pathlib import Path
from string import Formatter
from typing import Any, Mapping
import re


class _TemplateObject(dict):
    """Mapping that supports both ``field.key`` and ``field[key]`` syntax."""

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def _wrap_template_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _TemplateObject({str(k): _wrap_template_value(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_wrap_template_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_wrap_template_value(v) for v in value)
    return value


def _template_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Expose payload fields at top level and under ``payload``/``data``.

    Keeping the original mapping available means newly added upstream fields do
    not require another adapter change; explicit display aliases still win.
    """
    result = dict(values)
    payload = values.get("payload")
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            result.setdefault(str(key), value)
        result.setdefault("payload", payload)
        result.setdefault("data", payload)
    return {str(key): _wrap_template_value(value) for key, value in result.items()}


_FIELD_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:(?:\.([A-Za-z_][A-Za-z0-9_-]*)|\[([^\]]+)\]))*$")


def _safe_get_field(field_name: str, values: Mapping[str, Any]) -> Any:
    """Resolve only mapping keys and list indexes; never arbitrary attributes."""
    match = _FIELD_PATH_RE.fullmatch(field_name)
    if not match:
        raise KeyError(field_name)
    root_end = field_name.find(".")
    bracket = field_name.find("[")
    ends = [pos for pos in (root_end, bracket) if pos >= 0]
    root = field_name[: min(ends)] if ends else field_name
    if root not in values:
        raise KeyError(root)
    current: Any = values[root]
    rest = field_name[len(root) :]
    while rest:
        if rest.startswith("."):
            end = len(rest)
            for marker in (".", "["):
                pos = rest.find(marker, 1)
                if pos >= 0:
                    end = min(end, pos)
            key = rest[1:end]
            if not isinstance(current, Mapping) or key not in current:
                raise KeyError(key)
            current = current[key]
            rest = rest[end:]
        elif rest.startswith("["):
            end = rest.find("]", 1)
            token = rest[1:end]
            if token.isdigit():
                if not isinstance(current, (list, tuple)):
                    raise TypeError(token)
                current = current[int(token)]
            else:
                token = token.strip("'\"")
                if not isinstance(current, Mapping) or token not in current:
                    raise KeyError(token)
                current = current[token]
            rest = rest[end + 1 :]
        else:
            raise KeyError(field_name)
    return current


def render_template(template: str, values: Mapping[str, Any] | None = None, autohide=None) -> str:
    """Format templates using safe mapping/list traversal.

    Unknown or malformed placeholders remain verbatim so future upstream fields
    and existing custom phrases remain usable without exposing object attributes.

    ``autohide``：条件字段根名集合。这些字段的值缺失/为 None/为空串，或上游
    根本没提供时，占位符不原样保留而是整体隐藏（渲染为空串），并做最小
    清理（折叠重复空格、去掉空 ``/（）/() 残壳）。
    """
    values = _template_values(values or {})
    autohide = set(autohide or ())
    formatter = Formatter()
    output: list[str] = []
    hid_any = False
    try:
        for literal, field_name, format_spec, conversion in formatter.parse(str(template)):
            output.append(literal)
            if field_name is None:
                continue
            root = re.split(r"[.\[]", field_name, 1)[0]
            hide = root in autohide
            try:
                obj = _safe_get_field(field_name, values)
                if hide and (obj is None or obj == ""):
                    hid_any = True
                    continue
                if conversion:
                    obj = formatter.convert_field(obj, conversion)
                output.append(format(obj, format_spec))
            except (KeyError, IndexError, AttributeError, TypeError, ValueError):
                if hide:
                    hid_any = True
                    continue
                output.append("{" + field_name + ("!" + conversion if conversion else "") + (":" + format_spec if format_spec else "") + "}")
    except (ValueError, TypeError):
        return str(template)
    text = "".join(output)
    if hid_any:
        text = text.replace("``", "").replace("（ ）", "").replace("（）", "").replace("( )", "").replace("()", "")
        text = re.sub(r" {2,}", " ", text)
        text = text.strip()
    return text


# ---------------------------------------------------------------------------
# 内置台词预设（文案数据全部外置，代码内不保存任何台词文本）
# ---------------------------------------------------------------------------
_PRESET_DIR = Path(__file__).with_name("persona_presets")
_BUILTIN_MODES = ("legacy", "whale_maid")
_presets: dict[str, dict[str, list[str]]] = {}


def _clean_preset_events(raw: object) -> dict[str, list[str]]:
    """把磁盘 JSON 清洗成 {event: [文案…]}（文案 ≤240 字、每 key ≤8 条）。"""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, list[str]] = {}
    for key, value in raw.items():
        key = str(key).strip()
        if not key:
            continue
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            continue
        lines = [str(item).strip()[:240] for item in value if isinstance(item, str) and item.strip()]
        if lines:
            cleaned[key] = lines[:8]
    return cleaned


def _read_preset_file(mode: str) -> dict[str, list[str]]:
    path = _PRESET_DIR / f"{mode}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        logging.getLogger(__name__).warning("内置台词预设缺失，按空预设处理：%s", path)
        return {}
    except ValueError:
        logging.getLogger(__name__).warning("内置台词预设 JSON 无效，按空预设处理：%s", path)
        return {}
    return _clean_preset_events(raw)


def load_builtin_presets() -> dict[str, dict[str, list[str]]]:
    """（启动/模块导入时）读盘加载全部内置预设。

    返回读盘快照；渲染实时读全局注册表，:func:`reload_builtin_presets` 供
    测试/未来设置入口重新读盘，已持有的 PhrasePicker 无需重建。
    """
    global _presets
    _presets = {mode: _read_preset_file(mode) for mode in _BUILTIN_MODES}
    return {mode: dict(phrases) for mode, phrases in _presets.items()}


def reload_builtin_presets() -> dict[str, dict[str, list[str]]]:
    """重设入口：重新从磁盘加载内置预设（已持有的 PhrasePicker 立即生效）。"""
    return load_builtin_presets()


def builtin_phrases(mode: str) -> dict[str, list[str]]:
    """某内置模式当前的预设文案映射（未知模式 / 未加载 → 空，调用方走 fallback）。"""
    return _presets.get(str(mode or "").lower()) or {}


def phrase_for_agent(phrases: Mapping[str, Any] | None, agent_key: str, key: str) -> list[str] | None:
    """Resolve a phrase event in a unified preset (ticket 02).

    Preset shapes accepted:
    - ``{global: {key: [...]}, agents: {agent_key: {key: [...]}}}``（统一预设）
    - flat ``{key: [...]}``（旧自定义台词，等价于 global 单层）

    Resolution: ``agents[agent_key][key]`` → ``global[key]``（agent_key 为空时跳过
    agents 层，即非 Agent 场景只读 global）。找不到返回 None。
    """
    if not isinstance(phrases, dict):
        return None
    global_part = phrases.get("global") if isinstance(phrases.get("global"), dict) else None
    if agent_key:
        agents_part = phrases.get("agents") if isinstance(phrases.get("agents"), dict) else None
        if isinstance(agents_part, dict):
            agent_phrases = agents_part.get(agent_key)
            if isinstance(agent_phrases, dict) and key in agent_phrases:
                return agent_phrases[key]
    if global_part is not None:
        if key in global_part:
            return global_part[key]
        return None
    # Flat legacy preset: the whole mapping is the global layer.
    if "global" not in phrases and "agents" not in phrases:
        return phrases.get(key)
    return None


class PhrasePicker:
    """Small deterministic picker which avoids immediate repeats per phrase key."""

    def __init__(self) -> None:
        self._last: dict[str, int] = defaultdict(lambda: -1)

    def get(self, mode: str, key: str, fallback: str, autohide=None, **values) -> str:
        """Render a built-in preset phrase (legacy / whale_maid) for an event.

        mode 对应 pet/persona_presets/<mode>.json；该 key 无文案（预设缺失键 /
        未知模式 / 未加载）时原样返回 fallback。变体轮换避免同 key 连续重复。
        """
        variants = builtin_phrases(mode).get(key)
        if not variants:
            return fallback
        if isinstance(variants, str):
            variants = [variants]
        last = self._last[key]
        index = (last + 1) % len(variants)
        self._last[key] = index
        try:
            return render_template(variants[index], values, autohide=autohide)
        except (KeyError, ValueError):
            return fallback

    def custom(self, custom_phrases: dict, key: str, fallback: str, autohide=None, **values) -> str:
        """Render a custom phrase, rotating through all configured variants.

        统一走 ``phrase_for_agent`` 解析（route="" 只读 global 层）：兼容
        旧扁平 ``{key: [...]}`` 与统一预设双层 ``{global: {key: [...]},
        agents: …}`` 两种存档结构——后者若只按顶层 ``custom_phrases.get(key)``
        查会漏掉 global 层文案，让余额气泡等应用级事件误落 fallback。
        """
        raw = phrase_for_agent(custom_phrases, "", key)
        if raw is None:
            return fallback
        if isinstance(raw, list):
            variants = [str(item).strip() for item in raw if isinstance(item, str) and item.strip()]
        else:
            variants = [str(raw).strip()] if isinstance(raw, str) and raw.strip() else []
        if not variants:
            return fallback
        last = self._last[key]
        index = (last + 1) % len(variants)
        self._last[key] = index
        return render_template(variants[index], values, autohide=autohide)

    def custom_for_agent(self, phrases: Mapping[str, Any] | None, route_agent: str, key: str, fallback: str, autohide=None, **values) -> str:
        """Render a custom phrase with agent routing (ticket 02/05).

        Resolution: ``agents[route_agent][key]`` → ``global[key]``（route_agent="" 只查
        global）→ fallback。与 ``custom()`` 共享轮换与渲染语义。

        ``route_agent`` 命名避开 ``agent_key``：后者可能是渲染模板字段
        （_dialogue_context 注入），不参与路由以免关键字冲突。
        """
        raw = phrase_for_agent(phrases, route_agent, key)
        if raw is None:
            return fallback
        if isinstance(raw, list):
            variants = [str(item).strip() for item in raw if isinstance(item, str) and item.strip()]
        else:
            variants = [str(raw).strip()] if isinstance(raw, str) and raw.strip() else []
        if not variants:
            return fallback
        last = self._last[key]
        index = (last + 1) % len(variants)
        self._last[key] = index
        return render_template(variants[index], values, autohide=autohide)


def phrase_keys() -> tuple[str, ...]:
    """事件词表 = 全部内置预设文案键的并集（按数据文件驱动，无硬编码词表）。"""
    return tuple(sorted({key for phrases in _presets.values() for key in phrases}))


# Pet/桥接级「公共事件」：不随具体 Agent 归属，编辑某 Agent 专属文案层时隐藏。
# dsh.writeback.failed（写回 DSH 失败）属 Agent 操作回写，运行时按 agent_key 路由，
# 归入 Agent 专属层，不在此集合。
PUBLIC_DIALOGUE_EVENTS: frozenset[str] = frozenset(
    {
        "balance.loading",
        "balance.result",
        "bridge.install.pending",
        "bridge.install.success",
        "bridge.install.failed",
        "bridge.uninstall.failed",
        "bridge.unknown",
    }
)


def default_phrases() -> dict[str, str]:
    """每个事件的默认模板文案（取内置预设首个非空变体；legacy 优先、whale_maid 兜底）。"""
    result: dict[str, str] = {}
    for key in phrase_keys():
        for mode in ("legacy", "whale_maid"):
            variants = (_presets.get(mode) or {}).get(key) or []
            if variants:
                result[key] = variants[0]
                break
        else:
            result[key] = ""
    return result


# 启动即加载内置预设（此后可经 reload_builtin_presets() 在「重设」时重新读盘）。
load_builtin_presets()
