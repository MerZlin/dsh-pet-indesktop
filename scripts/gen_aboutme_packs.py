"""生成 aboutme-pets 的 curated 下载索引（联网脚本，运行期不执行）。

只收录许可文件齐全的两个角色（dsh / dsh1），每个角色只下载 idle / move /
click / drag / work / 2 个随机动作所需的精灵表，避免整仓 1.9GB。
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "petcore" / "content" / "data" / "aboutme_packs.json"
REPO = "https://raw.githubusercontent.com/hunmer/aboutme-pets/main"
PETS = ("dsh", "dsh1")
LICENSE_FILES = (
    "NOTICE.txt",
    "LICENSE-assets.txt",
    "LICENSE-code.txt",
    "SOURCE-upstream.txt",
    "BATCH-source.txt",
)
MAX_SHEETS_PER_PET = 8


def _url(path: str) -> str:
    return f"{REPO}/{urllib.parse.quote(path, safe='/')}"


def _fetch(path: str) -> bytes:
    request = urllib.request.Request(_url(path), headers={"User-Agent": "dsh-pet-pack-index"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _entry(path: str, data: bytes) -> dict:
    return {
        "path": path,
        "url": _url(path),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _pick(actions: list[dict], *, names=(), categories=(), used: set[str]) -> str | None:
    for action in actions:
        action_id = str(action.get("id") or "")
        if not action_id or action_id in used:
            continue
        name = str(action.get("name") or action_id)
        category = str(action.get("category") or "")
        if any(token in name for token in names) or category in categories:
            return action_id
    return None


def _clips(manifest: dict, actions: list[dict]) -> dict:
    by_id = {str(item.get("id") or ""): item for item in actions}
    used: set[str] = set()

    def take(value) -> str | None:
        text = str(value or "").strip()
        if not text or text not in by_id or text in used:
            return None
        used.add(text)
        return text

    idle = take(manifest.get("idleAnimationId"))
    movement = manifest.get("movement") if isinstance(manifest.get("movement"), dict) else {}
    move = take(movement.get("leftAnimationId")) or take(movement.get("rightAnimationId"))
    drag = take(movement.get("dragAnimationId"))
    click_ids = manifest.get("click", {}).get("animationIds") if isinstance(manifest.get("click"), dict) else []
    click = take(click_ids[0] if click_ids else None)
    if click is None:
        click = take(_pick(actions, names=("点击", "互动"), categories=("互动动作",), used=used))
    work = take(_pick(actions, names=("写", "工作", "记录", "敲"), categories=("职业动作",), used=used))
    if drag is None:
        drag = take(_pick(actions, names=("拖",), categories=(), used=used))
    random_ids: list[str] = []
    for action in actions:
        if len(random_ids) >= 2:
            break
        action_id = str(action.get("id") or "")
        if not action_id or action_id in used or action_id not in by_id:
            continue
        random_ids.append(action_id)
        used.add(action_id)
    return {
        "idle": idle,
        "move": move,
        "click": click,
        "drag": drag or idle,
        "work": work,
        "random": random_ids,
    }


def _box(regions, key: str, canvas: tuple[int, int]) -> list[int] | None:
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


def build_pack(pet: str) -> dict:
    pet_dir = f"pets/{pet}"
    manifest_bytes = _fetch(f"{pet_dir}/manifest.json")
    actions_bytes = _fetch(f"{pet_dir}/actions.json")
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    actions_doc = json.loads(actions_bytes.decode("utf-8"))
    actions = actions_doc.get("animations") if isinstance(actions_doc.get("animations"), list) else []
    if not actions:
        actions = manifest.get("animations") if isinstance(manifest.get("animations"), list) else []
    clips = _clips(manifest, actions)
    by_id = {str(item.get("id") or ""): item for item in actions}

    wanted: list[str] = []
    for key in ("idle", "move", "click", "drag", "work"):
        value = clips.get(key)
        if value and value not in wanted:
            wanted.append(value)
    for value in clips.get("random") or []:
        if value not in wanted:
            wanted.append(value)
    wanted = wanted[:MAX_SHEETS_PER_PET]

    files: list[dict] = []
    seen: set[str] = set()

    def add(relative: str, payload: bytes) -> None:
        if relative in seen:
            return
        seen.add(relative)
        files.append(_entry(f"{pet_dir}/{relative}", payload))

    add("manifest.json", manifest_bytes)
    add("actions.json", actions_bytes)
    for name in LICENSE_FILES:
        try:
            add(name, _fetch(f"{pet_dir}/{name}"))
        except Exception:  # noqa: BLE001 - 许可文件缺失不阻断索引生成
            continue
    for action_id in wanted:
        action = by_id.get(action_id)
        if not action:
            continue
        source = action.get("sources", {}).get("webp") if isinstance(action.get("sources"), dict) else ""
        if not source:
            continue
        try:
            payload = _fetch(f"{pet_dir}/{source}")
        except Exception as exc:  # noqa: BLE001
            print(f"skip {pet}/{source}: {exc}", file=sys.stderr)
            continue
        add(str(source), payload)

    canvas = (
        int(manifest.get("canvasWidth") or 640),
        int(manifest.get("canvasHeight") or 360),
    )
    return {
        "id": pet,
        "name": str(manifest.get("name") or pet),
        "pet_dir": pet_dir,
        "canvas": [canvas[0], canvas[1]],
        "facing": "left",
        "body_box": _box(manifest.get("regions"), "body", canvas),
        "head_box": _box(manifest.get("regions"), "head", canvas),
        "clips": clips,
        "files": files,
    }


def main() -> int:
    packs = []
    for pet in PETS:
        print(f"building {pet}…")
        packs.append(build_pack(pet))
    payload = {
        "schema": 1,
        "source": "https://github.com/hunmer/aboutme-pets",
        "license": "CC BY-NC-SA 4.0 / 素材禁止商用；二创须署名（详见各角色 NOTICE.txt）",
        "packs": packs,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = sum(item["size"] for pack in packs for item in pack["files"])
    print(f"wrote {OUT} ({len(packs)} packs, {total / 1024 / 1024:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
