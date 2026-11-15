"""致谢数据的应用服务：装载、刷新、B 站凭据与签名（Qt-free）。

刷新在调用方线程执行（设置页放进守护线程）：本模块不做线程调度；只写缓存文件，
并在**所有网络请求结束后**统一 :meth:`reload` 一次内存 memo（不做半程 reload），
GUI 收到 done 后也会再 reload。失败一律收敛成稳定 reason，保留旧数据，绝不删除/
覆盖随包名单。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import model, store

logger = logging.getLogger("petcore.community")

DEFAULT_REPO = "MerZlin/dsh-pet-indesktop"
DEFAULT_VMID = 293026389
COOKIE_OWNER = "community.bilibili"
COOKIE_KEY = "sessdata"
CREDITS_TTL_S = 6 * 3600
FANS_TTL_S = 24 * 3600


@dataclass(frozen=True)
class RefreshReport:
    credits_ok: bool = False
    fans_ok: bool = False
    fan_total: int | None = None
    fans_changed: bool = False
    errors: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    fetched_at: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


class _NetFetcher:
    """默认取数：真正的网络实现只在 :mod:`petcore.net`。"""

    def contributors(self, repo: str):
        from ..net import github

        return github.fetch_contributors(repo)

    def authors(self, repo: str):
        from ..net import github

        return github.fetch_activity_authors(repo)

    def fan_count(self, vmid: int) -> int:
        from ..net import bilibili

        return bilibili.fetch_fan_count(vmid)

    def fans(self, vmid: int, sessdata: str, progress=None):
        from ..net import bilibili

        return bilibili.fetch_fans(vmid, sessdata=sessdata, progress=progress)


def stamp_now(now: float | None = None) -> str:
    moment = datetime.fromtimestamp(float(now if now is not None else time.time()))
    return moment.astimezone().isoformat(timespec="seconds")


def reason_of(error: BaseException) -> str:
    reason = getattr(error, "reason", "")
    if reason:
        return str(reason)
    from ..net.errors import classify_exception

    return str(classify_exception(error) or "network")


class CommunityService:
    def __init__(
        self,
        root: Path | str,
        *,
        credentials: Any = None,
        fetcher: Any = None,
        clock: Callable[[], float] = time.time,
        repo: str = DEFAULT_REPO,
        vmid: int = DEFAULT_VMID,
    ) -> None:
        self.root = Path(root)
        self.credentials = credentials
        self.fetcher = fetcher or _NetFetcher()
        self.clock = clock
        self.repo = str(repo)
        self.vmid = int(vmid)
        self._credits: model.Credits | None = None
        self._fans: model.Fans | None = None

    # ------------------------------------------------------------ 装载
    def reload(self) -> None:
        self._credits = None
        self._fans = None

    def credits(self) -> model.Credits:
        if self._credits is None:
            bundle = store.load_bundled_credits()
            cached = store.load_cached_credits(self.root)
            self._credits = model.merge_credits(bundle, cached)
        return self._credits

    def fans(self) -> model.Fans:
        if self._fans is None:
            bundle = store.load_bundled_fans()
            cached = store.load_cached_fans(self.root)
            self._fans = model.merge_fans(bundle, cached)
        return self._fans

    def live_fan_total(self) -> tuple[int, str] | None:
        """最近一次免登录总数读数（total, fetched_at）；没有读数返回 None。"""
        payload = store.load_fans_count(self.root)
        if not isinstance(payload, dict):
            return None
        try:
            total = int(payload.get("total") or 0)
        except (TypeError, ValueError):
            return None
        if total < 0:
            return None
        return total, str(payload.get("fetched_at") or "")

    def known_total(self) -> int:
        """已记录名单的 B 站口径人数（含同名占位），用于判断有没有新粉丝。

        与 :func:`petcore.net.bilibili.fetch_fan_count` 同源（``x/relation/stat`` 的
        ``follower``），所以两次读数可以直接比。
        """
        fans = self.fans()
        return int(fans.total) or len(fans.names)

    def stale(self, kind: str, *, now: float | None = None) -> bool:
        if kind == "credits":
            age = model.age_seconds(self.credits().fetched_at, now=now)
            return age is None or age > CREDITS_TTL_S
        age = model.age_seconds(self.fans().fetched_at, now=now)
        return age is None or age > FANS_TTL_S

    # ------------------------------------------------------------ 刷新
    def refresh(
        self,
        *,
        kinds: tuple[str, ...] = ("credits", "fans"),
        force: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> RefreshReport:
        moment = stamp_now(self.clock())
        errors: dict[str, str] = {}
        counts: dict[str, int] = {}
        credits_ok = False
        fans_ok = False
        fans_changed = False
        fan_total: int | None = None

        def report(message: str) -> None:
            if progress is not None:
                try:
                    progress(message)
                except Exception:  # noqa: BLE001 - 进度回调失败不影响刷新
                    logger.debug("community progress callback failed", exc_info=True)

        if "credits" in kinds:
            if force or self.stale("credits"):
                report("正在更新贡献者…")
                credits_ok, counts = self._refresh_credits(moment, errors, report)
            else:
                credits_ok = True
                credits = self.credits()
                counts["contributors"] = len(credits.contributors)
                counts["authors"] = len(credits.authors)

        if "fans" in kinds:
            report("正在查询粉丝总数…")
            try:
                fan_total = max(0, int(self.fetcher.fan_count(self.vmid)))
                store.save_fans_count(self.root, self.vmid, fan_total, moment)
            except Exception as exc:  # noqa: BLE001 - 总数失败不阻断完整名单策略
                errors["fan_count"] = reason_of(exc)
                fan_total = None
            current_names = self.fans().names
            current = len(current_names)
            known = self.known_total()
            need_full = bool(force) or self.stale("fans") or (
                fan_total is not None and fan_total != known
            )
            if not need_full:
                fans_ok = True
                counts["fans"] = current
                report(f"名单已是最新（共 {current} 位）")
            else:
                fans_ok, fans_changed, fans_total, fans_count = self._refresh_fans(
                    moment, errors, report, current_names, fan_total
                )
                if fans_total is not None:
                    fan_total = fans_total
                counts["fans"] = fans_count

        # 整个刷新只在最后重建一次内存 memo（不做「半程 reload」）；GUI 收到
        # done 后也会再 reload，保证它读到的是缓存文件里的最终结果。
        self.reload()
        return RefreshReport(
            credits_ok=credits_ok,
            fans_ok=fans_ok,
            fan_total=fan_total,
            fans_changed=fans_changed,
            errors=errors,
            counts=counts,
            fetched_at=moment,
        )

    def _refresh_credits(
        self,
        moment: str,
        errors: dict[str, str],
        report: Callable[[str], None],
    ) -> tuple[bool, dict[str, int]]:
        bundle = store.load_bundled_credits()
        try:
            contributors = self.fetcher.contributors(self.repo)
            activity = self.fetcher.authors(self.repo)
        except Exception as exc:  # noqa: BLE001 - 失败保留旧数据
            reason = reason_of(exc)
            errors["credits"] = reason
            logger.info("community credits refresh failed: %s", reason)
            return False, {}
        authors = activity.get("authors") if isinstance(activity, dict) else ()
        fetched = model.Credits(
            repo=bundle.repo or self.repo,
            fetched_at=moment,
            author_accounts=bundle.author_accounts,
            special_thanks=bundle.special_thanks,
            contributors=model.parse_contributors(contributors),
            authors=model.parse_authors(authors, repo=bundle.repo or self.repo),
        )
        if not fetched.contributors and not fetched.authors:
            errors["credits"] = "protocol"
            return False, {}
        try:
            store.save_cached_credits(self.root, fetched)
        except OSError:
            logger.warning("community credits cache write failed", exc_info=True)
            errors["credits"] = "unavailable"
            return False, {}
        report(f"贡献者 {len(fetched.contributors)} 位、协作者 {len(fetched.authors)} 位")
        return True, {
            "contributors": len(fetched.contributors),
            "authors": len(fetched.authors),
        }

    def _refresh_fans(
        self,
        moment: str,
        errors: dict[str, str],
        report: Callable[[str], None],
        current_names: tuple[str, ...],
        stat_total: int | None = None,
    ) -> tuple[bool, bool, int | None, int]:
        current = len(current_names)
        cookie = self._cookie()
        if not cookie:
            # 没有登录态时只更新免登录总数；不再把「未登录」当错误反复弹。
            report("未配置 B 站登录凭据，只更新粉丝总数")
            return False, False, stat_total, current

        def on_page(page: int, total_pages: int) -> None:
            report(f"正在读取粉丝第 {page}/{total_pages} 页…")

        try:
            payload = self.fetcher.fans(self.vmid, cookie, progress=on_page)
        except Exception as exc:  # noqa: BLE001 - 失败保留旧数据
            reason = reason_of(exc)
            errors["fans"] = reason
            logger.info("community fans refresh failed: %s", reason)
            return False, False, None, current
        names = payload.get("names") if isinstance(payload, dict) else None
        partial = bool(payload.get("partial")) if isinstance(payload, dict) else True
        list_total = payload.get("total") if isinstance(payload, dict) else None
        # 记「下次用来比较的那个数」：优先 x/relation/stat 的 follower（与
        # known_total() 同源），拿不到才退回名单接口的 total，再退回名字数。
        recorded = stat_total if stat_total is not None else (list_total or 0)
        fans = model.parse_fans(
            {
                "vmid": self.vmid,
                "fetched_at": moment,
                "total": recorded,
                "names": list(names or []),
            }
        )
        if not fans.names:
            errors["fans"] = "protocol"
            return False, False, list_total, current
        if partial:
            # 半截名单不能覆盖完整名单（随包或缓存），只报告超时。
            errors["fans"] = "timeout"
            report("粉丝名单没有读完，保留原名单")
            return False, False, list_total, current
        try:
            store.save_cached_fans(self.root, fans)
        except OSError:
            logger.warning("community fans cache write failed", exc_info=True)
            errors["fans"] = "unavailable"
            return False, False, list_total, current
        # 只比集合：页序（order=desc）抖动不该算「名单变了」。
        changed = set(fans.names) != set(current_names)
        report(f"粉丝名单已更新（共 {len(fans.names)} 位）")
        return (
            True,
            changed,
            int(fans.total) or len(fans.names),
            len(fans.names),
        )

    # ------------------------------------------------------------ 凭据
    def _cookie(self) -> str:
        if self.credentials is None:
            return ""
        try:
            return str(self.credentials.get(COOKIE_OWNER, COOKIE_KEY) or "").strip()
        except Exception:  # noqa: BLE001 - 钥匙串坏了按未配置
            logger.debug("community cookie read failed", exc_info=True)
            return ""

    def set_bilibili_cookie(self, value: str) -> None:
        if self.credentials is None:
            raise RuntimeError("凭据存储不可用")
        text = str(value or "").strip()
        if text:
            from ..net.bilibili import sessdata_value
            from ..net.errors import NetworkError

            try:
                text = sessdata_value(text)
            except NetworkError as exc:
                raise ValueError("B 站凭据里没有 SESSDATA") from exc
        try:
            self.credentials.set(COOKIE_OWNER, COOKIE_KEY, text)
        except Exception as exc:  # noqa: BLE001 - 钥匙串不可用要可见
            raise RuntimeError("凭据存储不可用") from exc

    def bilibili_cookie_status(self) -> bool:
        return bool(self._cookie())

    # ------------------------------------------------------------ 签名
    def signature_path(self) -> Path:
        return store.signature_path(self.root)

    def signature_meta(self) -> dict[str, str]:
        return store.load_signature_meta(self.root)

    def save_signature(self, png: bytes, name: str = "") -> Path:
        path = store.save_signature_png(self.root, png)
        store.save_signature_meta(self.root, str(name or "")[:40], stamp_now(self.clock()))
        return path

    def save_signature_name(self, name: str) -> None:
        store.save_signature_meta(self.root, str(name or "")[:40], stamp_now(self.clock()))

    def clear_signature(self) -> bool:
        return store.clear_signature(self.root)
