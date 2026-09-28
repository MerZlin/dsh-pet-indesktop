# -*- coding: utf-8 -*-
"""全局快捷键的规则层：键名解析 / 规则清洗 / 默认表（**不 import Qt**）。

只认「修饰键 + 一个主键」这一种形状，且**必须带 Ctrl**：单键、纯 Shift 极易误触，
也容易抢别人的键。主键只认字母数字、F1~F24 与 ``HOTKEY_SPECIAL`` 里那几张——
Qt 的键值与 Windows 虚拟键码不是一套编码，宁可不注册，也不要注册成一个按不出来的键。

Win32 注册与消息接在 ``pet/hotkey_service.py``；本模块可离线单测
（见 ``tests/test_hotkey_rules.py``）。
"""
from __future__ import annotations

HOTKEY_BASE = 0xA510            # 注册用的 id 从这里起（只在桌宠自己这儿用）
HOTKEY_NOREPEAT = 0x4000        # 按住不放只算一次（不加会一直连发）
HOTKEY_ACTIONS = ("lines", "weather", "balance")
HOTKEY_LIMIT = 12

HOTKEY_MODS = {
    "ctrl": 0x0002, "control": 0x0002, "alt": 0x0001,
    "shift": 0x0004, "win": 0x0008, "meta": 0x0008,
}
HOTKEY_SPECIAL = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B,
    "escape": 0x1B, "backspace": 0x08, "del": 0x2E, "delete": 0x2E,
    "ins": 0x2D, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pgup": 0x21, "pageup": 0x21, "pgdn": 0x22, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}
HOTKEY_MIN_MODS = 0x0002        # 至少得带 Ctrl

# 会把用户正在用的编辑/复制粘贴抢走的组合：一律拒绝
HOTKEY_RESERVED = frozenset({
    "ctrl+c", "ctrl+v", "ctrl+x", "ctrl+z", "ctrl+y", "ctrl+a", "ctrl+s",
    "ctrl+f", "ctrl+p", "ctrl+w", "ctrl+t", "ctrl+n", "ctrl+r", "ctrl+shift+c",
    "ctrl+shift+v", "ctrl+alt+del",
})


def default_hotkeys() -> list[dict]:
    """内置三条：夸 / 安慰 / 损（键与台词都能在 config.json 里改）。"""
    return [
        {"id": "builtin-praise", "seq": "Ctrl+Alt+1", "act": "lines", "on": True,
         "lines": ["哇！这一下也太帅了吧", "不错嘛你，我就知道你能行"]},
        {"id": "builtin-cheer", "seq": "Ctrl+Alt+2", "act": "lines", "on": True,
         "lines": ["别急别急，慢慢来嘛，人家陪着你"]},
        {"id": "builtin-tease", "seq": "Ctrl+Alt+3", "act": "lines", "on": True,
         "lines": ["就这？……好吧，其实也还行啦"]},
    ]


def hotkey_supported_hint() -> str:
    return "支持的键：字母 / 数字 / F1~F24 / 空格、Tab、回车、Esc 这些；至少带 Ctrl。"


def normalize_seq(text) -> str:
    """把 ``"ctrl + alt + 1"`` 收拾成 ``"Ctrl+Alt+1"``（显示与比较都用它）。"""
    parts = [part.strip() for part in str(text or "").replace("＋", "+").split("+")]
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        token = part.lower()
        if token in ("ctrl", "control"):
            out.append("Ctrl")
        elif token == "alt":
            out.append("Alt")
        elif token == "shift":
            out.append("Shift")
        elif token in ("win", "meta"):
            out.append("Win")
        elif token.startswith("f") and token[1:].isdigit():
            out.append(part.upper())          # F1~F24 统一大写
        else:
            # 其余保持用户写的形状：单字母大写（a → A），特殊键原样（space / PageUp）
            out.append(part.upper() if len(part) == 1 else part)
    return "+".join(out)


def hotkey_parse(text):
    """``"Ctrl+Alt+1"`` → ``(mods, vk)``；认不出来 / 太危险回 ``None``。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    mods = 0
    vk = None
    for chunk in raw.replace("＋", "+").replace(" ", "").split("+"):
        token = chunk.strip().lower()
        if not token:
            continue
        if token in HOTKEY_MODS:
            mods |= HOTKEY_MODS[token]
        elif token in HOTKEY_SPECIAL:
            vk = HOTKEY_SPECIAL[token]
        elif len(token) == 1 and token.isalnum():
            vk = ord(token.upper())
        elif token.startswith("f") and token[1:].isdigit() and 1 <= int(token[1:]) <= 24:
            vk = 0x70 + int(token[1:]) - 1
        else:
            return None
    if vk is None or not (mods & HOTKEY_MIN_MODS):
        return None
    if normalize_seq(raw).lower() in HOTKEY_RESERVED:
        return None
    return mods, vk


def hotkey_usable(text) -> bool:
    return hotkey_parse(text) is not None


def clean_hotkeys(value, *, default_factory=default_hotkeys) -> list[dict]:
    """规则表清洗：非法键丢弃、字段补默认、按主键去重；空表回内置三条。"""
    if not isinstance(value, list):
        return default_factory()
    out: list[dict] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        seq = normalize_seq(raw.get("seq"))
        if not hotkey_usable(seq) or seq.lower() in seen:
            continue
        act = str(raw.get("act") or "lines").strip().lower()
        if act not in HOTKEY_ACTIONS:
            act = "lines"
        lines = [str(item).strip() for item in (raw.get("lines") or []) if str(item).strip()]
        if act == "lines" and not lines:
            continue
        seen.add(seq.lower())
        out.append({
            "id": str(raw.get("id") or f"hotkey-{index + 1}")[:48],
            "seq": seq, "act": act,
            "on": bool(raw.get("on", True)),
            "lines": lines,
        })
        if len(out) >= HOTKEY_LIMIT:
            break
    return out or default_factory()
