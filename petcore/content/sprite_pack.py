"""把 aboutme-pets 的 WebP 精灵表目录导入成 content mod。

只读源目录、只写目标 mod；不联网、不修改源素材。动作按名字/分类映射到
idle/move/click/drag/turn/work/random 七个目录，运行时由 `legacy` 的
`clips` 清单直接读精灵表。
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ..mods.manifest import is_valid_id

_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_LICENSE_FILES = (
    "NOTICE.txt",
    "LICENSE-assets.txt",
    "LICENSE-code.txt",
    "SOURCE-upstream.txt",
    "BATCH-source.txt",
)


def _safe_name(value: str) -> str:
    text = _BAD_NAME.sub("_", str(value or "")).strip(" .")
    return text[:80] or "clip"


def _read_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def character_id_for(value: str) -> str:
    """把包内 id 清洗成可用的角色 id；不安全/空值返回空串。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    text = _safe_name(raw.removesuffix("-pet"))
    return text if text and is_valid_id(f"character.{text}") else ""


def _actions(manifest: dict, actions_doc: dict) -> list[dict]:
    items = actions_doc.get("animations")
    if not isinstance(items, list):
        items = manifest.get("animations")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _folder_for(action: dict, manifest: dict) -> str:
    action_id = str(action.get("id") or "")
    name = str(action.get("name") or action_id)
    category = str(action.get("category") or "")
    movement = manifest.get("movement") if isinstance(manifest.get("movement"), dict) else {}
    if action_id and action_id == str(manifest.get("idleAnimationId") or ""):
        return "idle"
    if action_id and action_id in {
        str(movement.get("leftAnimationId") or ""),
        str(movement.get("rightAnimationId") or ""),
    }:
        return "move"
    if action_id and action_id == str(movement.get("dragAnimationId") or ""):
        return "drag"
    click_ids = manifest.get("click", {}).get("animationIds") if isinstance(manifest.get("click"), dict) else []
    if action_id and action_id in {str(item) for item in click_ids or []}:
        return "click"
    if any(token in name for token in ("工作", "写", "记录", "敲")) or category == "职业动作":
        return "work"
    if any(token in name for token in ("拖",)):
        return "drag"
    if any(token in name for token in ("点击", "互动")) or category == "互动动作":
        return "click"
    if any(token in name for token in ("转",)):
        return "turn"
    return "random"


def _sheet_fields(action: dict) -> dict:
    sheet = action.get("spritesheet") if isinstance(action.get("spritesheet"), dict) else {}
    try:
        frame_w = int(sheet.get("frameWidth") or 0)
        frame_h = int(sheet.get("frameHeight") or 0)
        columns = int(sheet.get("columns") or 0)
        rows = int(sheet.get("rows") or 0)
        count = int(sheet.get("frameCount") or 0)
        fps = float(sheet.get("fps") or 24.0)
    except (TypeError, ValueError):
        return {}
    if min(frame_w, frame_h, columns, rows, count) <= 0:
        return {}
    return {
        "frame_w": frame_w,
        "frame_h": frame_h,
        "columns": columns,
        "rows": rows,
        "frame_count": min(count, columns * rows),
        "fps": fps if fps > 0 else 24.0,
    }


def _region_box(regions, key: str, canvas: tuple[int, int]) -> list[int] | None:
    width, height = canvas
    for region in regions if isinstance(regions, list) else []:
        if str(region.get("id") or "") != key:
            continue
        try:
            x = float(region.get("x") or 0.0) * width
            y = float(region.get("y") or 0.0) * height
            w = float(region.get("width") or 0.0) * width
            h = float(region.get("height") or 0.0) * height
        except (TypeError, ValueError):
            return None
        if w > 1 and h > 1:
            return [int(round(x)), int(round(y)), int(round(x + w)), int(round(y + h))]
    return None


def build_sprite_pack(
    source: Path,
    out: Path,
    *,
    character_id: str | None = None,
) -> tuple[bool, str]:
    """把 aboutme 单角色目录转成 `character.<id>`；返回 (ok, message)。"""
    source = Path(source)
    if not source.is_dir():
        return False, f"源目录不存在：{source}"
    manifest = _read_json(source / "manifest.json")
    actions_doc = _read_json(source / "actions.json")
    actions = _actions(manifest, actions_doc)
    if not manifest or not actions:
        return False, "缺少 manifest.json / actions.json（aboutme-pets 单角色目录）"

    raw_id = str(character_id or manifest.get("id") or source.name)
    character_id = character_id_for(raw_id)
    if not character_id:
        return False, f"角色 id 不合法：{raw_id!r}"

    out = Path(out)
    if out.exists() and any(out.iterdir()):
        return False, f"目标目录非空：{out}"
    videos = out / "videos"
    videos.mkdir(parents=True, exist_ok=True)

    canvas = (640, 360)
    try:
        canvas = (
            int(manifest.get("canvasWidth") or 640),
            int(manifest.get("canvasHeight") or 360),
        )
    except (TypeError, ValueError):
        pass

    clips: dict[str, dict] = {}
    used_names: set[str] = set()
    copied = 0
    for action in actions:
        sheet = _sheet_fields(action)
        if not sheet:
            continue
        source_rel = action.get("sources", {}).get("webp") if isinstance(action.get("sources"), dict) else ""
        if not source_rel:
            continue
        sheet_path = (source / str(source_rel)).resolve()
        try:
            sheet_path.relative_to(source.resolve())
        except ValueError:
            continue
        if not sheet_path.is_file():
            continue
        folder = _folder_for(action, manifest)
        name = str(action.get("name") or action.get("id") or sheet_path.stem)
        if name in used_names:
            name = f"{name}·{folder}"
        used_names.add(name)
        filename = _safe_name(name) + ".webp"
        target = videos / folder / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sheet_path, target)
        clips[name] = {
            "sheet": f"{folder}/{filename}",
            "folder": folder,
            **sheet,
        }
        copied += 1

    if not clips:
        return False, "没有找到可用的 WebP 精灵表文件"

    manifest_out = {
        "name": str(manifest.get("name") or character_id),
        "canvas": [canvas[0], canvas[1]],
        "feet_y": max(0, canvas[1] - 30),
        "clips": clips,
    }
    body_box = _region_box(manifest.get("regions"), "body", canvas)
    head_box = _region_box(manifest.get("regions"), "head", canvas)
    if body_box:
        manifest_out["body_box"] = body_box
    if head_box:
        manifest_out["head_box"] = head_box
    (videos / "manifest.json").write_text(
        json.dumps(manifest_out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    mod = {
        "schema": 1,
        "id": f"character.{character_id}",
        "kind": "content",
        "name": str(manifest.get("name") or character_id),
        "version": "1.0.0",
        "api_version": "1",
        "core_requires": ">=0.1.0,<1.0.0",
        "author": "aboutme-pets",
        "character": {
            "media": "videos",
            "name": str(manifest.get("name") or character_id),
            "canvas": [canvas[0], canvas[1]],
            "feet_y": max(0, canvas[1] - 30),
        },
    }
    if body_box:
        mod["character"]["body_box"] = body_box
    if head_box:
        mod["character"]["head_box"] = head_box
    (out / "mod.json").write_text(
        json.dumps(mod, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for name in _LICENSE_FILES:
        source_file = source / name
        if source_file.is_file():
            shutil.copy2(source_file, out / name)
    (out / "NOTICE-aboutme.txt").write_text(
        "角色素材来自 https://github.com/hunmer/aboutme-pets\n"
        "许可：CC BY-NC-SA 4.0 / 禁止商用；二创须署名（以源目录 NOTICE.txt 为准）。\n",
        encoding="utf-8",
    )
    return True, f"已导入 {copied} 段精灵表 -> {out}"
