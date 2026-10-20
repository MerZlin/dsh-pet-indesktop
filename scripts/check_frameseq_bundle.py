# -*- coding: utf-8 -*-
"""打包前帧序列素材完整性硬门（构建管线，2026-10-08）。

**为什么需要这道门**：帧序列产物 ``assets/characters/<角色>/frameseq/`` 是派生的
二进制素材，刻意不入库（``.gitignore``: ``assets/characters/*/frameseq/``），只在
构建机的素材源里存在。worktree / 新克隆天然没有它，而 ``scripts/build_onedir.ps1``
对非 GIF 变体整树拷贝 ``assets/characters``（素材在就进包，缺了也不报错），于是
缺帧序列的部署包会被静默打出来：运行时每次启动只补转几段（启动预算受限），用户
长期看到未转换的素材（白边）。缺口必须在 PyInstaller 起跑**之前**挡住。

**判据**（与运行时采纳闸门口径一致，见 ``pet/frameseq_provision.py``）：
``assets/characters/<角色>/videos/**/*.webm`` 每段都必须有世代目录
``frameseq/<相对路径去扩展名>.g<源 sha256 前 12 位>/``，且其 ``meta.json`` 可读、
``frames`` 与磁盘 ``f_*.webp`` 帧数相符（半成品目录运行时同样拒收）、
``encoder`` / ``unblend`` 等于当前档（改档即让全部旧世代失效——这是设计好的迁移
路径，旧档产物不许冒充当前档出包）、**源身份**（``meta.source_sha256``）三件事
同时成立：64 位十六进制 ∧ 以该世代目录的戳（目录名前 12 位）开头 ∧ 等于当前源 webm
的完整 sha256。源身份是运行时的身份凭证（``provision:636-646`` / ``:674-680``）：
摘要缺失（旧版无戳产物）/ 非 hex / 与目录戳不符 / 指向另一份源，运行时都拒收那份
产物并回退 WebM 播放——目录名对而摘要不对的产物同样必须挡在构建期。

**约束**：零 Qt 零第三方依赖（构建机环境最小假设），只读文件系统（一个字节都不
写），不 import ``pet`` 包（那会连带 PySide6）。GIF 变体不在本门范围：它的素材是
``assets/characters_gif``（由 ``convert_to_gif.py`` 直出），没有帧序列这一层。

用法：
    python scripts/check_frameseq_bundle.py [--characters-dir assets/characters]

退出码：0 = 每个 clip 都有当前世代产物；1 = 素材树缺失 / clip 缺产物 / 世代档不符
/ 帧数不全 / meta 损坏（明细打到 stderr，含补法）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# 世代常量：与 pet/frameseq_provision.py **同源**（本脚本不 import pet 包，故此处
# 是副本；行号按当前源码标注）。tests/test_frameseq_bundle_gate.py 从该源码文本
# 重新提取这几个值逐条比对——provision 改档而这里没跟，测试即红。
# ---------------------------------------------------------------------------
# frameseq_provision.py:273 —— 统一编码档（全部 clip：Q70 + 白底反解 v5）。
# encoder 串是运行时采纳条件的一部分，不符 ⇒ 那份产物不被采纳（回退 WebM）。
ENCODER_DESC = "libwebp lossless (bgra straight, unblend-white-v6)"
# frameseq_provision.py:276 —— meta 里的反解标记（与 ENCODER_DESC 同写同弃）。
UNBLEND_MARK = "white-v6"
# frameseq_provision.py:240 / :241 —— 世代目录名 = <stem><GEN_INFIX><sha 前 N 位>。
GEN_INFIX = ".g"
GEN_STAMP_HEX = 12
# frameseq_provision.py:648 —— meta.source_sha256 必须是 64 位十六进制
# （``_complete_meta`` 的 ``_is_hex(stamp, 64)``：源身份凭证的长度口径）。
SOURCE_SHA_HEX = 64

# 每类失败清单最多列这么多条（构建日志可读性；总数照实报）。
MAX_LISTED = 20

PROBLEM_TITLES: dict[str, str] = {
    "missing_subtree": "角色缺 frameseq 子树（整棵未同步）",
    "missing_generation": "clip 缺当前世代产物",
    "stale_generation": "世代档不符（产物不是当前档）",
    "broken_meta": "meta.json 缺失或损坏",
    "bad_source_identity": "源身份不符（meta.source_sha256 缺失/非法/与目录戳或当前源不一致）",
    "incomplete_frames": "帧序列不全（帧数与 meta.json 不符）",
    "unreadable_source": "源 webm 不可读（无法定位世代目录）",
}

REMEDY = """怎么补（本门只读，不会替你生成任何东西）：
  1. 从规范素材源同步：把素材源的 assets/characters/<角色>/frameseq/ 整树拷进本仓库
     同名位置（每段 clip 一个 <stem>.g<戳>/ 目录，内含 meta.json；目录名不可改名）；
  2. 或在有 ffmpeg + 素材源的环境先跑离线供给补齐，再把 frameseq 目录同步进本仓库：
     python tools/convert_frameseq.py --character <角色> --include random,events
  背景：frameseq 是刻意不入库的派生产物（.gitignore 的 assets/characters/*/frameseq/），
  构建机必须自带素材源；新克隆 / 干净 worktree 里它天然不存在。"""


@dataclass(frozen=True)
class Problem:
    """一处构建门失败：类别 + 出问题的路径 + 人话说明。"""

    kind: str
    path: Path
    detail: str


@dataclass
class BundleReport:
    """素材树体检结果：每个角色（名, clip 数）+ 全部问题。"""

    characters: list[tuple[str, int]]
    problems: list[Problem]

    @property
    def clip_total(self) -> int:
        return sum(count for _, count in self.characters)

    @property
    def ok(self) -> bool:
        return not self.problems


def source_sha256(webm: Path) -> str | None:
    """webm 内容的 sha256（世代目录名的戳来源）；不可读返回 None。"""
    digest = hashlib.sha256()
    try:
        with webm.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def is_hex(text: object, length: int) -> bool:
    """定长十六进制判定（与 ``frameseq_provision._is_hex`` 同口径：大小写不敏感）。"""
    text = str(text)
    return len(text) == length and all(c in "0123456789abcdef" for c in text.lower())


def stamp_from_name(name: str) -> str | None:
    """从世代目录名解析源戳；不是世代目录名返回 None（与 provision 同口径）。"""
    text = str(name)
    if GEN_INFIX not in text:
        return None
    stem, _, tail = text.rpartition(GEN_INFIX)
    if not stem or not is_hex(tail, GEN_STAMP_HEX):
        return None
    return tail.lower()


def iter_character_dirs(characters_dir: Path) -> list[Path]:
    """素材根下的角色目录（含 videos 子目录的才算角色包），按名字排序。"""
    return sorted(
        (path for path in characters_dir.iterdir()
         if path.is_dir() and (path / "videos").is_dir()),
        key=lambda path: path.name,
    )


def iter_clips(videos_dir: Path) -> list[Path]:
    """角色 videos 下的全部 webm（深度不限，含 events/balance 这类二级目录），排序确定。"""
    return sorted(path for path in videos_dir.rglob("*.webm") if path.is_file())


def clip_generation_dir(webm: Path, videos_dir: Path, frameseq_root: Path,
                        digest: str) -> Path:
    """该 webm 当前源对应的世代目录：frameseq/<folder>/<stem>.g<戳>。

    与 ``frameseq_provision.clip_out_dir`` 同口径：戳 = 源 sha256 前 GEN_STAMP_HEX 位。
    源换版 ⇒ 戳变 ⇒ 旧戳目录不算数（世代陈旧在此暴露成「缺产物」）。
    """
    rel = webm.relative_to(videos_dir).with_suffix("")
    base = frameseq_root / rel
    return base.with_name(f"{base.name}{GEN_INFIX}{digest[:GEN_STAMP_HEX].lower()}")


def read_meta(gen_dir: Path) -> dict | None:
    """读世代目录的 meta.json；缺失 / 损坏 / 不是对象返回 None（与运行时同口径）。"""
    try:
        data = json.loads((gen_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def frames_on_disk(gen_dir: Path) -> int:
    """磁盘上的 ``f_*.webp`` 帧数（只数列目录名，不读内容）。

    与 ``frameseq_provision.frames_on_disk`` 同口径：帧目录是扁平结构。
    """
    count = 0
    try:
        with os.scandir(gen_dir) as entries:
            for entry in entries:
                name = entry.name
                if name.startswith("f_") and name.endswith(".webp"):
                    count += 1
    except OSError:
        return 0
    return count


def check_source_identity(gen_dir: Path, meta: dict, digest: str) -> Problem | None:
    """校验 ``meta.source_sha256`` 这份身份凭证（三件事，与运行时同口径）。

    ``digest`` 是**当前源 webm 的完整 sha256**，由调用方（``check_bundle``）算好传入：
    门本来就要为列清单算源哈希，这里复用，不双份读盘（一次 106 段 ≈ 53MB）。

    与 ``frameseq_provision._complete_meta`` 逐条对齐：长度/字符集 → 目录戳前缀 →
    完整值等于当前源。任一不成立，运行时就不采纳这份产物（回退 WebM 播放）。
    """
    stamp = stamp_from_name(gen_dir.name)
    if stamp is None:
        return Problem(
            "bad_source_identity", gen_dir,
            f"世代目录名解析不出源戳（应形如 <stem>{GEN_INFIX}<sha 前 "
            f"{GEN_STAMP_HEX} 位>）——运行时按名字取戳，取不到即拒收")
    identity = meta.get("source_sha256")
    if not is_hex(identity, SOURCE_SHA_HEX):
        return Problem(
            "bad_source_identity", gen_dir,
            f"meta.source_sha256={identity!r} 不是 {SOURCE_SHA_HEX} 位十六进制"
            "（旧版无戳产物没有身份凭证，运行时拒收）")
    identity = str(identity).lower()
    if not identity.startswith(stamp):
        return Problem(
            "bad_source_identity", gen_dir,
            f"meta.source_sha256={identity} 与目录戳 {stamp} 不符"
            f"（前 {GEN_STAMP_HEX} 位必须一致）")
    if identity != digest.lower():
        return Problem(
            "bad_source_identity", gen_dir,
            f"meta.source_sha256={identity} 指向另一份源"
            f"（当前源 webm 的 sha256 = {digest.lower()}）")
    return None


def check_clip(clip: Path, videos_dir: Path, frameseq_root: Path,
               digest: str) -> Problem | None:
    """单段 clip 体检：世代目录在不在、meta 能不能读、源身份对不对、帧全不全、档位对不对。"""
    gen_dir = clip_generation_dir(clip, videos_dir, frameseq_root, digest)
    if not gen_dir.is_dir():
        return Problem("missing_generation", gen_dir, "缺该 clip 的当前世代帧序列目录")
    meta = read_meta(gen_dir)
    if meta is None:
        return Problem("broken_meta", gen_dir, "meta.json 缺失、非 JSON 或不是对象")
    identity_problem = check_source_identity(gen_dir, meta, digest)
    if identity_problem is not None:
        return identity_problem
    # 帧数自洽（与运行时采纳闸门 ``_complete_meta`` 同口径）：半成品世代目录会被
    # 运行时拒收 ⇒ 用户看到的还是未转换素材，必须挡在构建期。
    frames = meta.get("frames")
    if type(frames) is not int or frames < 1:
        return Problem("incomplete_frames", gen_dir,
                       f"meta.json 的 frames={frames!r} 非法（帧数必须 ≥1）")
    on_disk = frames_on_disk(gen_dir)
    if on_disk != frames:
        return Problem("incomplete_frames", gen_dir,
                       f"帧数不符：meta.json 记 {frames} 帧，磁盘只有 {on_disk} 帧")
    mismatches: list[str] = []
    if meta.get("encoder") != ENCODER_DESC:
        mismatches.append(f"encoder={meta.get('encoder')!r}（当前档 {ENCODER_DESC!r}）")
    if meta.get("unblend") != UNBLEND_MARK:
        mismatches.append(f"unblend={meta.get('unblend')!r}（当前档 {UNBLEND_MARK!r}）")
    if mismatches:
        return Problem("stale_generation", gen_dir, "世代档不符：" + "；".join(mismatches))
    return None


def check_bundle(characters_dir: Path) -> BundleReport:
    """遍历素材树，返回每角色 clip 数 + 全部问题（不写盘、不抛）。"""
    characters: list[tuple[str, int]] = []
    problems: list[Problem] = []
    for character_dir in iter_character_dirs(characters_dir):
        videos_dir = character_dir / "videos"
        frameseq_root = character_dir / "frameseq"
        clips = iter_clips(videos_dir)
        characters.append((character_dir.name, len(clips)))
        if not clips:
            continue
        if not frameseq_root.is_dir():
            preview = "、".join(clip.stem for clip in clips[:3])
            if len(clips) > 3:
                preview += " 等"
            problems.append(Problem(
                "missing_subtree", frameseq_root,
                f"无 frameseq 目录：该角色 {len(clips)} 个 clip 全部没有帧序列（如 {preview}）",
            ))
            continue
        for clip in clips:
            digest = source_sha256(clip)
            if digest is None:
                problems.append(Problem("unreadable_source", clip, "源 webm 不可读"))
                continue
            problem = check_clip(clip, videos_dir, frameseq_root, digest)
            if problem is not None:
                problems.append(problem)
    return BundleReport(characters=characters, problems=problems)


def display_path(path: Path) -> str:
    """尽量用相对当前工作目录的路径（构建日志里短、可点）。"""
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def format_problems(problems: list[Problem]) -> list[str]:
    """按类别分组渲染失败清单（每类最多 MAX_LISTED 条，超出报总数）。"""
    grouped: dict[str, list[Problem]] = {}
    for problem in problems:
        grouped.setdefault(problem.kind, []).append(problem)
    lines: list[str] = []
    for kind, items in grouped.items():
        lines.append(f"  {PROBLEM_TITLES.get(kind, kind)}（{len(items)} 条）：")
        for problem in items[:MAX_LISTED]:
            lines.append(f"    - {display_path(problem.path)}：{problem.detail}")
        if len(items) > MAX_LISTED:
            lines.append(f"    … 其余 {len(items) - MAX_LISTED} 条省略")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="打包前帧序列素材完整性硬门（防缺帧序列的包被发布）"
    )
    parser.add_argument(
        "--characters-dir",
        default="assets/characters",
        help="角色素材根目录（默认 assets/characters，相对当前工作目录）",
    )
    args = parser.parse_args(argv)

    characters_dir = Path(args.characters_dir)
    if not characters_dir.is_dir():
        print(f"[frameseq-gate] 素材目录不存在: {characters_dir}"
              "（构建环境异常：打包机必须带 assets/characters 素材树）", file=sys.stderr)
        return 1

    report = check_bundle(characters_dir)
    if report.clip_total == 0:
        print(f"[frameseq-gate] 未在 {characters_dir} 下扫到任何 webm 素材"
              "（预期 <角色>/videos/**/*.webm）——构建环境异常，拒绝出包", file=sys.stderr)
        return 1

    if not report.ok:
        print("[frameseq-gate] FAIL: 帧序列素材不完整，拒绝出包"
              f"（{len(report.problems)} 处问题，{report.clip_total} 个 clip）", file=sys.stderr)
        for line in format_problems(report.problems):
            print(line, file=sys.stderr)
        print(REMEDY, file=sys.stderr)
        return 1

    print(f"[frameseq-gate] 检查 {display_path(characters_dir)}："
          f"{len(report.characters)} 个角色 / {report.clip_total} 个 clip")
    for name, count in report.characters:
        print(f"  - {name}: {count} 个 clip，世代档 {ENCODER_DESC} / 反解 {UNBLEND_MARK}")
    print("[frameseq-gate] PASS: 每个 clip 都有当前世代的帧序列产物，可以出包")
    return 0


if __name__ == "__main__":
    sys.exit(main())
