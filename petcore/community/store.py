"""随包快照与运行期缓存的读写（Qt-free）。

只做文件 IO 与 JSON 形态校验；「谁更新」的取舍在 :mod:`petcore.community.model`。
随包文件 **只读**：任何运行期路径都不会写回 ``petcore/community/data``。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from .model import Credits, Fans, parse_credits, parse_fans

logger = logging.getLogger("petcore.community.store")

BUNDLE_DIR = Path(__file__).resolve().parent / "data"
CREDITS_FILE = "credits.json"
FANS_FILE = "fans.json"
FANS_COUNT_FILE = "fans-count.json"
CACHE_DIR = "community"
SIGNATURE_FILE = "signature.png"
SIGNATURE_META_FILE = "signature.json"
MAX_SIGNATURE_BYTES = 2 * 1024 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def bundle_dir() -> Path:
    """随包目录：``petcore/community/data``（PyInstaller 以同路径收进 ``_internal``）。"""
    return BUNDLE_DIR


def data_dir(root: Path | str) -> Path:
    return Path(root) / CACHE_DIR


def bundled_credits_path() -> Path:
    return bundle_dir() / CREDITS_FILE


def bundled_fans_path() -> Path:
    return bundle_dir() / FANS_FILE


def credits_cache_path(root: Path | str) -> Path:
    return data_dir(root) / "credits-cache.json"


def fans_cache_path(root: Path | str) -> Path:
    return data_dir(root) / "fans-cache.json"


def fans_count_path(root: Path | str) -> Path:
    return data_dir(root) / FANS_COUNT_FILE


def signature_path(root: Path | str) -> Path:
    return data_dir(root) / SIGNATURE_FILE


def signature_meta_path(root: Path | str) -> Path:
    return data_dir(root) / SIGNATURE_META_FILE


# ---------------------------------------------------------------- JSON


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------- 快照/缓存


def load_bundled_credits() -> Credits:
    return parse_credits(_read_json(bundled_credits_path()))


def load_bundled_fans() -> Fans:
    return parse_fans(_read_json(bundled_fans_path()))


def load_cached_credits(root: Path | str) -> Credits | None:
    payload = _read_json(credits_cache_path(root))
    return parse_credits(payload) if isinstance(payload, dict) else None


def load_cached_fans(root: Path | str) -> Fans | None:
    payload = _read_json(fans_cache_path(root))
    return parse_fans(payload) if isinstance(payload, dict) else None


def save_cached_credits(root: Path | str, credits: Credits) -> None:
    _write_json(
        credits_cache_path(root),
        {
            "schema": 1,
            "repo": credits.repo,
            "fetched_at": credits.fetched_at,
            "contributors": [
                {"login": item.login, "contributions": item.contributions}
                for item in credits.contributors
            ],
            "authors": [
                {"login": item.login, "issues": item.issues, "prs": item.prs}
                for item in credits.authors
            ],
        },
    )


def load_fans_count(root: Path | str) -> dict | None:
    payload = _read_json(fans_count_path(root))
    return payload if isinstance(payload, dict) else None


def save_fans_count(
    root: Path | str, vmid: int, total: int, fetched_at: str
) -> None:
    _write_json(
        fans_count_path(root),
        {
            "schema": 1,
            "vmid": int(vmid),
            "total": max(0, int(total)),
            "fetched_at": str(fetched_at or ""),
        },
    )


def save_cached_fans(root: Path | str, fans: Fans) -> None:
    _write_json(
        fans_cache_path(root),
        {
            "schema": 1,
            "vmid": int(fans.vmid),
            "fetched_at": fans.fetched_at,
            "total": int(fans.total),
            "names": list(fans.names),
        },
    )


# ---------------------------------------------------------------- 签名


def save_signature_png(root: Path | str, data: bytes) -> Path:
    """原子覆盖唯一手写签名；空/超大/非 PNG 一律拒绝。"""
    if not data:
        raise ValueError("签名图像为空")
    if len(data) > MAX_SIGNATURE_BYTES:
        raise ValueError("签名图像过大")
    if not bytes(data[:8]).startswith(_PNG_MAGIC):
        raise ValueError("签名必须是 PNG")
    destination = signature_path(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, destination)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    return destination


def save_signature_meta(root: Path | str, name: str, updated_at: str) -> None:
    _write_json(
        signature_meta_path(root),
        {"name": str(name or "")[:40], "updated_at": str(updated_at or "")},
    )


def load_signature_meta(root: Path | str) -> dict[str, str]:
    payload = _read_json(signature_meta_path(root))
    if not isinstance(payload, dict):
        return {}
    return {
        "name": str(payload.get("name") or "")[:40],
        "updated_at": str(payload.get("updated_at") or "")[:40],
    }


def clear_signature(root: Path | str) -> bool:
    removed = False
    for path in (signature_path(root), signature_meta_path(root)):
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            continue
        except OSError:
            logger.debug("signature unlink failed: %s", path, exc_info=True)
    return removed
