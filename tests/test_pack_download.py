# -*- coding: utf-8 -*-
"""``pet/pack_download.py``（角色包注册表与断点续传）的回归测试。

用一个**本机 HTTP 服务器**测真实 Range 行为（不是 mock 传输层）。
"""
from __future__ import annotations

import hashlib
import http.server
import socket
import threading

import pytest

from pet import pack_download as pd

SHA_OK = "c" * 64


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Handler(http.server.BaseHTTPRequestHandler):
    payload: bytes = b""
    support_range: bool = True
    fail_times: int = 0

    def log_message(self, *args):        # 静音
        pass

    def do_GET(self):                    # noqa: N802
        cls = type(self)
        if cls.fail_times > 0:
            cls.fail_times -= 1
            self.send_response(500)
            self.end_headers()
            return
        data = cls.payload
        rng = self.headers.get("Range")
        if rng and cls.support_range:
            start = int(rng.split("=")[1].split("-")[0])
            body = data[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def server():
    _Handler.payload = b"hello-world-" * 5000        # 60KB
    _Handler.support_range = True
    _Handler.fail_times = 0
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/part.bin"
    finally:
        srv.shutdown()
        srv.server_close()


def _part(url: str, data: bytes, name: str = "core.zip") -> pd.Part:
    return pd.Part(name=name, urls=(url,), bytes=len(data), sha256=_sha(data))


def _registry(**over):
    base = {
        "schema": 1,
        "packs": [{
            "id": "shenshen", "name": "深深", "variant": "base", "version": "2.0.0",
            "canvas": [640, 360], "pipeline": "unblend-white-v6",
            "format": "lossless-frame-v1", "app_min": "4.4.0",
            "parts": [{
                "name": "core",
                "url": "https://cdn.jsdelivr.net/gh/x/y@main/core.zip",
                "bytes": 100, "sha256": SHA_OK,
            }],
        }],
    }
    base.update(over)
    return base


# ------------------------------------------------------------------ 注册表
def test_parse_registry_ok():
    packs = pd.parse_registry(_registry())
    assert len(packs) == 1
    pack = packs[0]
    assert pack.id == "shenshen" and pack.variant == "base"
    assert pack.folder_name() == "2.0.0-base-640x360"
    assert pack.labels() == ("core",)
    assert pack.parts[0].urls[0].startswith("https://cdn.jsdelivr.net")


def test_registry_rejects_untrusted_url():
    bad = _registry()
    bad["packs"][0]["parts"][0]["url"] = "http://evil.example.com/core.zip"
    with pytest.raises(pd.RegistryError):
        pd.parse_registry(bad)


def test_registry_rejects_bad_schema_and_empty_packs():
    with pytest.raises(pd.RegistryError):
        pd.parse_registry(_registry(schema=7))
    with pytest.raises(pd.RegistryError):
        pd.parse_registry(_registry(packs=[]))


def test_registry_rejects_duplicate_pack():
    reg = _registry()
    reg["packs"].append(dict(reg["packs"][0]))
    with pytest.raises(pd.RegistryError):
        pd.parse_registry(reg)


def test_registry_rejects_bad_sha_and_size():
    reg = _registry()
    reg["packs"][0]["parts"][0]["sha256"] = "zz"
    with pytest.raises(pd.RegistryError):
        pd.parse_registry(reg)
    reg = _registry()
    reg["packs"][0]["parts"][0]["bytes"] = 0
    with pytest.raises(pd.RegistryError):
        pd.parse_registry(reg)


# ------------------------------------------------------------------ 下载
def test_download_from_scratch(server, tmp_path):
    data = _Handler.payload
    got = pd.download_part(_part(server, data), tmp_path)
    assert got.read_bytes() == data
    assert not (tmp_path / "core.zip.part").exists()


def test_resume_from_partial_file(server, tmp_path):
    """已收一半的半成品 → Range 续传，最终内容完整。"""
    data = _Handler.payload
    part = _part(server, data)
    (tmp_path / "core.zip.part").write_bytes(data[:20000])
    got = pd.download_part(part, tmp_path)
    assert got.read_bytes() == data


def test_server_without_range_support_restarts_cleanly(tmp_path, server):
    """服务端忽略 Range（回 200 全量）时不得把全量追加到半成品后面。"""
    _Handler.support_range = False
    data = _Handler.payload
    part = _part(server, data)
    (tmp_path / "core.zip.part").write_bytes(data[:20000])
    got = pd.download_part(part, tmp_path)
    assert got.read_bytes() == data, "不支持续传时必须从头写，不能追加"


def test_sha_mismatch_discards_partial(server, tmp_path):
    data = _Handler.payload
    bad = pd.Part(name="core.zip", urls=(server,), bytes=len(data), sha256="d" * 64)
    with pytest.raises(pd.DownloadError):
        pd.download_part(bad, tmp_path)
    assert not (tmp_path / "core.zip.part").exists(), "校验失败必须删掉不可信半成品"
    assert not (tmp_path / "core.zip").exists()


def test_size_mismatch_rejected(server, tmp_path):
    data = _Handler.payload
    bad = pd.Part(name="core.zip", urls=(server,), bytes=len(data) + 10, sha256=_sha(data))
    with pytest.raises(pd.DownloadError):
        pd.download_part(bad, tmp_path)


def test_progress_callback_reports_totals(server, tmp_path):
    data = _Handler.payload
    seen = []
    pd.download_part(_part(server, data), tmp_path,
                     progress=lambda got, total: seen.append((got, total)))
    assert seen and seen[-1][0] == len(data) and seen[-1][1] == len(data)


def test_existing_valid_file_short_circuits(server, tmp_path):
    data = _Handler.payload
    (tmp_path / "core.zip").write_bytes(data)
    got = pd.download_part(_part(server, data), tmp_path)
    assert got.read_bytes() == data


def test_unreachable_host_raises_clear_error(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                  # 端口已释放 → 连接被拒
    with pytest.raises(pd.DownloadError):
        pd.download_part(
            pd.Part(name="x.zip", urls=(f"http://127.0.0.1:{port}/x",),
                    bytes=10, sha256="e" * 64),
            tmp_path, timeout=2.0)


# ------------------------------------------------------------------ 安装
def _real_webp(path, marker: int, *, size=(32, 24)) -> None:
    """写一张**真实可解码**的 webp 帧（安装校验现在真的解码抽查）。

    ``marker`` 染进像素，两张不同 marker 的帧字节必然不同——原来用
    ``b"OLD"/b"NEW"`` 做的"回滚后字节必须一致"断言改为比对像素内容。
    """
    from PySide6.QtGui import QImage
    img = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
    img.fill(0xFF000000 | ((marker * 37 % 256) << 8) | (marker % 256 + 1))
    assert img.save(str(path), "webp", 100)


def _build_part_zip(tmp_path, *, clips=2, frames=3, bad_digest=False, size=(32, 24)):
    """造一个真实分件 zip（含 part.json/manifest.json/**真 webp 帧与 meta**）。

    画布声明与帧的真实尺寸一致（安装校验会真的解码比对，32×24 冒充 640×360
    的包会被拒——终审点名的那条）。
    """
    import json
    import zipfile
    from pathlib import Path as _P

    src = tmp_path / "src"
    parts = []
    zpath = tmp_path / "part.zip"
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_STORED) as zf:
        for i in range(clips):
            name = f"clip{i}"
            d = src / "anim" / "idle" / name
            d.mkdir(parents=True)
            for f in range(1, frames + 1):
                _real_webp(d / f"f_{f:04d}.webp", i * 16 + f, size=size)
            digest = pd.frame_digest(d)
            (d / "meta.json").write_text(json.dumps(
                {"fps": 24.0, "frames": frames, "content_sha": digest}), encoding="utf-8")
            parts.append({"name": name, "category": "idle", "dir": f"anim/idle/{name}",
                          "frames": frames, "key_every": 12, "part": "core",
                          "content_sha": ("d" * 64) if bad_digest else digest,
                          "bytes": 10 * frames})
            for f in sorted(d.iterdir()):
                zf.write(f, f"anim/idle/{name}/{f.name}")
        zf.writestr("part.json", json.dumps({
            "schema": 1, "id": "shenshen", "name": "深深", "canvas": list(size),
            "fps": 24.0, "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
            "parts": ["core"], "clips": parts}, ensure_ascii=False))
        zf.writestr("manifest.json", json.dumps({"body_box": [1, 2, 3, 4]}))
    return zpath, parts


def test_frame_digest_matches_builder_formula(tmp_path):
    """安装侧与打包侧必须是同一口径（否则装不上）。"""
    d = tmp_path / "clip"
    d.mkdir()
    (d / "f_0001.webp").write_bytes(b"abc")
    (d / "f_0002.webp").write_bytes(b"def")
    import hashlib
    h = hashlib.sha256()
    for n, b in (("f_0001.webp", b"abc"), ("f_0002.webp", b"def")):
        h.update(n.encode()); h.update(b"\0"); h.update(b)
    assert pd.frame_digest(d) == h.hexdigest()


def test_install_part_extracts_and_merges_index(tmp_path):
    zpath, parts = _build_part_zip(tmp_path)
    target = tmp_path / "char"
    info = pd.install_part(zpath, target)
    assert info["clips"] == len(parts)
    import json
    idx = json.loads((target / "character.json").read_text(encoding="utf-8"))
    assert idx["id"] == "shenshen"
    assert [c["name"] for c in idx["clips"]] == ["clip0", "clip1"]
    assert (target / "anim/idle/clip0/f_0001.webp").is_file()
    assert (target / "manifest.json").is_file()
    assert not list(target.glob(".install-*")), "临时目录必须清掉"


def test_install_part_is_idempotent(tmp_path):
    zpath, _ = _build_part_zip(tmp_path)
    target = tmp_path / "char"
    pd.install_part(zpath, target)
    pd.install_part(zpath, target)          # 再装一次不炸、索引不重复
    import json
    idx = json.loads((target / "character.json").read_text(encoding="utf-8"))
    assert len(idx["clips"]) == 2


def test_install_part_rejects_bad_digest_and_installs_nothing(tmp_path):
    zpath, _ = _build_part_zip(tmp_path, bad_digest=True)
    target = tmp_path / "char"
    with pytest.raises(pd.DownloadError):
        pd.install_part(zpath, target)
    assert not (target / "anim").exists(), "校验不过不许落地任何帧"
    assert not (target / "character.json").exists()


def test_install_part_rejects_path_traversal(tmp_path):
    import zipfile
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("part.json", "{}")
        zf.writestr("../../evil.txt", "boom")
    with pytest.raises(pd.DownloadError):
        pd.install_part(zpath, tmp_path / "char")


def test_install_part_rejects_traversal_in_part_json(tmp_path):
    """ZIP 成员路径合法 ≠ part.json 里的 dir 合法。

    终审点名的高危路径：`clip.dir` 会被拿去 `rmtree`，绝对路径或上跳能删到
    目标目录之外。这条用例必须让它在**任何落地动作之前**就被拒。
    """
    import json
    import zipfile

    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("must survive", encoding="utf-8")

    zpath = tmp_path / "evil2.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("part.json", json.dumps({
            "schema": 1, "id": "x", "name": "x", "canvas": [1, 1], "fps": 24.0,
            "pipeline": "p", "format": "f", "parts": ["core"],
            "clips": [{"name": "c", "category": "idle",
                       "dir": "../../victim", "frames": 1, "key_every": 12,
                       "part": "core", "content_sha": "a" * 64, "bytes": 1}],
        }))
        zf.writestr("f_0001.webp", b"x")

    target = tmp_path / "characters" / "x" / "1.0.0-core-640x360"
    with pytest.raises(pd.DownloadError):
        pd.install_part(zpath, target)
    assert (victim / "keep.txt").is_file(), "目标目录外的内容绝不能被删"


def test_recover_target_restores_old_dir(tmp_path):
    """崩溃残留：旧目录已改名、新目录没就位 → 必须把旧目录改回来。

    宁可继续用旧素材，也不能让那段动画凭空消失（终审点名的半装状态）。
    """
    target = tmp_path / "char"
    clip = target / "anim" / "idle" / "c"
    old = clip.with_name(clip.name + pd.OLD_SUFFIX)
    old.mkdir(parents=True)
    (old / "f_0001.webp").write_bytes(b"x")
    info = pd.recover_target(target)
    assert info["restored"] == 1
    assert clip.is_dir() and (clip / "f_0001.webp").is_file()
    assert not old.exists()


def test_recover_target_cleans_staging_and_obsolete(tmp_path):
    target = tmp_path / "char"
    stage = target / ".install-999-dead"
    stage.mkdir(parents=True)
    (stage / "junk").write_bytes(b"x")
    clip = target / "anim" / "idle" / "c"
    clip.mkdir(parents=True)
    old = clip.with_name(clip.name + pd.OLD_SUFFIX)
    old.mkdir()
    info = pd.recover_target(target)
    assert not stage.exists()
    assert not old.exists(), "新旧都在时，旧的直接清掉"
    assert clip.is_dir()
    assert info["removed"] >= 2


def test_target_lock_is_exclusive_and_released_on_exit(tmp_path):
    """OS 级互斥：持有期间第二个实例拿不到；退出后立即可再拿。

    锁文件只是内核锁的载体（内容无意义，退出后不删除）；互斥由
    msvcrt/fcntl 保证，进程死亡内核自动放锁——没有"死锁残留接管"问题。
    """
    target = tmp_path / "char"
    target.mkdir()
    with pd._TargetLock(target, timeout=5):
        lock = target / pd.LOCK_NAME
        assert lock.is_file()
        with pytest.raises(pd.DownloadError):
            with pd._TargetLock(target, timeout=0.2):
                pass
    with pd._TargetLock(target, timeout=2):     # 退出后必须能再拿
        pass
    # 残留内容的旧锁文件不影响获取（内核锁不看内容）
    (target / pd.LOCK_NAME).write_text("dead", encoding="utf-8")
    with pd._TargetLock(target, timeout=2):
        pass


def test_install_part_leaves_no_old_suffix_and_no_lock(tmp_path):
    zpath, _ = _build_part_zip(tmp_path)
    target = tmp_path / "char"
    pd.install_part(zpath, target)
    assert not list(target.rglob(f"*{pd.OLD_SUFFIX}"))
    assert not list(target.glob(f"{pd.STAGE_PREFIX}*"))


@pytest.mark.parametrize("bad_id,bad_ver,bad_variant", [
    ("../evil", "2.0.0", "base"),
    ("/abs", "2.0.0", "base"),
    ("C:\\win", "2.0.0", "base"),
    ("ok", "..", "base"),
    ("ok", "2.0.0", "../x"),
])
def test_registry_rejects_path_components(bad_id, bad_ver, bad_variant):
    """`id/version/variant` 会被拼进安装路径：必须是单个安全组件。

    终审点名：绝对 id 或带 `..` 的版本能把整个 target_dir 带到角色根之外，
    而后续基于 target_dir 的越界检查会一并失效（信任根已经错了）。
    """
    reg = _registry()
    reg["packs"][0]["id"] = bad_id
    reg["packs"][0]["version"] = bad_ver
    reg["packs"][0]["variant"] = bad_variant
    with pytest.raises(pd.RegistryError):
        pd.parse_registry(reg)


def test_registry_accepts_normal_components():
    packs = pd.parse_registry(_registry())
    assert packs[0].id == "shenshen" and packs[0].variant == "base"


# ---------------------------------------------------------------- 安装事务日志 / 启动恢复
def _placed_clip(target, rel, *, frames=3, sha=None):
    """在目标目录里造一段"已落地"的 clip（含 meta.content_sha 凭证）。"""
    import json
    from pathlib import Path as _P

    d = _P(target) / rel
    d.mkdir(parents=True, exist_ok=True)
    for f in range(1, frames + 1):
        (d / f"f_{f:04d}.webp").write_bytes(b"x" * 10 + bytes([1, f]))
    digest = pd.frame_digest(d)
    (d / "meta.json").write_text(
        json.dumps({"fps": 24.0, "frames": frames, "content_sha": sha or digest}),
        encoding="utf-8")
    return digest


def _part_doc(*, clips):
    return {"schema": 1, "id": "shenshen", "name": "深深", "canvas": [640, 360],
            "fps": 24.0, "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
            "parts": ["core"], "clips": clips}


def test_install_part_leaves_no_journal(tmp_path):
    """装成功必须把事务日志删掉（留着会让下次启动反复前滚）。"""
    zpath, _ = _build_part_zip(tmp_path)
    target = tmp_path / "char"
    pd.install_part(zpath, target)
    assert not (target / pd.JOURNAL_NAME).exists()


def test_recover_installs_rejects_incomplete_merge(tmp_path):
    """崩在「帧已落地、索引还没合并」且内容**不完整** → 不承认半个分件。

    终审点名的缺陷：原实现把磁盘上就位的任意子集前滚进索引，缺段的分件被
    当成"已安装"，下载页不再提供重下。现在：分件声明的段没全在盘 = 安装已
    损坏 → 回滚落地的内容、不进索引、按"未安装"处理（用户重下）。
    """
    import json

    target = tmp_path / "char"
    target.mkdir()
    sha0 = _placed_clip(target, "anim/idle/clip0")
    doc = _part_doc(clips=[
        {"name": "clip0", "category": "idle", "dir": "anim/idle/clip0",
         "frames": 3, "part": "core", "content_sha": sha0},
        {"name": "clip1", "category": "idle", "dir": "anim/idle/clip1",
         "frames": 3, "part": "core", "content_sha": "e" * 64},   # 没落地
    ])
    pd._write_journal(target, {"schema": 1, "archive": "core",
                               "phase": "merge", "part_doc": doc})

    info = pd.recover_installs(target)
    assert info["journal"] is True and info["merged"] == 0, "半个分件不许进索引"
    assert not (target / pd.JOURNAL_NAME).exists(), "按未安装收口后日志必须删除"
    assert not (target / "character.json").exists(), "缺段不许合并索引"
    assert not (target / "anim/idle/clip0").exists(), "落地的半套必须回滚"

    # 幂等：再收一次没有日志 → 什么都不做
    again = pd.recover_installs(target)
    assert again["journal"] is False and again["merged"] == 0


def test_recover_installs_rolls_forward_complete_merge(tmp_path):
    """崩在合并前但分件**完整**在盘 → 前滚合并，并在提交后清掉 __old__。"""
    import json

    target = tmp_path / "char"
    target.mkdir()
    sha0 = _placed_clip(target, "anim/idle/clip0")
    sha1 = _placed_clip(target, "anim/idle/clip1")
    doc = _part_doc(clips=[
        {"name": "clip0", "category": "idle", "dir": "anim/idle/clip0",
         "frames": 3, "part": "core", "content_sha": sha0},
        {"name": "clip1", "category": "idle", "dir": "anim/idle/clip1",
         "frames": 3, "part": "core", "content_sha": sha1},
    ])
    pd._write_journal(target, {"schema": 1, "archive": "core",
                               "phase": "merge", "part_doc": doc})

    info = pd.recover_installs(target)
    assert info["journal"] is True and info["merged"] == 2
    idx = json.loads((target / "character.json").read_text(encoding="utf-8"))
    assert [c["name"] for c in idx["clips"]] == ["clip0", "clip1"]
    assert not (target / pd.JOURNAL_NAME).exists()


def test_recover_installs_restores_old_dir_when_placement_crashed(tmp_path):
    """崩在逐个落地阶段 → 旧目录必须还原，且不许合并半套索引。"""
    import json

    target = tmp_path / "char"
    target.mkdir()
    clip = target / "anim" / "idle" / "c"
    old = clip.with_name(clip.name + pd.OLD_SUFFIX)
    old.mkdir(parents=True)
    (old / "f_0001.webp").write_bytes(b"x")
    pd._write_journal(target, {"schema": 1, "archive": "core", "phase": "place",
                               "part_doc": _part_doc(clips=[
                                   {"name": "c", "category": "idle", "dir": "anim/idle/c",
                                    "frames": 1, "part": "core", "content_sha": "f" * 64}])})

    info = pd.recover_installs(target)
    assert info["journal"] is True and info["restored"] == 1
    assert clip.is_dir() and (clip / "f_0001.webp").is_file(), "旧素材必须回来"
    assert not old.exists()
    assert not (target / "character.json").exists(), "半套不装，不许合并索引"
    assert not (target / pd.JOURNAL_NAME).exists()


def test_recover_installs_no_journal_is_a_noop(tmp_path):
    """常规启动：没有日志 → 一次 is_file() 就返回，不建锁、不动索引。"""
    target = tmp_path / "char"
    target.mkdir()
    info = pd.recover_installs(target)
    assert info["journal"] is False and info["merged"] == 0
    assert not (target / pd.LOCK_NAME).exists()
    assert not (target / "character.json").exists()


def test_recover_installs_skips_when_install_in_flight(tmp_path):
    """有安装正持锁 → 跳过收口（它自己会收），日志不能被提前删掉。"""
    target = tmp_path / "char"
    target.mkdir()
    pd._write_journal(target, {"schema": 1, "archive": "core", "phase": "merge",
                               "part_doc": _part_doc(clips=[])})
    with pd._TargetLock(target, timeout=5):
        info = pd.recover_installs(target, timeout=0.1)
    assert info.get("skipped") is True
    assert (target / pd.JOURNAL_NAME).exists(), "安装还在跑，日志不许动"


def test_recover_installs_survives_corrupt_journal(tmp_path):
    """坏日志不许炸启动（用户可能被断电/杀进程留下半个 journal）。

    旧实现里 ``part_doc["clips"]`` 若不是 list，``clip.get`` 会抛 AttributeError
    一路穿到 ``MovieLibrary._load_all`` —— 素材坏了不该让整个应用起不来。
    """
    import json

    target = tmp_path / "char"
    target.mkdir()
    target.joinpath(pd.JOURNAL_NAME).write_text("{not json at all", encoding="utf-8")
    info = pd.recover_installs(target)
    assert info["journal"] is True and info["merged"] == 0
    assert not (target / pd.JOURNAL_NAME).exists(), "读不出来的日志要清理掉"

    # clips 不是 list（被手改/截断）
    target.joinpath(pd.JOURNAL_NAME).write_text(
        json.dumps({"phase": "merge", "part_doc": {"clips": "oops"}}), encoding="utf-8")
    info = pd.recover_installs(target)
    assert info["merged"] == 0
    assert not (target / "character.json").exists(), "拿不到可信 clips 就不许写索引"


def test_placement_crash_rolls_back_to_old_material(tmp_path):
    """``phase=place``（索引还没提交）→ 回滚到旧素材，而不是留下"新内容+旧索引"。"""
    import json

    target = tmp_path / "char"
    clip_new = target / "anim" / "idle" / "c"
    old = clip_new.with_name(clip_new.name + pd.OLD_SUFFIX)
    clip_new.mkdir(parents=True)
    (clip_new / "f_0001.webp").write_bytes(b"NEW")
    old.mkdir(parents=True)
    (old / "f_0001.webp").write_bytes(b"OLD")
    pd._write_journal(target, {"schema": 1, "archive": "core", "phase": "place",
                               "part_doc": _part_doc(clips=[])})

    info = pd.recover_installs(target)
    assert info["rolled_back"] == 1, "未提交的新内容必须被旧素材覆盖回去"
    assert (clip_new / "f_0001.webp").read_bytes() == b"OLD"
    assert not (target / "character.json").exists()


def test_index_commit_failure_keeps_rollback_material(tmp_path, monkeypatch):
    """合并索引失败 → 旧目录与日志都必须留着（否则回滚依据和重试依据都没了）。"""
    zpath, _ = _build_part_zip(tmp_path)
    target = tmp_path / "char"
    # 先正常装一次，制造"旧版本"
    pd.install_part(zpath, target)
    before = (target / "anim/idle/clip0/f_0001.webp").read_bytes()

    real_merge = pd._merge_index

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(pd, "_merge_index", boom)
    with pytest.raises(OSError):
        pd.install_part(zpath, target)
    monkeypatch.setattr(pd, "_merge_index", real_merge)

    assert (target / pd.JOURNAL_NAME).exists(), "崩溃/失败后的日志必须留着"
    assert list(target.rglob(f"*{pd.OLD_SUFFIX}")), "旧目录是回滚依据，不许提前删"
    # 收口后索引恢复一致
    pd.recover_installs(target)
    import json
    idx = json.loads((target / "character.json").read_text(encoding="utf-8"))
    assert [c["name"] for c in idx["clips"]] == ["clip0", "clip1"]
    assert not list(target.rglob(f"*{pd.OLD_SUFFIX}")), "提交后旧目录必须清掉"
    assert (target / "anim/idle/clip0/f_0001.webp").read_bytes() == before


def test_roll_forward_failure_keeps_journal(tmp_path, monkeypatch):
    """前滚写不进去（只读/占用）→ 日志保留，等下次重试（终审 B3）。"""
    target = tmp_path / "char"
    target.mkdir()
    sha0 = _placed_clip(target, "anim/idle/clip0")
    pd._write_journal(target, {"schema": 1, "archive": "core", "phase": "merge",
                               "part_doc": _part_doc(clips=[
                                   {"name": "clip0", "category": "idle",
                                    "dir": "anim/idle/clip0", "frames": 3,
                                    "part": "core", "content_sha": sha0}])})

    def boom(*_a, **_k):
        raise OSError("read-only")

    monkeypatch.setattr(pd, "_merge_index", boom)
    info = pd.recover_installs(target)
    assert info["merged"] == 0
    assert (target / pd.JOURNAL_NAME).exists(), "失败不许删日志"


def test_lock_is_not_stolen_from_a_live_holder(tmp_path):
    """锁被活着的实例持有 → 第二个实例必须超时失败（OS 级互斥）。"""
    target = tmp_path / "char"
    target.mkdir()
    with pd._TargetLock(target, timeout=5):
        with pytest.raises(pd.DownloadError):
            with pd._TargetLock(target, timeout=0.3):
                pass


def test_registry_allows_same_version_in_two_resolutions():
    """同版本号的 640 与 720 是两条产品线，不是重复包（去重键含分辨率）。"""
    base = {"schema": 1, "id": "shenshen", "variant": "base", "version": "1.0.0",
            "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
            "parts": [{"name": "core", "urls": ["https://github.com/MerZlin/dsh-pet-indesktop/releases/download/x/y.zip"],
                       "bytes": 10, "sha256": "a" * 64}]}
    r640 = dict(base, name="640", canvas=[640, 360])
    r720 = dict(base, name="720", canvas=[1280, 720])
    packs = pd.parse_registry({"schema": 1, "packs": [r640, r720]})
    assert len(packs) == 2, "640 与 720 必须能并存"
    with pytest.raises(pd.RegistryError, match="重复"):
        pd.parse_registry({"schema": 1, "packs": [r640, dict(r640)]})


def test_install_writes_receipt(tmp_path):
    """装完写安装凭证：来源/摘要/字节/时间（更新检测与排查靠它，不回扫远端）。"""
    import json

    zpath, _ = _build_part_zip(tmp_path)
    target = tmp_path / "char"
    pd.install_part(zpath, target)
    receipt = json.loads((target / pd.RECEIPT_NAME).read_text(encoding="utf-8"))
    assert receipt["archive"] == zpath.name
    assert receipt["archive_sha256"] == pd._hash_file(zpath)
    assert receipt["archive_bytes"] == zpath.stat().st_size
    assert receipt["clips"] == 2
    assert receipt["installed_at"] > 0


def test_install_refuses_when_previous_transaction_kept(tmp_path, monkeypatch):
    """上一笔事务前滚失败（日志保留）→ 新安装必须拒绝，不许覆盖重试依据（六轮）。"""
    zpath, _ = _build_part_zip(tmp_path)
    target = tmp_path / "char"
    target.mkdir()
    # 造一笔"前滚失败"的事务：段在盘、日志 phase=merge，但合并写不进去
    sha0 = _placed_clip(target, "anim/idle/clip0")
    pd._write_journal(target, {"schema": 1, "archive": "core", "phase": "merge",
                               "part_doc": _part_doc(clips=[
                                   {"name": "clip0", "category": "idle",
                                    "dir": "anim/idle/clip0", "frames": 3,
                                    "part": "core", "content_sha": sha0}])})

    def boom(*_a, **_k):
        raise OSError("read-only")

    monkeypatch.setattr(pd, "_merge_index", boom)
    pd.recover_installs(target)          # 前滚失败 → 日志保留
    assert (target / pd.JOURNAL_NAME).exists()
    with pytest.raises(pd.DownloadError, match="未完成的安装事务"):
        pd.install_part(zpath, target)


def test_resume_skips_request_when_partial_is_full_size(server, tmp_path):
    """半成品恰好全长（上次崩在 rename 前）→ 不发请求直接复核摘要（六轮：416 卡死）。"""
    payload = b"z" * 4096
    part = pd.Part(name="core.zip", urls=("http://127.0.0.1:1/never",),
                   bytes=len(payload), sha256=_sha(payload))
    partial = tmp_path / "core.zip.part"
    partial.write_bytes(payload)
    # URL 不可达：若它真的发请求就会失败；成功只能说明没发请求、直接复核通过
    out = pd.download_part(part, tmp_path)
    assert out.read_bytes() == payload


def test_identity_rejects_dir_takeover_by_new_name(tmp_path):
    """新名字指向已有 clip 的目录 = 绕过按名查重换别人的目录，必须拒（六轮）。"""
    zpath, parts = _build_part_zip(tmp_path)
    target = tmp_path / "char"
    pd.install_part(zpath, target)
    # 造第二个分件：clips 名不同，但 dir 指向已装的 anim/idle/clip0
    import json as _json
    import zipfile
    src = tmp_path / "src2"
    d = src / "anim" / "idle" / "clip0"
    d.mkdir(parents=True)
    _real_webp(d / "f_0001.webp", 99)
    (d / "meta.json").write_text(_json.dumps({"fps": 24.0, "frames": 1}), encoding="utf-8")
    z2 = tmp_path / "evil2.zip"
    with zipfile.ZipFile(z2, "w", compression=zipfile.ZIP_STORED) as zf:
        for f in sorted(d.iterdir()):
            zf.write(f, f"anim/idle/clip0/{f.name}")
        zf.writestr("part.json", _json.dumps({
            "schema": 1, "id": "shenshen", "name": "深深", "canvas": [32, 24],
            "fps": 24.0, "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
            "parts": ["extra"], "clips": [{
                "name": "新名字", "category": "random", "dir": "anim/idle/clip0",
                "frames": 1, "key_every": 12, "part": "extra",
                "content_sha": pd.frame_digest(d), "bytes": 1}]}, ensure_ascii=False))
    with pytest.raises(pd.DownloadError, match="目录归属冲突"):
        pd.install_part(z2, target)


def test_incomplete_merge_rolls_back_new_sidecar(tmp_path):
    """phase=merge 缺段回滚时，没有旧备份的新 sidecar 也要撤掉（六轮）。"""
    import json as _json

    target = tmp_path / "char"
    target.mkdir()
    sha0 = _placed_clip(target, "anim/idle/clip0")
    doc = _part_doc(clips=[
        {"name": "clip0", "category": "idle", "dir": "anim/idle/clip0",
         "frames": 3, "part": "core", "content_sha": sha0},
        {"name": "clip1", "category": "idle", "dir": "anim/idle/clip1",
         "frames": 3, "part": "core", "content_sha": "e" * 64},
    ])
    (target / "manifest.json").write_text(_json.dumps({"body_box": [1, 2, 3, 4]}),
                                          encoding="utf-8")
    pd._write_journal(target, {"schema": 1, "archive": "core", "phase": "merge",
                               "part_doc": doc, "sidecars": {"manifest.json": "new"}})
    pd.recover_installs(target)
    assert not (target / "manifest.json").exists(), "新 sidecar 必须随回滚撤掉"
    assert not (target / "character.json").exists()


def test_zip_with_explicit_dir_entries_installs(tmp_path):
    """含显式目录项（``anim/``）的合法 zip 必须能装（七轮：目录项被误拒）。"""
    import json as _json
    import zipfile

    src = tmp_path / "srcd"
    d = src / "anim" / "idle" / "c0"
    d.mkdir(parents=True)
    _real_webp(d / "f_0001.webp", 7)
    digest = pd.frame_digest(d)
    (d / "meta.json").write_text(_json.dumps(
        {"fps": 24.0, "frames": 1, "content_sha": digest}), encoding="utf-8")
    zpath = tmp_path / "withdirs.zip"
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("anim/", "")
        zf.writestr("anim/idle/", "")
        zf.writestr("anim/idle/c0/", "")
        for f in sorted(d.iterdir()):
            zf.write(f, f"anim/idle/c0/{f.name}")
        zf.writestr("part.json", _json.dumps({
            "schema": 1, "id": "shenshen", "name": "深深", "canvas": [32, 24],
            "fps": 24.0, "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
            "parts": ["core"], "clips": [{
                "name": "c0", "category": "idle", "dir": "anim/idle/c0",
                "frames": 1, "key_every": 12, "part": "core",
                "content_sha": digest, "bytes": 1}]}, ensure_ascii=False))
    info = pd.install_part(zpath, tmp_path / "char")
    assert info["clips"] == 1
    assert (tmp_path / "char" / "anim/idle/c0/f_0001.webp").is_file()


def test_identity_rejects_dot_alias_dir_takeover(tmp_path):
    """``anim/idle/./clip0`` 这类别名绕过目录所有权检查，必须拒（七轮）。"""
    zpath, parts = _build_part_zip(tmp_path)
    target = tmp_path / "char"
    pd.install_part(zpath, target)
    import json as _json
    import zipfile
    src = tmp_path / "src3"
    d = src / "anim" / "idle" / "clip0"
    d.mkdir(parents=True)
    _real_webp(d / "f_0001.webp", 55)
    (d / "meta.json").write_text(_json.dumps({"fps": 24.0, "frames": 1}), encoding="utf-8")
    z2 = tmp_path / "alias.zip"
    with zipfile.ZipFile(z2, "w", compression=zipfile.ZIP_STORED) as zf:
        for f in sorted(d.iterdir()):
            zf.write(f, f"anim/idle/clip0/{f.name}")
        zf.writestr("part.json", _json.dumps({
            "schema": 1, "id": "shenshen", "name": "深深", "canvas": [32, 24],
            "fps": 24.0, "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
            "parts": ["extra"], "clips": [{
                "name": "新名字", "category": "random", "dir": "anim/idle/./clip0",
                "frames": 1, "key_every": 12, "part": "extra",
                "content_sha": pd.frame_digest(d), "bytes": 1}]}, ensure_ascii=False))
    with pytest.raises(pd.DownloadError, match="目录归属冲突"):
        pd.install_part(z2, target)


def test_part_doc_missing_schema_and_name_still_merges_valid_index(tmp_path):
    """part.json 缺 schema/name：预检补的默认必须**就地生效**——合并落盘的
    character.json 不许带 None（七轮：预检与落盘不一致）。"""
    import json as _json
    import zipfile

    src = tmp_path / "src4"
    d = src / "anim" / "idle" / "c0"
    d.mkdir(parents=True)
    _real_webp(d / "f_0001.webp", 11)
    digest = pd.frame_digest(d)
    (d / "meta.json").write_text(_json.dumps(
        {"fps": 24.0, "frames": 1, "content_sha": digest}), encoding="utf-8")
    zpath = tmp_path / "nodefaults.zip"
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_STORED) as zf:
        for f in sorted(d.iterdir()):
            zf.write(f, f"anim/idle/c0/{f.name}")
        # 故意不给 schema / name
        zf.writestr("part.json", _json.dumps({
            "id": "shenshen", "canvas": [32, 24],
            "fps": 24.0, "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
            "parts": ["core"], "clips": [{
                "name": "c0", "category": "idle", "dir": "anim/idle/c0",
                "frames": 1, "key_every": 12, "part": "core",
                "content_sha": digest, "bytes": 1}]}, ensure_ascii=False))
    target = tmp_path / "char"
    pd.install_part(zpath, target)
    from pet import character_pack as cp
    index = cp.load_index(target)
    assert index is not None and index.schema == 1 and index.name == "shenshen"


def test_place_crash_removes_backupless_sidecar(tmp_path):
    """place 阶段崩在 sidecar 复制中途：没有旧备份的新 sidecar 必须被撤掉（七轮）。"""
    import json as _json

    target = tmp_path / "char"
    target.mkdir()
    (target / "manifest.json").write_text(_json.dumps({"body_box": [9, 9, 9, 9]}),
                                          encoding="utf-8")
    pd._write_journal(target, {"schema": 1, "archive": "core", "phase": "place",
                               "part_doc": _part_doc(clips=[]),
                               "sidecars": {"manifest.json": "new"}})
    info = pd.recover_installs(target)
    assert not (target / "manifest.json").exists(), "未提交的新 sidecar 必须撤掉"
    assert not (target / pd.JOURNAL_NAME).exists()


def test_place_crash_preserves_untouched_old_sidecar(tmp_path):
    """八轮 P1：崩在 clip 放置阶段（sidecar 还没碰）→ 盘上已有的旧 manifest 不许删。"""
    import json as _json

    target = tmp_path / "char"
    target.mkdir()
    (target / "manifest.json").write_text(_json.dumps({"body_box": [1, 1, 1, 1]}),
                                          encoding="utf-8")
    pd._write_journal(target, {"schema": 1, "archive": "core", "phase": "place",
                               "part_doc": _part_doc(clips=[]), "sidecars": {}})
    pd.recover_installs(target)
    assert (target / "manifest.json").is_file(), "尚未触碰的旧 sidecar 不许被回滚删掉"


def test_place_recovery_keeps_replaced_sidecar_restored(tmp_path):
    """八轮 P1："replaced" 的旧 sidecar 由 __old__ 恢复后，不许被清理循环再删。"""
    import json as _json

    target = tmp_path / "char"
    target.mkdir()
    old = target / "manifest.json.__old__"
    old.write_text(_json.dumps({"body_box": [2, 2, 2, 2]}), encoding="utf-8")
    (target / "manifest.json").write_text(_json.dumps({"body_box": [9, 9, 9, 9]}),
                                          encoding="utf-8")
    pd._write_journal(target, {"schema": 1, "archive": "core", "phase": "place",
                               "part_doc": _part_doc(clips=[]),
                               "sidecars": {"manifest.json": "replaced"}})
    info = pd.recover_installs(target)
    assert info["rolled_back"] >= 1
    text = (target / "manifest.json").read_text(encoding="utf-8")
    assert "2, 2, 2, 2" in text, "恢复回来的旧 sidecar 必须还在（且是旧内容）"
    assert not old.exists()


class _FlakyHandler(http.server.BaseHTTPRequestHandler):
    """第一次请求只给一半就断流（Content-Length 虚报全长）；Range 续传给剩余。"""

    payload: bytes = b""

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        data = type(self).payload
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            body = data[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            # 断流：声称全长、只发一半就关连接
            body = data[: len(data) // 2]
            self.send_response(200)
        self.send_header("Content-Length", str(len(data) if not rng else len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_transient_drop_is_retried_and_resumed(tmp_path):
    """断流（响应截短）必须自动重试并从断点续传——不许让人点第二次（实机需求）。"""
    import http.server
    import threading

    _FlakyHandler.payload = b"z" * 60000
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FlakyHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/p.bin"
        part = pd.Part(name="p.zip", urls=(url,), bytes=60000,
                       sha256=_sha(b"z" * 60000))
        out = pd.download_part(part, tmp_path)
        assert out.read_bytes() == b"z" * 60000
    finally:
        srv.shutdown()
        srv.server_close()


def test_http_404_switches_url_without_retry(tmp_path):
    """404 是永久错误：不许同址重试 10 次（白等半分钟），立即换下一个地址。"""
    import http.server
    import threading
    import time

    class _404(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            self.send_response(404)
            self.end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _404)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        bad = f"http://127.0.0.1:{srv.server_address[1]}/missing.zip"
        part = pd.Part(name="p.zip", urls=(bad,), bytes=10, sha256="a" * 64)
        t0 = time.perf_counter()
        try:
            pd.download_part(part, tmp_path)
            raise AssertionError("404 必须失败")
        except pd.DownloadError:
            pass
        assert time.perf_counter() - t0 < 5, "404 不许进入断流重试（立即换源）"
    finally:
        srv.shutdown()
        srv.server_close()
