# -*- coding: utf-8 -*-
"""把一个角色的帧序列打成可分发的分件包（core / extra）。

**为什么分件**：完整无损素材 106 段约 1.3 GB，一次下载不现实。core 只装
「离线也必须完整可用」的交互闭包（待机/移动/转向/点击/拖拽，与激活闸门
``character_pack.CORE_REQUIRED_CATEGORIES`` 同一常量），其余（random/events
长尾）走 extra——按 ~256MB 切成 extra1/extra2/…（断点续传友好），装完随时补。

产物（默认 `dist-packs/`）::

    <id>-<version>-<variant>-<WxH>.core.zip
    <id>-<version>-<variant>-<WxH>.extra1.zip（可能还有 extra2/extra3…）
    <id>-<version>.registry.json      ← 本版注册表片段
    packs-index.json（仓库根）         ← 自动并入的总清单（jsdelivr @main 服务）

发布门：打完包会把两个分件**真装进临时目录**（完整走解包预算/结构校验/发行
身份/逐 clip 摘要/解码抽查/画布比对），再查 core 闭包——门不过不许发。
可用 --skip-verify 跳过（只调试用）。

分件内布局::

    part.json      ← 本分件元信息 + clips 清单（安装时合并成 character.json）
    manifest.json  ← 角色画布元信息（body_box/head_box）
    anim/<category>/<clip>/meta.json + f_*.webp

`content_sha` 口径：**逐 clip 按帧序拼接的帧文件字节的 sha256**（存储身份）。
安装后重算同一摘要即可发现"下坏了/被改写"，见 `pet/pack_download.py` 的安装校验。

用法::

    python scripts/build_character_pack.py --character shenshen --version 2.0.0
    python scripts/build_character_pack.py --version 2.0.0 --url-base https://github.com/.../releases/download/v2.0.0
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pet import catalog  # noqa: E402
from pet import character_pack as cp  # noqa: E402
from pet import frameseq_provision as fp  # noqa: E402
from pet import pack_download as pd  # noqa: E402

#: core = 离线必须完整可用的交互闭包，**与激活闸门同一常量**
#: （``character_pack.CORE_REQUIRED_CATEGORIES``）——两边不许各写一份，
#: 否则会出现"打包放进 core 的类"与"闸门要求的类"漂移（events 曾因此
#: 被错放进 core）。
CORE_CATEGORIES = cp.CORE_REQUIRED_CATEGORIES

#: extra 分件的切块大小（下载友好：块越小断点续传越不痛）。
EXTRA_CHUNK_BYTES = 256 * 1024 ** 2


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _clip_digest(frames: list[Path]) -> str:
    """逐 clip 的存储身份：按帧序把**帧文件字节**喂进同一个 sha256。"""
    h = hashlib.sha256()
    for p in frames:
        h.update(p.name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()


def collect_clips(char_dir: Path) -> list[dict]:
    """扫世代目录，产出 clip 清单（dir/frames/key_every/part/content_sha/bytes）。"""
    videos = char_dir / "videos"
    seq_root = char_dir / "frameseq"
    out: list[dict] = []
    pack_fps: float | None = None
    for webm in sorted(videos.rglob("*.webm")):
        rel = webm.relative_to(videos).with_suffix("")
        category = rel.parts[0].lower()
        gen = seq_root / rel.parent / f"{rel.name}.g{fp.source_sha256(webm)[:12]}"
        if not gen.is_dir():
            raise SystemExit(f"缺世代目录（先跑 convert_frameseq）: {gen}")
        meta = fp.read_meta(gen) or {}
        if meta.get("encoder") != fp.ENCODER_DESC:
            raise SystemExit(
                f"档位不符（先重出素材）: {gen} encoder={meta.get('encoder')!r}"
                f" 期望={fp.ENCODER_DESC!r}")
        # 包内必须同帧率（终审第六轮：整包 fps 取首段，其他段的错帧率会
        # 一路溜过门、运行时才被拒——在收集阶段就拦住）
        clip_fps = float(meta.get("fps") or 24.0)
        if pack_fps is None:
            pack_fps = clip_fps
        elif abs(clip_fps - pack_fps) > 0.01:
            raise SystemExit(f"帧率不一致: {gen} fps={clip_fps} != {pack_fps}")
        frames = sorted(gen.glob("f_*.webp"))
        declared = int(meta.get("frames") or 0)
        if len(frames) != declared or declared <= 0:
            raise SystemExit(f"帧数不符: {gen} meta={declared} 磁盘={len(frames)}")
        part = "core" if category in CORE_CATEGORIES else "extra"
        out.append({
            "name": rel.name,
            "category": category,
            "dir": f"anim/{category}/{rel.name}",
            "src_dir": gen,
            "frames": declared,
            "key_every": int(meta.get("key_every") or 12),
            "part": part,
            "content_sha": _clip_digest(frames),
            "bytes": sum(p.stat().st_size for p in frames),
        })
    if not out:
        raise SystemExit(f"没找到任何 clip: {videos}")
    return out


def write_part(char_dir: Path, clips: list[dict], part: str, target: Path,
               *, ident: dict) -> dict:
    """把一个分件写成 zip，返回注册表用的 part 描述。"""
    manifest = json.loads((char_dir / "videos" / "manifest.json").read_text(encoding="utf-8"))
    part_doc = {
        "schema": cp.SCHEMA,
        "id": ident["id"],
        "name": ident["name"],
        "canvas": ident["canvas"],
        "fps": ident["fps"],
        "pipeline": ident["pipeline"],
        "format": ident["format"],
        "parts": [p for p in ("core", "extra") if any(c["part"] == p for c in clips)] or [part],
        "clips": [{k: c[k] for k in ("name", "category", "dir", "frames",
                                     "key_every", "part", "content_sha", "bytes")}
                  for c in clips],
    }
    # 行为 sidecar（终审第四轮 B10）：打包原来只带 manifest 与帧目录，于是新包里
    # 读不到 text_clips（朝右时文字反显）与 move_strides（移动动画丢标定步幅/逐帧
    # 曲线）。它们随 **core** 走：extra-only 安装不该凭空造出"半个角色"。
    sidecars = {}
    if part == "core":
        for name in ("text_clips.json", "move_strides.json"):
            src = char_dir / "videos" / name
            if src.is_file():
                sidecars[name] = src.read_text(encoding="utf-8")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("part.json", json.dumps(part_doc, ensure_ascii=False, indent=1))
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        for name, text in sidecars.items():
            zf.writestr(name, text)
        for c in clips:
            for p in sorted(Path(c["src_dir"]).iterdir()):
                zf.write(p, f"{c['dir']}/{p.name}")
    zip_size = target.stat().st_size
    return {"name": part, "bytes": zip_size,
            # ZIP_STORED：解包体积 ≈ 包体积（磁盘预检的口径，见下载页预检）
            "unpacked_bytes": zip_size,
            "sha256": _sha256_file(target), "urls": []}


def main() -> int:
    ap = argparse.ArgumentParser(description="打角色分件包")
    ap.add_argument("--character", default="shenshen")
    ap.add_argument("--version", required=True, help="包版本，例如 2.0.0")
    ap.add_argument("--variant", default="base")
    ap.add_argument("--out", default="dist-packs")
    ap.add_argument("--url-base", default="",
                    help="分件下载地址前缀（Release 资产直链）；留空则输出占位")
    ap.add_argument("--app-min", default="4.4.0")
    ap.add_argument("--index", default="packs-index.json",
                    help="要并入的总清单（相对仓库根；默认 packs-index.json，"
                         "即 jsdelivr @main 服务的那份）")
    ap.add_argument("--skip-verify", action="store_true",
                    help="跳过发布门（把分件真装进临时目录 + core 闭包校验）")
    args = ap.parse_args()

    char_dir = REPO_ROOT / "assets" / "characters" / args.character
    if not (char_dir / "frameseq").is_dir():
        print(f"角色素材不完整: {char_dir}", file=sys.stderr)
        return 1
    clips = collect_clips(char_dir)
    # core 闭包门：core 分件必须覆盖激活闸门要求的全部交互类——少一类，
    # 用户装完 core 也激活不了（闸门与打包用同一常量，这里是第二道核对）
    core_cats = {c["category"] for c in clips if c["part"] == "core"}
    missing = [c for c in CORE_CATEGORIES if c not in core_cats]
    if missing:
        raise SystemExit(f"core 分件缺必需动作类 {missing}（激活闸门不会放行）")
    manifest = json.loads((char_dir / "videos" / "manifest.json").read_text(encoding="utf-8"))

    # 画布取角色目录的规范常量（meta 里不存画布尺寸，别在这里猜数字）
    first_meta = fp.read_meta(Path(clips[0]["src_dir"])) or {}
    canvas = [int(catalog.CANVAS_W), int(catalog.CANVAS_H)]
    ident = {
        "id": args.character,
        "name": manifest.get("display_name") or args.character,
        "canvas": canvas,
        "fps": float(first_meta.get("fps") or 24.0),
        "pipeline": fp.UNBLEND_MARK,
        "format": "lossless-frame-v1",
    }

    out_dir = REPO_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{args.character}-{args.version}-{args.variant}-{canvas[0]}x{canvas[1]}"

    # extra 按体积切块（~256MB/块）：1.3GB 一个大包对慢网太狠，切块 + 断点续传
    # 才能"网不好也慢慢拉"。块名就是 clip 的 part 归属（extra1/extra2/…），
    # 与注册表分件名、索引 parts 列表三方一致——下载页据此判断"哪块已装"。
    extra_clips = sorted((c for c in clips if c["part"] == "extra"),
                         key=lambda c: c["name"])
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_bytes = 0
    for c in extra_clips:
        if current and current_bytes + c["bytes"] > EXTRA_CHUNK_BYTES:
            chunks.append(current)
            current, current_bytes = [], 0
        current.append(c)
        current_bytes += c["bytes"]
    if current:
        chunks.append(current)
    named_subsets: list[tuple[str, list[dict]]] = [
        ("core", [c for c in clips if c["part"] == "core"])]
    for i, chunk in enumerate(chunks, 1):
        for c in chunk:
            c["part"] = f"extra{i}"
        named_subsets.append((f"extra{i}", chunk))
    named_subsets = [(p, s) for p, s in named_subsets if s]

    parts = []
    for part, subset in named_subsets:
        target = out_dir / f"{stem}.{part}.zip"
        info = write_part(char_dir, subset, part, target, ident=ident)
        if args.url_base:
            direct = f"{args.url_base.rstrip('/')}/{target.name}"
            # 多镜像回退（国内直连 GitHub 经常断流，实机踩过）：镜像只当 CDN，
            # 内容身份由 sha256 校验兜底。顺序 = 直连优先、镜像兜底。
            info["urls"] = [direct,
                            f"https://mirror.ghproxy.com/{direct}",
                            f"https://gh-proxy.com/{direct}"]
        parts.append(info)
        total_frames = sum(c["frames"] for c in subset)
        print(f"  {target.name}: {len(subset)} 段 / {total_frames} 帧 / "
              f"{info['bytes'] / 1048576:.1f} MB  sha256={info['sha256'][:16]}…")

    pack_entry = {
        "id": ident["id"],
        "name": ident["name"],
        "variant": args.variant,
        "version": args.version,
        "canvas": canvas,
        "pipeline": ident["pipeline"],
        "format": ident["format"],
        "app_min": args.app_min,
        "parts": parts,
    }
    registry = {"schema": 1, "packs": [pack_entry]}
    reg_path = out_dir / f"{args.character}-{args.version}.registry.json"
    reg_path.write_text(json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n注册表: {reg_path}")
    print(f"总段数 {len(clips)}，core {sum(1 for c in clips if c['part'] == 'core')} 段 / "
          f"extra {sum(1 for c in clips if c['part'] != 'core')} 段"
          f"（{len(chunks)} 块）")

    if not args.skip_verify:
        # 发布门（最全的一道）：把分件**真的装进临时目录**——解包预算、part.json
        # 结构、发行身份、逐 clip 摘要、可播放验证（解码抽查 + 画布）全走一遍
        # 真实安装路径；装完再查 core 闭包 + 运行时采纳闸门（admissible）——
        # 只装得上不算完，还得**播得了**（终审第六轮）。
        import tempfile
        stem_dir = f"{args.version}-{args.variant}-{canvas[0]}x{canvas[1]}"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ident["id"] / stem_dir
            for info in parts:
                pd.install_part(out_dir / f"{stem}.{info['name']}.zip", target)
            index = cp.load_index(target)
            assert index is not None, "装完没有索引（发布门失败）"
            gaps = cp.core_gaps(target, index)
            if gaps:
                raise SystemExit(f"发布门失败：core 闭包不过（{gaps[:5]}），这批包不许发")
            bad = []
            for entry in index.clips:
                clip_dir = target / entry.dir
                meta = fp.read_meta(clip_dir)
                names = sorted(p.name for p in clip_dir.glob("f_*.webp"))
                ok, why = cp.admissible(entry, meta, names, encoder=fp.ENCODER_DESC,
                                        fps=float(index.fps), canvas=index.canvas)
                if not ok:
                    bad.append(f"{entry.name}: {why}")
            if bad:
                raise SystemExit("发布门失败：运行时采纳闸门不过（" + "; ".join(bad[:5]) + "）")
        print("发布门：分件真实安装 + core 闭包 + 运行时采纳闸门 全部通过")

    # 并入总清单（jsdelivr @main 服务的那份）：同 (id,variant,version,canvas)
    # 条目整体替换，其余保留——发新素材包不需要用户更新应用。
    index_path = REPO_ROOT / args.index
    if index_path.is_file():
        try:
            doc = json.loads(index_path.read_text(encoding="utf-8"))
            existing = doc.get("packs") if isinstance(doc, dict) else None
            packs = list(existing) if isinstance(existing, list) else []
        except ValueError:
            raise SystemExit(f"总清单不是合法 JSON，先人工核对: {index_path}")
    else:
        packs = []
    key = (pack_entry["id"], pack_entry["variant"], pack_entry["version"],
           pack_entry["canvas"][0], pack_entry["canvas"][1])
    packs = [p for p in packs
             if (p.get("id"), p.get("variant"), p.get("version"),
                 (p.get("canvas") or [0, 0])[0], (p.get("canvas") or [0, 0])[1]) != key]
    packs.append(pack_entry)
    index_path.write_text(
        json.dumps({"schema": 1, "packs": packs}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"总清单: {index_path}（{len(packs)} 个包）")

    if not args.url_base:
        print("提示：未给 --url-base，注册表里的 url 为空；发布到 Release 后需回填。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
