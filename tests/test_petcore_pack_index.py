"""素材包索引解析：字段校验、https/主机白名单、跳过汇总与 by_id。"""

from __future__ import annotations

import json

import pytest

from petcore.net import packs
from petcore.net.errors import PROTOCOL, NetworkError

SHA = "0123456789abcdef" * 4
URL = "https://github.com/dsh-pet/packs/releases/download/v1/blue-fish-1.0.0.zip"


def _entry(**overrides) -> dict:
    payload = {
        "id": "blue-fish",
        "name": "蓝胖鱼",
        "version": "1.0.0",
        "filename": "blue-fish-1.0.0.zip",
        "size": 12_345,
        "sha256": SHA,
        "character": "big_blue_fat_fish",
        "urls": [URL],
    }
    payload.update(overrides)
    return payload


def _index(*entries: dict, schema: int = 1) -> dict:
    return {"schema": schema, "packs": list(entries)}


def test_parses_a_valid_index() -> None:
    index = packs.parse_index(_index(_entry()))

    assert index.schema == 1
    assert len(index.packs) == 1
    entry = index.packs[0]
    assert entry.id == "blue-fish"
    assert entry.name == "蓝胖鱼"
    assert entry.version == "1.0.0"
    assert entry.filename == "blue-fish-1.0.0.zip"
    assert entry.size == 12_345
    assert entry.sha256 == SHA.lower()
    assert entry.character == "big_blue_fat_fish"
    assert entry.urls == (URL,)
    assert entry.links == {}
    assert entry.notes == ""


def test_accepts_bytes_and_text_payloads() -> None:
    text = json.dumps(_index(_entry()))

    assert packs.parse_index(text.encode("utf-8")).packs[0].id == "blue-fish"
    assert packs.parse_index(text).packs[0].id == "blue-fish"


def test_links_and_notes_are_kept() -> None:
    entry = packs.parse_index(
        _index(_entry(links={"homepage": "https://example.com"}, notes="首版"))
    ).packs[0]

    assert entry.links == {"homepage": "https://example.com"}
    assert entry.notes == "首版"


def test_http_url_is_rejected() -> None:
    with pytest.raises(NetworkError) as caught:
        packs.parse_index(_index(_entry(urls=["http://github.com/x.zip"])))

    assert caught.value.reason == PROTOCOL
    assert "http://" not in str(caught.value)


def test_missing_sha256_is_rejected() -> None:
    payload = _entry()
    payload.pop("sha256")

    with pytest.raises(NetworkError) as caught:
        packs.parse_index(_index(payload))

    assert caught.value.reason == PROTOCOL


def test_short_sha256_is_rejected() -> None:
    with pytest.raises(NetworkError):
        packs.parse_index(_index(_entry(sha256="abc123")))


@pytest.mark.parametrize(
    "pack_id",
    ["Blue-Fish", "-fish", "a" * 65, "", "fish pack", "../fish", "fish/../x", "、鱼"],
)
def test_bad_id_is_rejected(pack_id: str) -> None:
    with pytest.raises(NetworkError) as caught:
        packs.parse_index(_index(_entry(id=pack_id)))

    assert caught.value.reason == PROTOCOL


@pytest.mark.parametrize(
    "filename",
    [
        "../evil.zip",
        "a/b.zip",
        "..\\evil.zip",
        "..",
        ".",
        "blue fish.zip",
        "evil.zip ",
        "C:evil.zip",
        "",
    ],
)
def test_path_escaping_filename_is_rejected(filename: str) -> None:
    with pytest.raises(NetworkError) as caught:
        packs.parse_index(_index(_entry(filename=filename)))

    assert caught.value.reason == PROTOCOL


@pytest.mark.parametrize("size", [0, -1, True, "12345", 1.5, None])
def test_non_positive_or_non_integer_size_is_rejected(size) -> None:
    with pytest.raises(NetworkError) as caught:
        packs.parse_index(_index(_entry(size=size)))

    assert caught.value.reason == PROTOCOL


@pytest.mark.parametrize("urls", [[], (), None, [123], ""])
def test_empty_or_broken_urls_are_rejected(urls) -> None:
    with pytest.raises(NetworkError) as caught:
        packs.parse_index(_index(_entry(urls=urls)))

    assert caught.value.reason == PROTOCOL


def test_host_outside_the_whitelist_is_rejected() -> None:
    with pytest.raises(NetworkError) as caught:
        packs.parse_index(_index(_entry(urls=["https://evil.example.com/x.zip"])))

    assert caught.value.reason == PROTOCOL


def test_the_host_whitelist_can_be_injected() -> None:
    index = packs.parse_index(
        _index(_entry(urls=["https://mirror.example.com/x.zip"])),
        hosts=("mirror.example.com",),
    )

    assert index.packs[0].urls == ("https://mirror.example.com/x.zip",)


def test_partially_invalid_index_keeps_the_valid_entries() -> None:
    index = packs.parse_index(
        _index(
            _entry(),
            _entry(id="bad", size=0),
            _entry(id="second", urls=["ftp://github.com/x.zip"]),
        )
    )

    assert [entry.id for entry in index.packs] == ["blue-fish"]


def test_index_without_any_valid_entry_raises() -> None:
    with pytest.raises(NetworkError) as caught:
        packs.parse_index(_index(_entry(size=0), _entry(id="Bad")))

    assert caught.value.reason == PROTOCOL
    assert "没有可用条目" in str(caught.value)


def test_empty_packs_list_is_not_an_error() -> None:
    index = packs.parse_index({"schema": 1, "packs": []})

    assert index.packs == ()
    assert index.by_id("blue-fish") is None


def test_by_id_returns_the_entry_or_none() -> None:
    index = packs.parse_index(_index(_entry(), _entry(id="second")))

    assert index.by_id("second") is not None
    assert index.by_id("second").id == "second"
    assert index.by_id("missing") is None


@pytest.mark.parametrize("schema", [0, 2, "1", None, True])
def test_unsupported_schema_is_rejected(schema) -> None:
    payload = _index(_entry())
    payload["schema"] = schema

    with pytest.raises(NetworkError) as caught:
        packs.parse_index(payload)

    assert caught.value.reason == PROTOCOL


def test_missing_schema_is_rejected() -> None:
    with pytest.raises(NetworkError):
        packs.parse_index({"packs": [_entry()]})


def test_malformed_json_is_rejected() -> None:
    with pytest.raises(NetworkError) as caught:
        packs.parse_index(b"{not json")

    assert caught.value.reason == PROTOCOL


def test_non_object_top_level_is_rejected() -> None:
    with pytest.raises(NetworkError) as caught:
        packs.parse_index(json.dumps([_entry()]))

    assert caught.value.reason == PROTOCOL


def test_missing_packs_list_is_rejected() -> None:
    with pytest.raises(NetworkError) as caught:
        packs.parse_index({"schema": 1})

    assert caught.value.reason == PROTOCOL


def test_duplicate_urls_are_collapsed() -> None:
    entry = packs.parse_index(_index(_entry(urls=[URL, URL]))).packs[0]

    assert entry.urls == (URL,)


# ---------------------------------------------------------------- 分片
def _part_index(parts, **overrides):
    body = {
        "schema": 1,
        "packs": [
            {
                "id": "blue-fish",
                "name": "蓝胖鱼",
                "version": "1.0.0",
                "filename": "blue-fish-1.0.0.zip",
                "size": 300,
                "sha256": "a" * 64,
                "character": "big_blue_fat_fish",
                "urls": ["https://github.com/dsh-pet/packs/releases/download/v1/blue-fish-1.0.0.zip"],
                "parts": parts,
            }
        ],
    }
    body["packs"][0].update(overrides)
    return body


def _part(name="p1.bin", size=150, sha="b" * 64, urls=None):
    return {
        "filename": name,
        "size": size,
        "sha256": sha,
        "urls": urls or [f"https://gitee.com/dsh/packs/raw/main/{name}"],
    }


def test_parses_parts_and_flag():
    index = packs.parse_index(
        _part_index([_part("p1.bin", 150), _part("p2.bin", 150)], prefer_parts=True),
        hosts=("github.com", "gitee.com"),
    )
    entry = index.packs[0]
    assert [part.filename for part in entry.parts] == ["p1.bin", "p2.bin"]
    assert entry.parts[0].size == 150
    assert entry.prefer_parts is True


def test_parts_size_must_add_up_to_the_pack():
    with pytest.raises(NetworkError) as caught:
        packs.parse_index(
            _part_index([_part("p1.bin", 100), _part("p2.bin", 100)]),
            hosts=("github.com", "gitee.com"),
        )
    assert "分片大小合计" in str(caught.value)


def test_parts_filename_must_be_safe_and_unique():
    with pytest.raises(NetworkError):
        packs.parse_index(
            _part_index([_part("../x.bin", 150), _part("p2.bin", 150)]),
            hosts=("github.com", "gitee.com"),
        )
    with pytest.raises(NetworkError):
        packs.parse_index(
            _part_index([_part("same.bin", 150), _part("same.bin", 150)]),
            hosts=("github.com", "gitee.com"),
        )


def test_parts_urls_must_be_https_and_whitelisted():
    with pytest.raises(NetworkError):
        packs.parse_index(
            _part_index([_part("p1.bin", 150, urls=["http://gitee.com/x"]), _part("p2.bin", 150)]),
            hosts=("github.com", "gitee.com"),
        )
    with pytest.raises(NetworkError):
        packs.parse_index(
            _part_index([_part("p1.bin", 150, urls=["https://evil.example.com/x"]), _part("p2.bin", 150)]),
            hosts=("github.com", "gitee.com"),
        )


def test_prefer_parts_must_be_boolean():
    with pytest.raises(NetworkError):
        packs.parse_index(
            _part_index([_part("p1.bin", 150), _part("p2.bin", 150)], prefer_parts="yes"),
            hosts=("github.com", "gitee.com"),
        )
