"""AI 表情包目录：彩蛋路径解析、manifest、白名单取图与标记消费。"""

from __future__ import annotations

import json
import random
from pathlib import Path

from petcore import paths
from petcore.mods import meme_images


def _touch(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(b"")
    return path


def _manifest(directory: Path, items: list[dict]) -> None:
    (directory / "meme-manifest.json").write_text(
        json.dumps({"version": 1, "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_default_directory_matches_the_easter_egg_default() -> None:
    assert meme_images.directory("") == paths.legacy_asset_root() / "big_blue_fat_fish"


def test_directory_keeps_a_user_configured_path(tmp_path: Path) -> None:
    target = tmp_path / "my memes"
    target.mkdir()
    assert meme_images.directory(str(target)) == target


def test_catalog_reads_the_manifest_caption_and_tags(tmp_path: Path) -> None:
    _touch(tmp_path, "happy.gif")
    _touch(tmp_path, "sad.png")
    _manifest(
        tmp_path,
        [
            {"file": "happy.gif", "caption": "开心到转圈", "tags": ["happy", "daily"]},
            {"file": "sad.png", "caption": "无语", "keywords": ["沉默"]},
        ],
    )

    result = meme_images.catalog(tmp_path)

    assert result.total == 2
    by_name = {item.name: item for item in result.items}
    assert by_name["happy.gif"].caption == "开心到转圈"
    assert by_name["happy.gif"].tags == ("happy", "daily")
    assert by_name["sad.png"].caption == "无语"
    assert by_name["sad.png"].keywords == ("沉默",)


def test_catalog_falls_back_to_the_file_stem_without_a_manifest(tmp_path: Path) -> None:
    _touch(tmp_path, "dead_fish.jpg")
    result = meme_images.catalog(tmp_path)
    assert result.items[0].caption == "dead_fish"


def test_catalog_ignores_broken_manifest_entries_and_non_images(tmp_path: Path) -> None:
    _touch(tmp_path, "a.png")
    _touch(tmp_path, "notes.txt")
    (tmp_path / "meme-manifest.json").write_text("{broken", encoding="utf-8")
    result = meme_images.catalog(tmp_path)
    assert [item.name for item in result.items] == ["a.png"]


def test_catalog_skips_manifest_files_pointing_at_missing_images(tmp_path: Path) -> None:
    _touch(tmp_path, "a.png")
    _manifest(tmp_path, [{"file": "gone.gif", "caption": "不存在"}])
    result = meme_images.catalog(tmp_path)
    assert [item.name for item in result.items] == ["a.png"]


def test_catalog_caps_large_directories(tmp_path: Path) -> None:
    for index in range(70):
        _touch(tmp_path, f"{index:03d}.png")
    result = meme_images.catalog(tmp_path, limit=60)
    assert result.total == 70
    assert len(result.items) == 60
    assert result.items[0].name == "000.png"
    assert result.items[-1].name == "069.png"


def test_resolve_accepts_name_stem_tag_and_caption(tmp_path: Path) -> None:
    _touch(tmp_path, "dadeyaolaile.png")
    _touch(tmp_path, "sad.gif")
    _manifest(
        tmp_path,
        [
            {"file": "dadeyaolaile.png", "caption": "大肥鱼来了", "tags": ["happy"]},
            {"file": "sad.gif", "caption": "流泪", "tags": ["sad"]},
        ],
    )
    catalog = meme_images.catalog(tmp_path)

    assert meme_images.resolve("dadeyaolaile.png", catalog).name == "dadeyaolaile.png"
    assert meme_images.resolve("dadeyaolaile", catalog).name == "dadeyaolaile.png"
    assert meme_images.resolve("happy", catalog).name == "dadeyaolaile.png"
    assert meme_images.resolve("流泪", catalog).name == "sad.gif"


def test_resolve_never_accepts_a_path_outside_the_catalog(tmp_path: Path) -> None:
    _touch(tmp_path, "a.png")
    catalog = meme_images.catalog(tmp_path)
    assert meme_images.resolve("../../etc/passwd", catalog) is None
    assert meme_images.resolve(str(tmp_path / "a.png"), catalog) is None


def test_apply_meme_removes_the_marker_and_returns_the_image(tmp_path: Path) -> None:
    _touch(tmp_path, "a.png")
    _manifest(tmp_path, [{"file": "a.png", "caption": "开心", "tags": ["happy"]}])
    catalog = meme_images.catalog(tmp_path)

    text, image = meme_images.apply_meme("好耶！\n[表情: happy]", catalog, random.Random(1))

    assert text == "好耶！"
    assert image is not None and image.name == "a.png"


def test_apply_meme_falls_back_to_a_local_pick_when_the_model_forgets(tmp_path: Path) -> None:
    _touch(tmp_path, "a.png")
    catalog = meme_images.catalog(tmp_path)

    text, image = meme_images.apply_meme("今天也要加油", catalog, random.Random(1))

    assert text == "今天也要加油"
    assert image is not None and image.name == "a.png"


def test_apply_meme_without_images_leaves_the_text_alone(tmp_path: Path) -> None:
    catalog = meme_images.catalog(tmp_path)
    text, image = meme_images.apply_meme("只有文字", catalog, random.Random(1))
    assert text == "只有文字"
    assert image is None


def test_prompt_listing_mentions_the_total_and_entries(tmp_path: Path) -> None:
    _touch(tmp_path, "a.png")
    _manifest(tmp_path, [{"file": "a.png", "caption": "开心", "tags": ["happy"]}])
    catalog = meme_images.catalog(tmp_path)
    listing = meme_images.prompt_listing(catalog)
    assert "共 1 张" in listing
    assert "a.png" in listing
    assert "开心" in listing
    assert "happy" in listing
