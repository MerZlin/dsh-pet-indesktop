"""CommunityService：刷新策略、失败保留、半截名单、凭据与签名。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from petcore.community import model, store
from petcore.community.service import CommunityService, stamp_now
from petcore.net.errors import NetworkError


class FakeCredentials:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get(self, owner, key):
        return self.data.get((str(owner), str(key)))

    def set(self, owner, key, value):
        if value:
            self.data[(str(owner), str(key))] = str(value)
        else:
            self.data.pop((str(owner), str(key)), None)


class FakeFetcher:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.contributor_rows: list[dict] = [{"login": "klxxya", "contributions": 742}]
        self.author_payload: dict = {"authors": [{"login": "reporter", "issues": 1, "prs": 0}]}
        self.contributors_error: BaseException | None = None
        self.authors_error: BaseException | None = None
        self.fan_count_value: int | None = 2
        self.fan_count_error: BaseException | None = None
        self.fans_result: dict = {"names": ["a", "b"], "total": 2, "pages": 1, "partial": False}
        self.fans_error: BaseException | None = None
        self.fans_sessdata = ""

    def contributors(self, repo):
        self.calls.append("contributors")
        if self.contributors_error is not None:
            raise self.contributors_error
        return self.contributor_rows

    def authors(self, repo):
        self.calls.append("authors")
        if self.authors_error is not None:
            raise self.authors_error
        return self.author_payload

    def fan_count(self, vmid):
        self.calls.append("fan_count")
        if self.fan_count_error is not None:
            raise self.fan_count_error
        return self.fan_count_value

    def fans(self, vmid, sessdata, progress=None):
        self.calls.append("fans")
        self.fans_sessdata = sessdata
        if self.fans_error is not None:
            raise self.fans_error
        return self.fans_result


def _bundle(
    tmp_path: Path,
    monkeypatch,
    *,
    fans_stamp: str = "",
    credits_stamp: str = "",
    names=("a", "b"),
    total: int | None = None,
):
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "credits.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "repo": "MerZlin/dsh-pet-indesktop",
                "fetched_at": credits_stamp or stamp_now(time.time() - 86400),
                "author_accounts": [{"platform": "抖音", "handle": "@張墨林"}],
                "special_thanks": [{"kind": "project", "name": "PC2005-cloud/dsh-pet"}],
                "contributors": [{"login": "old", "contributions": 1}],
                "authors": [{"login": "old-reporter", "issues": 1, "prs": 0}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    payload = {
        "vmid": 293026389,
        "fetched_at": fans_stamp or stamp_now(time.time() - 86400),
        "names": list(names),
    }
    if total is not None:
        payload["total"] = int(total)
    (bundle_dir / "fans.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(store, "bundle_dir", lambda: bundle_dir)
    return bundle_dir


def _service(tmp_path: Path, fetcher: FakeFetcher | None = None) -> CommunityService:
    return CommunityService(
        tmp_path, credentials=FakeCredentials(), fetcher=fetcher or FakeFetcher()
    )


def test_bundled_snapshot_is_visible_offline(tmp_path: Path, monkeypatch) -> None:
    bundle_dir = _bundle(tmp_path, monkeypatch)
    before = (bundle_dir / "fans.json").read_bytes()
    service = _service(tmp_path)

    assert service.fans().names == ("a", "b")
    assert service.credits().author_accounts[0].platform == "抖音"
    assert service.stale("fans") is True
    assert service.stale("credits") is True
    assert (bundle_dir / "fans.json").read_bytes() == before


def test_refresh_skips_fresh_credits_without_a_github_request(
    tmp_path: Path, monkeypatch
) -> None:
    _bundle(tmp_path, monkeypatch, credits_stamp=stamp_now(), fans_stamp=stamp_now())
    fetcher = FakeFetcher()
    service = _service(tmp_path, fetcher)

    report = service.refresh()

    assert report.credits_ok is True
    assert "contributors" not in fetcher.calls
    assert "authors" not in fetcher.calls
    assert report.counts["contributors"] == 1


def test_refresh_skips_full_fans_when_total_matches(tmp_path: Path, monkeypatch) -> None:
    _bundle(tmp_path, monkeypatch, fans_stamp=stamp_now())
    fetcher = FakeFetcher()
    fetcher.fan_count_value = 2
    service = _service(tmp_path, fetcher)

    report = service.refresh()

    assert report.credits_ok is True
    assert report.fans_ok is True
    assert report.fans_changed is False
    assert "fans" not in fetcher.calls, "总数没变 + 新鲜时不该拉 79 页"


def test_refresh_skips_full_fans_when_duplicate_names_collapse(
    tmp_path: Path, monkeypatch
) -> None:
    """真实快照 3948 个唯一名 / B 站 total 3950：判断变更必须用记录的 total。

    名字去重后天然小于 total，如果拿唯一名字数去比，每次打开关于页都会重拉 79 页。
    """
    _bundle(
        tmp_path,
        monkeypatch,
        fans_stamp=stamp_now(),
        names=("a", "b", "b"),
        total=3,
    )
    fetcher = FakeFetcher()
    fetcher.fan_count_value = 3
    service = _service(tmp_path, fetcher)

    report = service.refresh(kinds=("fans",))

    assert service.fans().names == ("a", "b")
    assert service.known_total() == 3
    assert report.fans_ok is True
    assert report.fans_changed is False
    assert "fans" not in fetcher.calls, "只有重名占位差异时不该拉 79 页"


def test_full_pull_records_bilibili_total_so_the_next_check_is_cheap(
    tmp_path: Path, monkeypatch
) -> None:
    _bundle(tmp_path, monkeypatch, fans_stamp=stamp_now(), names=("a", "b"), total=2)
    fetcher = FakeFetcher()
    fetcher.fan_count_value = 3
    fetcher.fans_result = {
        "names": ["x", "x", "y"],
        "total": 3,
        "pages": 1,
        "partial": False,
    }
    service = _service(tmp_path, fetcher)
    service.set_bilibili_cookie("SESSDATA=abc")

    first = service.refresh(kinds=("fans",))

    assert first.fans_changed is True
    assert service.fans().names == ("x", "y")
    assert service.known_total() == 3
    cached = json.loads(store.fans_cache_path(tmp_path).read_text(encoding="utf-8"))
    assert cached["total"] == 3, "缓存要记下 B 站口径总人数，不能只留去重后的名字数"

    fetcher.calls.clear()
    second = service.refresh(kinds=("fans",))

    assert second.fans_changed is False
    assert "fans" not in fetcher.calls


def test_recorded_total_follows_the_stat_endpoint(tmp_path: Path, monkeypatch) -> None:
    """比较用的数来自 `x/relation/stat`，记录的也必须是它，不能记名单接口的 total。"""
    _bundle(tmp_path, monkeypatch, fans_stamp=stamp_now(), names=("a", "b"), total=2)
    fetcher = FakeFetcher()
    fetcher.fan_count_value = 3
    fetcher.fans_result = {"names": ["a", "b"], "total": 9, "pages": 1, "partial": False}
    service = _service(tmp_path, fetcher)
    service.set_bilibili_cookie("SESSDATA=abc")

    service.refresh(kinds=("fans",))

    assert service.known_total() == 3, "该记 stat 的 follower，不是名单接口的 total"
    fetcher.calls.clear()
    service.refresh(kinds=("fans",))
    assert "fans" not in fetcher.calls, "两个接口数字不同也不该每次重拉"


def test_old_cache_without_total_heals_in_one_pull(tmp_path: Path, monkeypatch) -> None:
    """老缓存没有 total 键：允许一次全量补齐，之后必须回到便宜路径。"""
    _bundle(
        tmp_path,
        monkeypatch,
        fans_stamp=stamp_now(time.time() - 7200),
        names=("a", "b"),
        total=2,
    )
    cache = store.fans_cache_path(tmp_path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            {
                "schema": 1,
                "vmid": 293026389,
                "fetched_at": stamp_now(),
                "names": ["a", "b"],
            }
        ),
        encoding="utf-8",
    )
    fetcher = FakeFetcher()
    fetcher.fan_count_value = 3
    fetcher.fans_result = {"names": ["a", "b", "c"], "total": 3, "pages": 1, "partial": False}
    service = _service(tmp_path, fetcher)
    service.set_bilibili_cookie("SESSDATA=abc")

    first = service.refresh(kinds=("fans",))

    assert first.fans_ok is True
    assert "fans" in fetcher.calls, "缺 total 的老缓存要靠一次全量补齐"
    assert service.known_total() == 3

    fetcher.calls.clear()
    service.refresh(kinds=("fans",))

    assert "fans" not in fetcher.calls, "补齐后不该再全量"


def test_refresh_pulls_full_fans_when_total_changed(tmp_path: Path, monkeypatch) -> None:
    _bundle(tmp_path, monkeypatch, fans_stamp=stamp_now())
    fetcher = FakeFetcher()
    fetcher.fan_count_value = 3
    fetcher.fans_result = {"names": ["a", "b", "c"], "total": 3, "pages": 1, "partial": False}
    service = _service(tmp_path, fetcher)
    service.set_bilibili_cookie("SESSDATA=abc")

    report = service.refresh()

    assert "fans" in fetcher.calls
    assert fetcher.fans_sessdata == "abc"
    assert report.fans_ok is True
    assert report.fans_changed is True
    assert report.fan_total == 3
    assert service.fans().names == ("a", "b", "c")
    assert store.fans_cache_path(tmp_path).exists()


def test_failed_fans_refresh_keeps_bundled_list_and_files(
    tmp_path: Path, monkeypatch
) -> None:
    bundle_dir = _bundle(tmp_path, monkeypatch)
    before = (bundle_dir / "fans.json").read_bytes()
    fetcher = FakeFetcher()
    fetcher.fan_count_value = 3
    fetcher.fans_error = NetworkError("网络不可达", reason="network")
    service = _service(tmp_path, fetcher)
    service.set_bilibili_cookie("SESSDATA=abc")

    report = service.refresh()

    assert report.fans_ok is False
    assert report.errors["fans"] == "network"
    assert service.fans().names == ("a", "b")
    assert (bundle_dir / "fans.json").read_bytes() == before
    assert not store.fans_cache_path(tmp_path).exists()


def test_partial_fans_are_never_saved(tmp_path: Path, monkeypatch) -> None:
    bundle_dir = _bundle(tmp_path, monkeypatch)
    before = (bundle_dir / "fans.json").read_bytes()
    fetcher = FakeFetcher()
    fetcher.fan_count_value = 100
    fetcher.fans_result = {"names": ["x", "y"], "total": 100, "pages": 40, "partial": True}
    service = _service(tmp_path, fetcher)
    service.set_bilibili_cookie("SESSDATA=abc")

    report = service.refresh()

    assert report.fans_ok is False
    assert report.errors["fans"] == "timeout"
    assert service.fans().names == ("a", "b")
    assert not store.fans_cache_path(tmp_path).exists()
    assert (bundle_dir / "fans.json").read_bytes() == before


def test_refresh_without_cookie_keeps_snapshot(tmp_path: Path, monkeypatch) -> None:
    _bundle(tmp_path, monkeypatch)
    fetcher = FakeFetcher()
    fetcher.fan_count_value = 9
    service = _service(tmp_path, fetcher)

    report = service.refresh()

    assert report.fans_ok is False
    assert "fans" not in report.errors, "没有凭据不是错误，只更新免登录总数"
    assert report.fan_total == 9
    assert "fans" not in fetcher.calls, "没有凭据时不该发起需要登录的请求"
    assert service.live_fan_total()[0] == 9


def test_credits_refresh_writes_cache_and_counts(tmp_path: Path, monkeypatch) -> None:
    _bundle(tmp_path, monkeypatch)
    service = _service(tmp_path)

    report = service.refresh(kinds=("credits",))

    assert report.credits_ok is True
    assert report.counts["contributors"] == 1
    assert report.counts["authors"] == 1
    assert service.credits().contributors[0].login == "klxxya"
    assert store.credits_cache_path(tmp_path).exists()


def test_credits_failure_keeps_bundled_entries(tmp_path: Path, monkeypatch) -> None:
    _bundle(tmp_path, monkeypatch)
    fetcher = FakeFetcher()
    fetcher.contributors_error = NetworkError("限流", reason="rate_limit")
    service = _service(tmp_path, fetcher)

    report = service.refresh(kinds=("credits",))

    assert report.credits_ok is False
    assert report.errors["credits"] == "rate_limit"
    assert service.credits().contributors[0].login == "old"
    assert not store.credits_cache_path(tmp_path).exists()


def test_fan_count_failure_does_not_force_a_full_pull(tmp_path: Path, monkeypatch) -> None:
    _bundle(tmp_path, monkeypatch, fans_stamp=stamp_now())
    fetcher = FakeFetcher()
    fetcher.fan_count_error = NetworkError("超时", reason="timeout")
    service = _service(tmp_path, fetcher)

    report = service.refresh(kinds=("fans",))

    assert report.fans_ok is True
    assert report.errors.get("fan_count") == "timeout"
    assert "fans" not in fetcher.calls


def test_cookie_status_and_clear(tmp_path: Path, monkeypatch) -> None:
    _bundle(tmp_path, monkeypatch)
    service = _service(tmp_path)
    assert service.bilibili_cookie_status() is False

    service.set_bilibili_cookie("SESSDATA=abc")
    assert service.bilibili_cookie_status() is True

    service.set_bilibili_cookie("")
    assert service.bilibili_cookie_status() is False


def test_invalid_cookie_is_rejected_before_it_is_stored(tmp_path: Path, monkeypatch) -> None:
    _bundle(tmp_path, monkeypatch)
    service = _service(tmp_path)

    with pytest.raises(ValueError):
        service.set_bilibili_cookie("foo=1; b=2")

    assert service.bilibili_cookie_status() is False


def test_signature_round_trip(tmp_path: Path, monkeypatch) -> None:
    _bundle(tmp_path, monkeypatch)
    service = _service(tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 16

    path = service.save_signature(png, "墨林")

    assert path.exists() and path.read_bytes() == png
    assert service.signature_meta()["name"] == "墨林"
    assert service.clear_signature() is True
    assert not path.exists()
