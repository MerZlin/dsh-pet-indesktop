"""aboutme-pets 角色目录：仓库地址、9 个角色包快照与 GitHub 目录 URL 解析。

只做字符串与静态元数据，不联网（网络在 :mod:`petcore.mods.url_install`）。
目录页 URL 形如 ``https://github.com/hunmer/aboutme-pets/tree/main/pets/gpt``；
安装器把它换成 ``raw.githubusercontent.com`` 上逐文件下载，不需要仓库提供 zip。

``PACKS`` 是上游 ``pets.json`` 的**快照**（2026-11-15）：上游新增角色不会自动
出现，但任意角色目录 URL 仍可手动粘贴安装，列表只影响「一键填入」的候选。
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlsplit

REPO_URL = "https://github.com/hunmer/aboutme-pets"
REPO_OWNER = "hunmer"
REPO_NAME = "aboutme-pets"
REPO_BRANCH = "main"
#: 常用动作（idle/move/click/drag/work + 2 个随机）最多取几段精灵表。
#: 与 `scripts/gen_aboutme_packs.py` 的 curated 口径一致：整包 100+ 张、约 150 MB，
#: 桌宠没必要为「偶尔才播一次的动作」等十几分钟。
ESSENTIAL_SHEETS = 8

#: (目录名, 展示名)：与上游 ``pets.json`` 的 ``id``/``name`` 对应。
PACKS: tuple[tuple[str, str], ...] = (
    ("dsh", "蓝毛小女仆"),
    ("dsh1", "蓝毛小女仆·新动作包"),
    ("gpt", "GPT 动作包"),
    ("claude", "Claude 动作包"),
    ("gemini", "Gemini 动作包"),
    ("qwen", "Qwen 动作包"),
    ("glm", "GLM 动作包"),
    ("grok", "Grok 动作包"),
    ("kimi", "Kimi 动作包"),
)

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
#: 段只允许 ASCII 字母数字与 ``._-``：目录名/分支名够用，且挡住 ``..`` 与编码绕过。
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_PATH_SEGMENTS = 12


def tree_url(folder: str) -> str:
    """角色目录的 GitHub 页面地址（给「打开」按钮）。"""
    name = str(folder or "").strip().strip("/")
    return f"{REPO_URL}/tree/{REPO_BRANCH}/pets/{name}"


def pack_name(folder: str) -> str:
    wanted = str(folder or "").strip().strip("/")
    for name, label in PACKS:
        if name == wanted:
            return label
    return wanted


def parse_tree_url(url: str) -> tuple[str, str, str, str] | None:
    """``github.com/<owner>/<repo>/tree/<ref>/<path…>`` → ``(owner, repo, ref, path)``。

    解析失败返回 ``None``；不拼接、不请求用户给的原始 URL（安装器只用这四个
    字段自己拼 ``raw.githubusercontent.com``，避免跳到任意主机）。
    """
    raw = str(url or "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    if parts.netloc.lower() not in _GITHUB_HOSTS:
        return None
    segments = [unquote(item) for item in parts.path.split("/") if item]
    if len(segments) < 4 or segments[2] != "tree":
        return None
    owner, repo, ref = segments[0], segments[1], segments[3]
    rest = segments[4:]
    if len(rest) > _MAX_PATH_SEGMENTS:
        return None
    if not _SEGMENT.match(owner) or not _SEGMENT.match(repo) or not _SEGMENT.match(ref):
        return None
    if not rest:
        return None
    for segment in rest:
        if not _SEGMENT.match(segment):
            return None
    return owner, repo, ref, "/".join(rest)


def is_tree_url(url: str) -> bool:
    return parse_tree_url(url) is not None


def raw_base(owner: str, repo: str, ref: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"


def raw_file_url(owner: str, repo: str, ref: str, path: str) -> str:
    """文件直链：路径段按 URL 规则转义（精灵表名是中文，不能原样塞进请求行）。"""
    return raw_base(owner, repo, ref, quote(str(path or ""), safe="/"))


def _pick_action(
    actions: list[dict],
    *,
    names: tuple[str, ...] = (),
    categories: tuple[str, ...] = (),
    exclude: set[str],
) -> str | None:
    for action in actions:
        action_id = str(action.get("id") or "")
        if not action_id or action_id in exclude:
            continue
        name = str(action.get("name") or action_id)
        category = str(action.get("category") or "")
        if any(token in name for token in names) or category in categories:
            return action_id
    return None


def essential_actions(
    manifest: dict, actions: list[dict], *, limit: int = ESSENTIAL_SHEETS
) -> list[dict]:
    """挑选常用动作（idle/move/click/drag/work + 随机），返回动作字典列表。

    与 `scripts/gen_aboutme_packs.py` 的 curated 口径一致：只下必要精灵表，
    避免为「偶尔才播一次的动作」等整包（100+ 张、约 150 MB）。缺 idle/move 时
    依次补第一个可用动作，保证打出来的角色至少能待机与移动。
    """
    items = [item for item in actions if isinstance(item, dict)]
    by_id = {str(item.get("id") or ""): item for item in items}
    picked: list[str] = []
    used: set[str] = set()

    def take(value) -> str | None:
        text = str(value or "").strip()
        if not text or text not in by_id or text in used:
            return None
        used.add(text)
        return text

    def push(action_id: str | None) -> None:
        if action_id and action_id not in picked:
            picked.append(action_id)

    idle = take(manifest.get("idleAnimationId"))
    if idle is None:
        idle = take(_pick_action(items, names=("待机", "呼吸"), exclude=used))
    push(idle)

    movement = manifest.get("movement") if isinstance(manifest.get("movement"), dict) else {}
    move = take(movement.get("leftAnimationId")) or take(movement.get("rightAnimationId"))
    if move is None:
        move = take(_pick_action(items, names=("走路", "行走", "移动"), exclude=used))
    push(move)

    click_ids = (
        manifest.get("click", {}).get("animationIds")
        if isinstance(manifest.get("click"), dict)
        else []
    )
    click = take(click_ids[0] if click_ids else None) or take(
        _pick_action(
            items, names=("点击", "互动"), categories=("互动动作",), exclude=used
        )
    )
    push(click)

    drag = take(movement.get("dragAnimationId")) or take(
        _pick_action(items, names=("拖",), exclude=used)
    )
    push(drag)
    push(
        take(
            _pick_action(
                items,
                names=("写", "工作", "记录", "敲"),
                categories=("职业动作",),
                exclude=used,
            )
        )
    )

    for action in items:
        if len(picked) >= limit:
            break
        action_id = str(action.get("id") or "")
        if action_id and action_id not in picked:
            picked.append(action_id)

    for action in items:
        if len(picked) >= 2:
            break
        action_id = str(action.get("id") or "")
        if action_id and action_id not in picked:
            picked.append(action_id)
    return [by_id[action_id] for action_id in picked[:limit] if action_id in by_id]


def safe_relative(path: str) -> str | None:
    """精灵表相对路径：拒绝绝对路径、``..``、盘符与控制字符。"""
    text = str(path or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or len(text) > 300:
        return None
    if re.match(r"^[A-Za-z]:", text):
        return None
    segments = [item for item in text.split("/") if item not in ("", ".")]
    if not segments or any(item == ".." for item in segments):
        return None
    for segment in segments:
        if any(ord(char) < 32 for char in segment):
            return None
    return "/".join(segments)


__all__ = [
    "ESSENTIAL_SHEETS",
    "PACKS",
    "REPO_BRANCH",
    "REPO_NAME",
    "REPO_OWNER",
    "REPO_URL",
    "essential_actions",
    "is_tree_url",
    "pack_name",
    "parse_tree_url",
    "raw_base",
    "raw_file_url",
    "safe_relative",
    "tree_url",
]
