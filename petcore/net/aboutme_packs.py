"""aboutme-pets curated 角色包下载：逐文件校验，失败不留半包。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from .http import fetch_url

_INDEX = Path(__file__).resolve().parents[1] / "content" / "data" / "aboutme_packs.json"
MAX_FILE_BYTES = 4 * 1024 * 1024


def index_path() -> Path:
    return _INDEX


def load_index() -> dict:
    try:
        raw = json.loads(_INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def packs() -> list[dict]:
    items = load_index().get("packs")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def pack_for(pack_id: str) -> dict | None:
    wanted = str(pack_id or "").strip()
    for pack in packs():
        if str(pack.get("id") or "") == wanted:
            return pack
    return None


def _target_path(root: Path, pet_dir: str, relative: str) -> Path | None:
    prefix = str(pet_dir or "").rstrip("/") + "/"
    text = str(relative or "")
    if prefix and text.startswith(prefix):
        text = text[len(prefix) :]
    target = (root / text).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def download_pack(
    pack_id: str,
    dest: Path,
    *,
    progress=None,
) -> tuple[bool, str, Path | None]:
    """下载一个 curated 包到 ``dest``；返回 (ok, message, path)。"""
    pack = pack_for(pack_id)
    if pack is None:
        return False, f"未知角色包：{pack_id}", None
    files = [item for item in pack.get("files") or [] if isinstance(item, dict)]
    if not files:
        return False, "角色包索引为空", None
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        return False, f"目标目录非空：{dest}", None
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{dest.name}.", dir=str(dest.parent)))
    total = sum(int(item.get("size") or 0) for item in files)
    done = 0
    try:
        for item in files:
            url = str(item.get("url") or "")
            relative = str(item.get("path") or "")
            expected_size = int(item.get("size") or 0)
            expected_sha = str(item.get("sha256") or "").lower()
            if not url or not relative or not expected_sha:
                return False, "角色包索引缺少 url/path/sha256", None
            target = _target_path(temp, str(pack.get("pet_dir") or ""), relative)
            if target is None:
                return False, f"索引路径越界：{relative}", None
            status, _headers, body = fetch_url(
                url,
                timeout=60,
                prefer_direct=False,
                max_bytes=MAX_FILE_BYTES,
            )
            payload = bytes(body)
            if getattr(body, "truncated", False):
                return False, f"下载被截断：{relative}", None
            if int(status) != 200:
                return False, f"下载失败（HTTP {status}）：{url.split('/')[2]}", None
            if expected_size and len(payload) != expected_size:
                return False, f"文件大小不符：{relative}", None
            digest = hashlib.sha256(payload).hexdigest()
            if digest != expected_sha:
                return False, f"文件摘要不符：{relative}", None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            done += len(payload)
            if callable(progress):
                try:
                    progress(done, total or done, relative)
                except Exception:  # noqa: BLE001 - 进度回调失败不影响下载
                    pass
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        os.replace(temp, dest)
        return True, f"已下载 {pack.get('name') or pack_id}", dest
    except Exception as exc:  # noqa: BLE001 - 只暴露类型，不带完整 URL
        return False, f"下载失败：{type(exc).__name__}", None
    finally:
        if temp.exists():
            shutil.rmtree(temp, ignore_errors=True)


__all__ = ["download_pack", "index_path", "load_index", "pack_for", "packs"]
