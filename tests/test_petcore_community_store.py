"""致谢数据模型与文件层：解析、合并、分页/搜索、缓存与签名。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from petcore.community import model, store


def _credits_payload(**overrides) -> dict:
    payload = {
        "schema": 1,
        "repo": "MerZlin/dsh-pet-indesktop",
        "fetched_at": "2026-10-27T12:00:00+08:00",
        "author_accounts": [
            {"platform": "BiliBili", "handle": "@張墨林"},
            {"platform": "抖音", "handle": "@張墨林"},
        ],
        "special_thanks": [
            {"kind": "project", "name": "PC2005-cloud/dsh-pet", "note": "素材来源"}
        ],
        "contributors": [
            {"login": "klxxya", "contributions": 742},
            {"login": "MerZlin", "contributions": 314},
        ],
        "authors": [
            {"login": "klxxya", "issues": 2, "prs": 96},
            {"login": "MerZlin", "issues": 16, "prs": 55},
            {"login": "some-bot[bot]", "issues": 99, "prs": 0},
        ],
    }
    payload.update(overrides)
    return payload


def test_parse_credits_filters_bots_and_owner() -> None:
    credits = model.parse_credits(_credits_payload())

    assert [item.login for item in credits.contributors] == ["klxxya", "MerZlin"]
    assert [item.login for item in credits.authors] == ["klxxya"], "owner/bot 不应出现在协作者里"
    assert credits.authors[0].issues == 2 and credits.authors[0].prs == 96
    assert credits.author_accounts[1].platform == "抖音"


def test_parse_credits_falls_back_to_default_static_entries() -> None:
    credits = model.parse_credits({"schema": 1})

    assert [item.handle for item in credits.author_accounts] == [
        "@張墨林",
        "@快乐小咸鱼a",
        "@張墨林",
        "@MerZlin",
        "@炎圣人但丁",
    ]
    assert len(credits.special_thanks) == 15


def test_merge_credits_prefers_newer_dynamic_entries() -> None:
    bundle = model.parse_credits(_credits_payload(fetched_at="2026-10-20T00:00:00+08:00"))
    cached = model.parse_credits(
        _credits_payload(
            fetched_at="2026-10-27T00:00:00+08:00",
            contributors=[{"login": "newcomer", "contributions": 3}],
            authors=[{"login": "reporter", "issues": 4, "prs": 0}],
        )
    )

    merged = model.merge_credits(bundle, cached)

    assert [item.login for item in merged.contributors] == ["newcomer"]
    assert [item.login for item in merged.authors] == ["reporter"]
    assert merged.author_accounts == bundle.author_accounts, "静态条目永远取随包"


def test_merge_credits_keeps_bundle_when_cache_is_older() -> None:
    bundle = model.parse_credits(_credits_payload(fetched_at="2026-10-27T00:00:00+08:00"))
    cached = model.parse_credits(
        _credits_payload(
            fetched_at="2026-10-01T00:00:00+08:00",
            contributors=[{"login": "stale", "contributions": 1}],
        )
    )

    merged = model.merge_credits(bundle, cached)

    assert [item.login for item in merged.contributors] == ["klxxya", "MerZlin"]


def test_merge_fans_prefers_newer_non_empty_and_offline_falls_back() -> None:
    bundle = model.parse_fans(
        {"vmid": 293026389, "fetched_at": "2026-10-20T00:00:00+08:00", "names": ["a", "b"]}
    )
    cached = model.parse_fans(
        {"vmid": 293026389, "fetched_at": "2026-10-27T00:00:00+08:00", "names": ["c"]}
    )

    assert model.merge_fans(bundle, cached).names == ("c",)
    assert model.merge_fans(bundle, None).names == ("a", "b")
    empty = model.parse_fans({"vmid": 293026389, "fetched_at": "2026-10-28T00:00:00+08:00"})
    assert model.merge_fans(bundle, empty).names == ("a", "b"), "空缓存不能盖掉随包名单"


def test_filter_and_pagination_are_pure() -> None:
    names = [f"粉丝{i:03d}" for i in range(125)]
    names.append("@SpecialFan")

    assert model.filter_names(names, "special") == ["@SpecialFan"]
    assert len(model.filter_names(names, "@special")) == 1
    assert model.filter_names(names, "") == names
    assert model.page_count(names, 60) == 3
    assert model.page_slice(names, 3, 60) == names[120:]
    assert model.page_slice(names, 99, 60) == names[120:], "越界页码要钳到末页"
    assert model.clamp_page(-3, names, 60) == 1


def test_cache_round_trip_and_bundle_is_never_written(tmp_path: Path, monkeypatch) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "credits.json").write_text(
        json.dumps(_credits_payload(), ensure_ascii=False), encoding="utf-8"
    )
    (bundle_dir / "fans.json").write_text(
        json.dumps({"vmid": 293026389, "fetched_at": "2026-10-20T00:00:00+08:00", "names": ["a"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(store, "bundle_dir", lambda: bundle_dir)
    before = (bundle_dir / "fans.json").read_bytes()

    credits = model.parse_credits(_credits_payload(fetched_at="2026-10-28T00:00:00+08:00"))
    fans = model.parse_fans(
        {"vmid": 293026389, "fetched_at": "2026-10-28T00:00:00+08:00", "names": ["b"]}
    )
    store.save_cached_credits(tmp_path, credits)
    store.save_cached_fans(tmp_path, fans)

    assert store.load_cached_credits(tmp_path).fetched_at == "2026-10-28T00:00:00+08:00"
    assert store.load_cached_fans(tmp_path).names == ("b",)
    assert (bundle_dir / "fans.json").read_bytes() == before, "运行期不许写随包快照"
    assert not list((tmp_path / "community").glob("*.tmp"))


def test_signature_save_overwrites_and_clear_removes_both(tmp_path: Path) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 32

    first = store.save_signature_png(tmp_path, png)
    store.save_signature_meta(tmp_path, "墨林", "2026-10-27T12:00:00+08:00")
    second = store.save_signature_png(tmp_path, png + b"y")

    assert first == second
    assert first.read_bytes() == png + b"y"
    assert store.load_signature_meta(tmp_path) == {
        "name": "墨林",
        "updated_at": "2026-10-27T12:00:00+08:00",
    }

    assert store.clear_signature(tmp_path) is True
    assert not first.exists()
    assert not store.signature_meta_path(tmp_path).exists()


def test_signature_rejects_empty_oversize_and_non_png(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        store.save_signature_png(tmp_path, b"")
    with pytest.raises(ValueError):
        store.save_signature_png(tmp_path, b"not a png")
    with pytest.raises(ValueError):
        store.save_signature_png(tmp_path, b"\x89PNG\r\n\x1a\n" + b"x" * (3 * 1024 * 1024))


def test_age_seconds_handles_missing_and_old_stamps() -> None:
    assert model.age_seconds("") is None
    assert model.age_seconds("not-a-date") is None
    age = model.age_seconds("2026-10-27T12:00:00+08:00", now=1793102400.0)
    assert age is not None and age > 0
