# -*- coding: utf-8 -*-
"""macOS 上定位四个播放器的 .app（``pet/music/players.py`` 的 darwin 分支）。

真实文件系统（tmp 目录里搭 ``Foo.app/Contents/Info.plist``），只把一处操作系统
边界换掉：「应用程序」根目录列表（真机是 /Applications 与 ~/Applications）。
定位与 Windows 侧同一条纪律：纯本地有界查找，不起进程（不调 Spotlight）——由
``test_music_player_cache.py::test_detection_stays_local_and_bounded`` 钉住。
"""
from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from pet.music import players as music_players

# 模块导入时捕获真实现：conftest 的禁扫桩（``_no_filesystem_player_scan``）会在用例
# 运行期把 ``_search`` 换成恒 None。本文件的扫描都落在 tmp 目录（根目录列表已换成
# 替身），可以安全地恢复真实现（同 test_music_player_cache.py 口径）。
_REAL_SEARCH = music_players._search


def _make_app(root: Path, name: str, bundle_id: str = "") -> Path:
    app = root / name
    (app / "Contents" / "MacOS").mkdir(parents=True)
    info = {"CFBundleName": app.stem}
    if bundle_id:
        info["CFBundleIdentifier"] = bundle_id
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    return app


@pytest.fixture
def mac_roots(tmp_path, monkeypatch):
    """darwin + 两个受控的「应用程序」根目录。"""
    system = tmp_path / "Applications"
    user = tmp_path / "home" / "Applications"
    system.mkdir()
    user.mkdir(parents=True)
    monkeypatch.setattr(music_players.sys, "platform", "darwin")
    monkeypatch.setattr(music_players, "_MAC_APP_ROOTS", (str(system), str(user)))
    monkeypatch.setattr(music_players, "_search", _REAL_SEARCH)
    music_players.clear_cache()
    yield {"system": system, "user": user}
    music_players.clear_cache()


def test_every_registered_player_has_a_mac_layout():
    """注册表里每个播放器都要有 mac 安装布局（新增播放器漏登记 = mac 上永远找不到）。"""
    assert set(music_players._MAC_APPS) == set(music_players.player_keys())
    for key, (names, bundle_ids) in music_players._MAC_APPS.items():
        assert names and all(name.endswith(".app") for name in names), key
        assert bundle_ids, key


@pytest.mark.parametrize("key, name", [
    ("netease", "NeteaseMusic.app"),
    ("qqmusic", "QQMusic.app"),
    ("kugou", "KugouMusic.app"),
    ("soda", "汽水音乐.app"),
])
def test_finds_player_app_in_applications(mac_roots, key, name):
    app = _make_app(mac_roots["system"], name)
    assert music_players.find_player(key) == str(app)


def test_user_applications_folder_is_searched_too(mac_roots):
    app = _make_app(mac_roots["user"], "网易云音乐.app")
    assert music_players.find_player("netease") == str(app)


def test_folder_without_bundle_contents_is_not_an_app(mac_roots):
    """名字对、但不是应用包（没有 Contents/）：不认，免得把同名空目录当播放器启动。"""
    (mac_roots["system"] / "QQMusic.app").mkdir()
    assert music_players.find_player("qqmusic") is None


def test_renamed_app_is_found_by_its_declared_bundle_id(mac_roots):
    """包名被改过（或 App Store 版用了别的名字）：浅扫一层，按 Info.plist 的 bundle id 认。"""
    _make_app(mac_roots["system"], "Some Other Player.app", "com.example.other")
    renamed = _make_app(mac_roots["user"], "我的网易云.app", "COM.NETEASE.163MUSIC")
    assert music_players.find_player("netease") == str(renamed)


def test_bundle_id_scan_does_not_descend_into_subfolders(mac_roots):
    """浅扫只看根目录下直接的 .app：不进子目录（有界，不是全盘找）。"""
    _make_app(mac_roots["system"] / "Tencent", "Renamed.app", "com.tencent.QQMusicMac")
    assert music_players.find_player("qqmusic") is None


def test_bundle_id_scan_is_bounded(mac_roots, monkeypatch):
    """条目数有上界：超出上限的条目不再读 Info.plist。"""
    monkeypatch.setattr(music_players, "_MAC_SCAN_MAX_ENTRIES", 3)
    for index in range(6):
        _make_app(mac_roots["system"], f"Filler{index}.app", f"com.example.f{index}")
    reads: list[str] = []
    real = music_players.bundle_identifier
    monkeypatch.setattr(music_players, "bundle_identifier",
                        lambda path: (reads.append(path), real(path))[1])
    assert music_players.find_player("soda") is None
    assert len(reads) <= 3 * len(music_players._MAC_APP_ROOTS)


def test_bundle_id_match_is_deterministic(mac_roots):
    """同一根目录里两个包都声明了同一个 bundle id：按包名取第一个，不赌枚举顺序。"""
    _make_app(mac_roots["system"], "Zeta.app", "com.kugou.mac.Music")
    alpha = _make_app(mac_roots["system"], "Alpha.app", "com.kugou.mac.Music")
    assert music_players.find_player("kugou") == str(alpha)


def test_missing_player_is_negatively_cached(mac_roots, monkeypatch):
    assert music_players.find_player("kugou") is None
    scans: list = []
    monkeypatch.setattr(music_players, "_mac_app_entries",
                        lambda root: (scans.append(root), [])[1])
    assert music_players.find_player("kugou") is None
    assert scans == [], "负结果要缓存，不能每次都重扫「应用程序」"
    assert music_players.cached_player("kugou") == (music_players.CACHED_MISSING, None)


def test_manual_app_bundle_path_is_accepted(mac_roots, tmp_path):
    """设置页在 mac 上选的是 .app（一个目录）：手填路径不能只认普通文件。"""
    app = _make_app(tmp_path / "Custom", "MyNetease.app")
    assert music_players.find_player("netease", str(app)) == str(app)
    assert music_players.cached_player("netease", str(app)) == (
        music_players.CACHED_FOUND, str(app))


def test_manual_plain_directory_is_rejected(mac_roots, tmp_path):
    folder = tmp_path / "not-an-app"
    folder.mkdir()
    assert music_players.find_player("netease", str(folder)) is None
    assert music_players.cached_player("netease", str(folder)) == (
        music_players.CACHED_MISSING, None)


def test_app_directories_are_not_launchable_off_mac(monkeypatch, tmp_path):
    """.app 目录只在 macOS 上是"可启动的程序"；其它平台手填一个目录仍判无效。"""
    app = _make_app(tmp_path, "NeteaseMusic.app")
    monkeypatch.setattr(music_players.sys, "platform", "win32")
    assert music_players.find_player("netease", str(app)) is None


def test_app_bundle_root_strips_the_inner_binary_path(tmp_path):
    app = _make_app(tmp_path, "QQMusic.app")
    inner = app / "Contents" / "MacOS" / "QQMusic"
    assert music_players.app_bundle_root(str(inner)) == str(app)
    assert music_players.app_bundle_root(str(app)) == str(app)
    assert music_players.app_bundle_root("D:/CloudMusic/cloudmusic.exe") == "D:/CloudMusic/cloudmusic.exe"


def test_session_key_is_bundle_id_on_mac_and_exe_name_elsewhere(monkeypatch, tmp_path):
    """会话身份：mac 上读应用包的 CFBundleIdentifier（不写死），Windows 上是 exe 名。"""
    app = _make_app(tmp_path, "NeteaseMusic.app", "com.netease.163music")
    monkeypatch.setattr(music_players.sys, "platform", "darwin")
    assert music_players.session_key(str(app)) == "com.netease.163music"
    assert music_players.session_key(str(app / "Contents" / "MacOS" / "x")) == "com.netease.163music"
    assert music_players.session_key(str(tmp_path / "missing.app")) == ""

    monkeypatch.setattr(music_players.sys, "platform", "win32")
    assert music_players.session_key("D:/CloudMusic/cloudmusic.exe") == "cloudmusic.exe"


def test_session_key_rejects_malformed_info_plist(monkeypatch, tmp_path):
    app = tmp_path / "Broken.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(b"not a plist")
    monkeypatch.setattr(music_players.sys, "platform", "darwin")
    assert music_players.session_key(str(app)) == ""

