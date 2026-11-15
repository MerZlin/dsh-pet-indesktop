from __future__ import annotations

import json
from pathlib import Path

import pytest

from petcore.content.legacy import build_animations, list_characters, load_character
from petcore.engine.enums import AnimatType, GraphType
from petcore.engine.geometry import HEAD_FALLBACK_RATIO

FOLDERS = ("idle", "turn", "move", "click", "drag", "random")
BODY_BOX = [100, 40, 300, 330]
HEAD_BOX = [120, 60, 280, 150]


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_character(
    base: Path,
    character_id: str = "pet1",
    *,
    folders=FOLDERS,
    manifest=None,
    move_strides=None,
    text_clips=None,
) -> Path:
    videos = base / character_id / "videos"
    for folder in folders:
        folder_dir = videos / folder
        folder_dir.mkdir(parents=True, exist_ok=True)
        (folder_dir / "x.webm").write_bytes(b"")
    if manifest is not None:
        _write_json(videos / "manifest.json", manifest)
    if move_strides is not None:
        _write_json(videos / "move_strides.json", move_strides)
    if text_clips is not None:
        _write_json(videos / "text_clips.json", text_clips)
    return base / character_id


@pytest.fixture
def character_root(tmp_path):
    base = tmp_path / "characters"
    _make_character(
        base,
        manifest={"name": "Pet One", "body_box": BODY_BOX, "head_box": HEAD_BOX},
        move_strides={"walk": 90, "run": {"stride": 150, "curve": [0.0, 0.5, 1.0]}},
        text_clips={"no_mirror": ["idle", "turn"]},
    )
    return base


def _load(base: Path, character_id: str = "pet1", **kwargs):
    kwargs.setdefault("frameseq_mode", "off")
    return load_character(character_id, root=base, **kwargs)


def _by_name(character) -> dict:
    return {spec.name: spec for spec in character.animations}


# ---------------------------------------------------------------- folder mapping


def test_folders_map_to_graph_animat_locate_and_loop(character_root):
    character = _load(character_root)
    assert character is not None
    specs = _by_name(character)

    assert set(specs) == {"x", "idle/x", "turn/x", "move/x", "drag/x", "random/x"}

    idle = specs["idle/x"]
    assert (idle.graph, idle.animat, idle.locate, idle.loop) == (
        GraphType.IDEL,
        AnimatType.B_LOOP,
        "ground",
        True,
    )

    turn = specs["turn/x"]
    assert (turn.graph, turn.animat, turn.locate, turn.loop) == (
        GraphType.MOVE,
        AnimatType.B_LOOP,
        "in_place",
        False,
    )

    move = specs["move/x"]
    assert (move.graph, move.animat, move.locate, move.loop) == (
        GraphType.MOVE,
        AnimatType.B_LOOP,
        "ground",
        False,
    )

    click = specs["x"]
    assert (click.graph, click.animat, click.locate, click.loop) == (
        GraphType.TOUCH_BODY,
        AnimatType.SINGLE,
        "ground",
        False,
    )

    drag = specs["drag/x"]
    assert (drag.graph, drag.animat, drag.locate, drag.loop) == (
        GraphType.RAISED_STATIC,
        AnimatType.SINGLE,
        "ground",
        False,
    )

    random_folder = specs["random/x"]
    assert (random_folder.graph, random_folder.animat, random_folder.locate, random_folder.loop) == (
        GraphType.DEFAULT,
        AnimatType.SINGLE,
        "ground",
        False,
    )


def test_duplicate_stems_are_disambiguated_by_folder(character_root):
    character = _load(character_root)
    specs = _by_name(character)

    # Media files are sorted by folder, so click/x.webm claims the plain name.
    assert specs["x"].path == character.root / "videos" / "click" / "x.webm"
    for spec in character.animations:
        assert spec.path is not None
        assert spec.path.exists()
        assert spec.path.suffix == ".webm"
        assert spec.frameseq_dir is None


def test_distinct_stems_keep_their_stem_as_name(tmp_path):
    base = tmp_path / "characters"
    videos = base / "pet2" / "videos" / "idle"
    videos.mkdir(parents=True)
    (videos / "breathe.webm").write_bytes(b"")

    character = _load(base, "pet2")
    assert character is not None
    assert [spec.name for spec in character.animations] == ["breathe"]
    assert character.animations[0].graph is GraphType.IDEL


def test_files_at_the_videos_root_become_default_singles(tmp_path):
    base = tmp_path / "characters"
    videos = base / "pet3" / "videos"
    videos.mkdir(parents=True)
    (videos / "top.webm").write_bytes(b"")

    character = _load(base, "pet3")
    assert character is not None
    spec = character.animations[0]
    assert spec.name == "top"
    assert spec.graph is GraphType.DEFAULT
    assert spec.animat is AnimatType.SINGLE
    assert spec.locate == "ground"
    assert spec.loop is False


def test_build_animations_matches_load_character(character_root):
    character = _load(character_root)
    assert character is not None
    videos = character.root / "videos"
    built = build_animations(videos, character.root / "frameseq", "off")
    assert built == character.animations


# ---------------------------------------------------------------- manifest / boxes


def test_manifest_boxes_are_parsed(character_root):
    character = _load(character_root)
    assert character is not None
    assert character.body_box == tuple(BODY_BOX)
    assert character.head_box == tuple(HEAD_BOX)


def test_invalid_boxes_are_ignored(tmp_path):
    base = tmp_path / "characters"
    _make_character(
        base,
        manifest={"body_box": [0, 0, 0, 0], "head_box": [1, 2, 3]},
    )
    character = _load(base)
    assert character is not None
    assert character.body_box is None
    assert character.head_box is None


def test_out_of_range_and_non_numeric_boxes_are_ignored(tmp_path):
    base = tmp_path / "characters"
    _make_character(base, "wide", manifest={"body_box": [0, 0, 641, 360]})
    _make_character(base, "tall", manifest={"body_box": [0, 0, 640, 361]})
    _make_character(base, "text", manifest={"body_box": ["a", "b", "c", "d"]})
    _make_character(base, "empty", manifest={"body_box": []})

    for character_id in ("wide", "tall", "text", "empty"):
        character = _load(base, character_id)
        assert character is not None
        assert character.body_box is None
        assert character.head_box is None


def test_box_edges_are_inclusive(tmp_path):
    base = tmp_path / "characters"
    _make_character(base, manifest={"body_box": [0, 0, 640, 360], "head_box": [0, 0, 640, 360]})
    character = _load(base)
    assert character is not None
    assert character.body_box == (0, 0, 640, 360)
    assert character.head_box == (0, 0, 640, 360)


def test_head_box_falls_back_to_the_body_box(tmp_path):
    base = tmp_path / "characters"
    _make_character(base, "absent", manifest={"body_box": BODY_BOX})
    _make_character(
        base,
        "invalid",
        manifest={"body_box": BODY_BOX, "head_box": [0, 0, 0, 0]},
    )
    x1, y1, x2, y2 = BODY_BOX
    expected_head = (x1, y1, x2, y1 + round((y2 - y1) * HEAD_FALLBACK_RATIO))
    for character_id in ("absent", "invalid"):
        character = _load(base, character_id)
        assert character is not None
        assert character.body_box == tuple(BODY_BOX)
        assert character.head_box == expected_head


def test_broken_manifest_json_falls_back_to_defaults(tmp_path):
    base = tmp_path / "characters"
    _make_character(base)
    (base / "pet1" / "videos" / "manifest.json").write_text("{not json", encoding="utf-8")

    character = _load(base)
    assert character is not None
    assert character.name == "pet1"
    assert character.body_box is None
    assert character.head_box is None


def test_manifest_at_the_character_root_is_used(tmp_path):
    base = tmp_path / "characters"
    character_dir = _make_character(base)
    _write_json(character_dir / "manifest.json", {"name": "Root Name", "body_box": BODY_BOX})

    character = _load(base)
    assert character is not None
    assert character.name == "Root Name"
    assert character.body_box == tuple(BODY_BOX)


# ---------------------------------------------------------------- extra metadata


def test_move_strides_accept_ints_and_objects(character_root):
    character = _load(character_root)
    assert character is not None
    assert character.move_strides == {"walk": 90, "run": 150}
    assert character.move_curves == {"run": (0.0, 0.5, 1.0)}


def test_move_strides_drop_invalid_entries(tmp_path):
    base = tmp_path / "characters"
    _make_character(
        base,
        move_strides={
            "walk": 90,
            "run": {"stride": 150},
            "zero": 0,
            "negative": -10,
            "flag": True,
            "text": "150",
            "bad_obj": {"stride": 0},
            "bad_obj_text": {"stride": "150"},
        },
    )
    character = _load(base)
    assert character is not None
    assert character.move_strides == {"walk": 90, "run": 150}


def test_missing_move_strides_is_an_empty_table(tmp_path):
    base = tmp_path / "characters"
    _make_character(base)
    character = _load(base)
    assert character is not None
    assert character.move_strides == {}


def test_move_segments_are_parsed_from_stride_objects(tmp_path):
    base = tmp_path / "characters"
    _make_character(
        base,
        move_strides={
            "run": {
                "stride": 150,
                "curve": [0.0, 0.5, 1.0],
                "segments": {
                    "start": 0,
                    "loop_start": 1,
                    "loop_end": 2,
                    "end": 3,
                },
            }
        },
    )
    character = _load(base)
    assert character is not None
    assert character.move_segments == {
        "run": {"start": 0, "loop_start": 1, "loop_end": 2, "end": 3}
    }


def test_move_segments_drop_invalid_values(tmp_path):
    base = tmp_path / "characters"
    _make_character(
        base,
        move_strides={
            "run": {"stride": 150, "segments": {"loop_start": -1, "loop_end": "2"}},
            "walk": {"stride": 90, "segments": {"loop_start": 1, "loop_end": 4}},
        },
    )
    character = _load(base)
    assert character is not None
    assert character.move_segments == {"walk": {"loop_start": 1, "loop_end": 4}}


def test_text_clips_no_mirror_collects_strings(character_root):
    character = _load(character_root)
    assert character is not None
    assert character.text_clips == frozenset({"idle", "turn"})


def test_text_clips_ignore_non_strings_and_missing_keys(tmp_path):
    base = tmp_path / "characters"
    _make_character(base, "flt", text_clips={"no_mirror": ["idle", 7, None, "turn"]})
    _make_character(base, "none", text_clips={})
    _make_character(base, "bad", text_clips={"no_mirror": "idle"})

    assert _load(base, "flt").text_clips == frozenset({"idle", "turn"})
    assert _load(base, "none").text_clips == frozenset()
    assert _load(base, "bad").text_clips == frozenset()


def test_character_metadata_and_phrases(character_root):
    character = _load(character_root)
    assert character is not None
    assert character.id == "pet1"
    assert character.name == "Pet One"
    assert character.root == character_root / "pet1"
    assert character.media_kind == "webm"
    assert character.owner == "legacy"
    assert character.canvas == (640, 360)
    assert character.feet_y == 330
    assert character.phrases_path is None

    (character.root / "videos" / "phrases.json").write_text("{}", encoding="utf-8")
    with_phrases = _load(character_root)
    assert with_phrases is not None
    assert with_phrases.phrases_path == with_phrases.root / "videos" / "phrases.json"


def test_character_name_falls_back_to_the_id(tmp_path):
    base = tmp_path / "characters"
    _make_character(base, manifest={})
    character = _load(base)
    assert character is not None
    assert character.name == "pet1"


def test_mixed_media_kind(tmp_path):
    base = tmp_path / "characters"
    character_dir = _make_character(base)
    (character_dir / "videos" / "idle" / "blink.gif").write_bytes(b"")

    character = _load(base)
    assert character is not None
    assert character.media_kind == "mixed"
    assert {spec.name for spec in character.animations} == {
        "x",
        "idle/x",
        "blink",
        "turn/x",
        "move/x",
        "drag/x",
        "random/x",
    }


def test_default_frameseq_mode_does_not_adopt_anything_without_a_frameseq_dir(character_root):
    character = load_character("pet1", root=character_root)
    assert character is not None
    assert all(spec.frameseq_dir is None for spec in character.animations)


# ---------------------------------------------------------------- load / list behavior


def test_missing_videos_dir_returns_none(tmp_path):
    base = tmp_path / "characters"
    base.mkdir()
    assert load_character("missing", root=base, frameseq_mode="off") is None

    (base / "empty").mkdir()
    assert load_character("empty", root=base, frameseq_mode="off") is None


def test_videos_dir_without_media_returns_none(tmp_path):
    base = tmp_path / "characters"
    videos = base / "pet1" / "videos"
    videos.mkdir(parents=True)
    (videos / "notes.txt").write_text("no media", encoding="utf-8")
    assert load_character("pet1", root=base, frameseq_mode="off") is None


def test_list_characters_returns_sorted_ids_with_media(tmp_path):
    base = tmp_path / "characters"
    _make_character(base, "zebra")
    _make_character(base, "alpha")
    (base / "no-videos").mkdir()
    (base / "empty-videos" / "videos").mkdir(parents=True)
    (base / "a-file").write_text("x", encoding="utf-8")

    nested = base / "nested" / "videos" / "deep"
    nested.mkdir(parents=True)
    (nested / "clip.webm").write_bytes(b"")

    assert list_characters(root=base) == ["alpha", "nested", "zebra"]


def test_list_characters_without_a_root_returns_empty_for_a_missing_dir(tmp_path):
    assert list_characters(root=tmp_path / "does-not-exist") == []
