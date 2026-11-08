"""致谢/鸣谢数据的纯模型：解析、合并、搜索与分页（Qt-free）。

三份数据源：

- ``petcore/community/data/credits.json``（随包静态快照，运行期只读）
- ``<data_root>/community/credits-cache.json``（运行期拉取缓存）
- ``petcore/community/data/fans.json`` + ``<data_root>/community/fans-cache.json``

静态条目（作者账号、特别鸣谢）永远取随包文件；动态条目（贡献者、issue/PR
作者、粉丝）取「较新且非空」的一份。断网时随包快照仍可完整显示。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

#: 抓取时间缺失/损坏时的兜底比较值。
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class AuthorAccount:
    platform: str
    handle: str
    url: str = ""


@dataclass(frozen=True, slots=True)
class SpecialThanks:
    kind: str
    name: str
    url: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class Contributor:
    login: str
    contributions: int = 0


@dataclass(frozen=True, slots=True)
class ActivityAuthor:
    login: str
    issues: int = 0
    prs: int = 0

    @property
    def total(self) -> int:
        return int(self.issues) + int(self.prs)


@dataclass(frozen=True, slots=True)
class Credits:
    repo: str = ""
    fetched_at: str = ""
    author_accounts: tuple[AuthorAccount, ...] = ()
    special_thanks: tuple[SpecialThanks, ...] = ()
    contributors: tuple[Contributor, ...] = ()
    authors: tuple[ActivityAuthor, ...] = ()


@dataclass(frozen=True, slots=True)
class Fans:
    vmid: int = 0
    fetched_at: str = ""
    names: tuple[str, ...] = ()
    #: B 站口径的总人数（含重名占位）。``names`` 去重后可能比它小，
    #: 用它判断「有没有新粉丝」，不要拿唯一名字数去比。
    total: int = 0


#: 随包兜底：即使 JSON 缺失也保证作者与鸣谢区不为空。
DEFAULT_AUTHOR_ACCOUNTS: tuple[AuthorAccount, ...] = (
    AuthorAccount("BiliBili", "@張墨林"),
    AuthorAccount("BiliBili", "@快乐小咸鱼a"),
    AuthorAccount("抖音", "@張墨林"),
    AuthorAccount("GitHub", "@MerZlin", "https://github.com/MerZlin"),
    AuthorAccount("BiliBili", "@炎圣人但丁"),
)

DEFAULT_SPECIAL_THANKS: tuple[SpecialThanks, ...] = (
    SpecialThanks(
        "project",
        "PC2005-cloud/dsh-pet",
        "https://github.com/PC2005-cloud/dsh-pet",
        "本项目参考的上游项目；初始「蓝色大肥鱼」素材来源",
    ),
    SpecialThanks("bilibili", "@这个刀子真甜", "", "桌宠形象来源"),
    SpecialThanks("bilibili", "@上善无形", "", "桌宠形象来源"),
    SpecialThanks("bilibili", "@ZipZipPipe", "", "桌宠形象来源"),
    SpecialThanks("bilibili", "@浩瀚星空2005", "", "参考项目作者、初始动画素材来源"),
    SpecialThanks("bilibili", "@邦多利素世", "", "冬装大肥鱼形象作者"),
    SpecialThanks("bilibili", "@炎圣人但丁", "", "新动画素材制作者"),
    SpecialThanks("bilibili", "@玄秋沐雨", "", "手机版大肥鱼作者"),
    SpecialThanks("bilibili", "@咕噜千鹤子", "", "项目参与、部分功能开发"),
    SpecialThanks("bilibili", "@月匠", "", "余额部分功能的参考实现"),
    SpecialThanks("bilibili", "@夜宵夜吃", "", "素材制作与桌宠开发"),
    SpecialThanks("bilibili", "@小透明到处逛", "", "桌宠素材制作"),
    SpecialThanks("bilibili", "@苏辞SuTzu", "", "QQ、DSH与桌宠联动贡献者"),
    SpecialThanks("bilibili", "@貓事液體這件事", "", "Codex联动作者"),
    SpecialThanks(
        "company",
        "深度求索 DeepSeek",
        "https://www.deepseek.com/",
        "提供 DeepSeek 模型与 DeepSeek Harness，以及开源精神",
    ),
)


def clean_text(value: Any, limit: int = 200) -> str:
    text = _CONTROL.sub("", str(value or ""))
    return " ".join(text.split())[: max(1, int(limit))]


def normalize_login(value: Any) -> str:
    """GitHub 账号：去 ``@``/空白/控制字符；不改变大小写。"""
    return clean_text(str(value or "").lstrip("@"), 64)


def normalize_fan_name(value: Any) -> str:
    """B 站用户名：去控制字符、折叠空白、限长；空名返回空串。"""
    return clean_text(value, 64)


def _stamp(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return _EPOCH
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        # Old stamps without an offset used local time. Aware stamps must retain
        # their instant when bundles/caches move between machines or timezones.
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return _EPOCH


def newer(left: str, right: str) -> bool:
    """``left`` 是否不早于 ``right``（无法解析时退回字符串比较）。"""
    left_stamp, right_stamp = _stamp(left), _stamp(right)
    if left_stamp == _EPOCH or right_stamp == _EPOCH:
        return str(left or "") >= str(right or "")
    return left_stamp >= right_stamp


def age_seconds(fetched_at: str, *, now: float | None = None) -> float | None:
    """距抓取时间的秒数；时间缺失/损坏返回 None（调用方按「过期」处理）。"""
    stamp = _stamp(fetched_at)
    if stamp == _EPOCH:
        return None
    current = datetime.fromtimestamp(float(now if now is not None else time.time()), timezone.utc)
    return max(0.0, (current - stamp).total_seconds())


# ---------------------------------------------------------------- 解析


def _parse_accounts(items: Any) -> tuple[AuthorAccount, ...]:
    if not isinstance(items, list):
        return DEFAULT_AUTHOR_ACCOUNTS
    accounts: list[AuthorAccount] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        platform = clean_text(item.get("platform"), 32)
        handle = clean_text(item.get("handle"), 64)
        if not platform or not handle:
            continue
        key = (platform.casefold(), handle.casefold())
        if key in seen:
            continue
        seen.add(key)
        accounts.append(AuthorAccount(platform, handle, clean_text(item.get("url"), 300)))
    return tuple(accounts) or DEFAULT_AUTHOR_ACCOUNTS


def _parse_thanks(items: Any) -> tuple[SpecialThanks, ...]:
    if not isinstance(items, list):
        return DEFAULT_SPECIAL_THANKS
    thanks: list[SpecialThanks] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = clean_text(item.get("name"), 80)
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        thanks.append(
            SpecialThanks(
                clean_text(item.get("kind"), 24) or "other",
                name,
                clean_text(item.get("url"), 300),
                clean_text(item.get("note"), 200),
            )
        )
    return tuple(thanks) or DEFAULT_SPECIAL_THANKS


def parse_contributors(items: Any) -> tuple[Contributor, ...]:
    if not isinstance(items, list):
        return ()
    merged: dict[str, Contributor] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        login = normalize_login(item.get("login"))
        if not login or login.endswith("[bot]"):
            continue
        try:
            count = max(0, int(item.get("contributions") or 0))
        except (TypeError, ValueError):
            count = 0
        key = login.casefold()
        previous = merged.get(key)
        if previous is None or count > previous.contributions:
            merged[key] = Contributor(login, count)
    return tuple(
        sorted(merged.values(), key=lambda item: (-item.contributions, item.login.casefold()))
    )


def owner_login(repo: str) -> str:
    return normalize_login(str(repo or "").split("/", 1)[0])


def parse_authors(items: Any, *, repo: str = "") -> tuple[ActivityAuthor, ...]:
    if not isinstance(items, list):
        return ()
    owner = owner_login(repo).casefold()
    merged: dict[str, ActivityAuthor] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        login = normalize_login(item.get("login"))
        if not login or login.endswith("[bot]") or login.casefold() == owner:
            continue
        try:
            issues = max(0, int(item.get("issues") or 0))
            prs = max(0, int(item.get("prs") or 0))
        except (TypeError, ValueError):
            continue
        key = login.casefold()
        previous = merged.get(key)
        if previous is None:
            merged[key] = ActivityAuthor(login, issues, prs)
        else:
            merged[key] = ActivityAuthor(
                login, max(previous.issues, issues), max(previous.prs, prs)
            )
    return tuple(
        sorted(
            merged.values(),
            key=lambda item: (-item.total, item.login.casefold()),
        )
    )


def parse_credits(payload: Any) -> Credits:
    if not isinstance(payload, dict):
        return Credits(
            author_accounts=DEFAULT_AUTHOR_ACCOUNTS,
            special_thanks=DEFAULT_SPECIAL_THANKS,
        )
    repo = clean_text(payload.get("repo"), 120)
    return Credits(
        repo=repo,
        fetched_at=clean_text(payload.get("fetched_at"), 40),
        author_accounts=_parse_accounts(payload.get("author_accounts")),
        special_thanks=_parse_thanks(payload.get("special_thanks")),
        contributors=parse_contributors(payload.get("contributors")),
        authors=parse_authors(payload.get("authors"), repo=repo),
    )


def parse_fans(payload: Any) -> Fans:
    if not isinstance(payload, dict):
        return Fans()
    names: list[str] = []
    seen: set[str] = set()
    raw = payload.get("names")
    if isinstance(raw, list):
        for item in raw:
            name = normalize_fan_name(item)
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
    try:
        vmid = int(payload.get("vmid") or 0)
    except (TypeError, ValueError):
        vmid = 0
    try:
        total = int(payload.get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    return Fans(
        vmid=max(0, vmid),
        fetched_at=clean_text(payload.get("fetched_at"), 40),
        names=tuple(names),
        total=max(0, total, len(names)),
    )


# ---------------------------------------------------------------- 合并


def merge_credits(bundle: Credits, cached: Credits | None) -> Credits:
    """静态条目取随包，动态条目取较新且非空的一份。"""
    if cached is None:
        return bundle
    contributors = bundle.contributors
    if cached.contributors and (
        not bundle.contributors or newer(cached.fetched_at, bundle.fetched_at)
    ):
        contributors = cached.contributors
    authors = bundle.authors
    if cached.authors and (not bundle.authors or newer(cached.fetched_at, bundle.fetched_at)):
        authors = cached.authors
    fetched_at = cached.fetched_at if newer(cached.fetched_at, bundle.fetched_at) else bundle.fetched_at
    return Credits(
        repo=bundle.repo or cached.repo,
        fetched_at=fetched_at,
        author_accounts=bundle.author_accounts or DEFAULT_AUTHOR_ACCOUNTS,
        special_thanks=bundle.special_thanks or DEFAULT_SPECIAL_THANKS,
        contributors=contributors,
        authors=authors,
    )


def merge_fans(bundle: Fans, cached: Fans | None) -> Fans:
    """取 ``fetched_at`` 较新且名字非空的一份；都空则空名单。"""
    if cached is None or not cached.names:
        return bundle
    if not bundle.names or newer(cached.fetched_at, bundle.fetched_at):
        return cached
    return bundle


# ---------------------------------------------------------------- 搜索/分页


def filter_names(names: Any, query: str) -> list[str]:
    values = [str(item) for item in (names or ())]
    needle = str(query or "").strip().lstrip("@").casefold()
    if not needle:
        return values
    return [name for name in values if needle in name.lstrip("@").casefold()]


def page_count(names: Any, page_size: int) -> int:
    size = max(1, int(page_size))
    return max(1, (len(list(names or ())) + size - 1) // size)


def clamp_page(page: Any, names: Any, page_size: int) -> int:
    try:
        value = int(page)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(max(1, value), page_count(names, page_size)))


def page_slice(names: Any, page: Any, page_size: int) -> list[str]:
    values = [str(item) for item in (names or ())]
    size = max(1, int(page_size))
    current = clamp_page(page, values, size)
    start = (current - 1) * size
    return values[start : start + size]
