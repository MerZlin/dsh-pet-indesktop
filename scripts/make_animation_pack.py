"""把角色目录打成素材包 ZIP，并生成 ``packs-index.json`` 的条目。

素材包结构必须能被 ``petcore.content.importer.install_character_source`` 直接吃下：
ZIP 根目录要么是 ``videos/``，要么是单个包装目录。本脚本按「角色目录的**内容**」
入包（``videos/`` + ``frameseq/``），因此导入端看到的根就是 ``videos/``。

用法::

    python scripts/make_animation_pack.py \
        --source assets/characters/shenshen \
        --out .scratch/animations-repo \
        --id shenshen --version 1 \
        --name "咲恋 · 完整素材包" \
        --base-url https://github.com/MerZlin/Animations/releases/download/packs-v1/

成功后在 ``--out`` 下得到 ``<id>-<version>.zip`` 与 ``packs-index.json``（同 id 覆盖）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_NAME = "咲恋 · 完整素材包"
DEFAULT_LINKS = {
    "github": "https://github.com/MerZlin/Animations/releases/tag/packs-v1",
    "quark": "https://pan.quark.cn/s/68fc681ae486",
}


def _iter_files(source: Path):
    for current, dirnames, filenames in os.walk(source):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(current) / name
            if path.is_file():
                yield path, path.relative_to(source)


def build_zip(source: Path, target: Path, *, compress_level: int = 6) -> tuple[int, int]:
    """写 ZIP；返回 ``(文件数, 原始字节数)``。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".part")
    files = 0
    raw_bytes = 0
    started = time.perf_counter()
    with zipfile.ZipFile(
        temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=compress_level
    ) as archive:
        for path, relative in _iter_files(source):
            archive.write(path, arcname=relative.as_posix())
            files += 1
            raw_bytes += path.stat().st_size
            if files % 500 == 0:
                print(
                    f"  {files} 个文件 / {raw_bytes / (1024 * 1024):.1f} MiB"
                    f"（{time.perf_counter() - started:.0f}s）",
                    flush=True,
                )
    os.replace(temp, target)
    return files, raw_bytes


def split_file(source: Path, *, part_bytes: int, out_dir: Path) -> list[dict]:
    """把成品 zip 切成 ``part_bytes`` 大小的分片，返回索引里的 ``parts`` 列表。"""
    size = source.stat().st_size
    if part_bytes <= 0 or size <= part_bytes:
        return []
    parts: list[dict] = []
    with source.open("rb") as stream:
        index = 1
        while True:
            block = stream.read(part_bytes)
            if not block:
                break
            name = f"{source.name}.{index:03d}"
            target = out_dir / name
            target.write_bytes(block)
            parts.append(
                {
                    "filename": name,
                    "size": len(block),
                    "sha256": hashlib.sha256(block).hexdigest(),
                    "urls": [],
                }
            )
            index += 1
    return parts


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(4 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def entry_for(
    *,
    pack_id: str,
    name: str,
    version: str,
    filename: str,
    size: int,
    sha256: str,
    base_url: str,
    notes: str,
    links: dict[str, str],
    parts: list[dict] | None = None,
    prefer_parts: bool = False,
) -> dict:
    url = base_url.rstrip("/") + "/" + filename
    entry = {
        "id": pack_id,
        "name": name,
        "version": str(version),
        "filename": filename,
        "size": int(size),
        "sha256": sha256,
        "character": pack_id,
        "urls": [url],
        "links": dict(links),
        "notes": notes,
    }
    if parts:
        entry["parts"] = parts
        entry["prefer_parts"] = bool(prefer_parts)
    return entry


def merge_index(path: Path, entry: dict) -> dict:
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
    else:
        payload = None
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        payload = {"schema": 1, "packs": []}
    packs = [item for item in payload.get("packs", []) if isinstance(item, dict)]
    packs = [item for item in packs if item.get("id") != entry["id"]]
    packs.append(entry)
    payload["packs"] = packs
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="make_animation_pack")
    parser.add_argument("--source", type=Path, required=True, help="角色目录（含 videos/）")
    parser.add_argument("--out", type=Path, required=True, help="产物目录")
    parser.add_argument("--id", required=True, help="素材包与角色 id（小写字母数字）")
    parser.add_argument("--version", default="1")
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument(
        "--base-url",
        default="https://github.com/MerZlin/Animations/releases/download/packs-v1/",
        help="下载地址前缀（文件名会拼在后面）",
    )
    parser.add_argument("--notes", default="")
    parser.add_argument("--compress-level", type=int, default=6)
    parser.add_argument("--index", type=Path, default=None, help="索引输出路径（默认 <out>/packs-index.json）")
    parser.add_argument(
        "--part-bytes",
        type=int,
        default=0,
        help="超过 0 时把包切成这个大小的分片（Gitee 免费仓库单文件上限 100 MB，建议 60 MiB）",
    )
    parser.add_argument(
        "--parts-base-url",
        default="",
        help="分片下载地址前缀（例：https://gitee.com/MerZlin/dsh-pet-animations/raw/main/packs）",
    )
    parser.add_argument(
        "--prefer-parts",
        action="store_true",
        help="索引里标明优先走分片（国内镜像）",
    )
    args = parser.parse_args(argv)

    source = args.source.resolve()
    if not (source / "videos").is_dir():
        print(f"角色目录缺少 videos/：{source}")
        return 2
    out = args.out.resolve()
    filename = f"{args.id}-{args.version}.zip"
    target = out / filename
    print(f"打包 {source} → {target}", flush=True)
    files, raw_bytes = build_zip(source, target, compress_level=args.compress_level)
    size = target.stat().st_size
    digest = sha256_of(target)
    print(
        f"完成：{files} 个文件 / 原始 {raw_bytes / (1024 * 1024):.1f} MiB / "
        f"压缩后 {size / (1024 * 1024):.1f} MiB",
        flush=True,
    )
    entry = entry_for(
        pack_id=args.id,
        name=args.name,
        version=args.version,
        filename=filename,
        size=size,
        sha256=digest,
        base_url=args.base_url,
        notes=args.notes,
        links=DEFAULT_LINKS,
    )
    parts_spec = split_file(target, part_bytes=int(args.part_bytes or 0), out_dir=out)
    if parts_spec:
        base = args.parts_base_url.rstrip("/")
        if not base:
            print("警告：切了分片但没给 --parts-base-url，索引里的分片地址为空（应用会跳过这套源）")
        for spec in parts_spec:
            if base:
                spec["urls"] = [f"{base}/{spec['filename']}"]
        print(f"已切分：{len(parts_spec)} 片，最大 {max(s['size'] for s in parts_spec) / 1048576:.1f} MiB")
        entry["parts"] = parts_spec
        entry["prefer_parts"] = bool(args.prefer_parts)
    index_path = args.index or (out / "packs-index.json")
    payload = merge_index(index_path, entry)
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    print(f"索引已更新：{index_path}（{len(payload['packs'])} 个包）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
