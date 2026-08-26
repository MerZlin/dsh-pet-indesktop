# -*- coding: utf-8 -*-
"""检查更新与下载模块测试。"""

import http.server
import json
import threading
from pathlib import Path

import pytest

from pet import updater


def test_version_parts_and_is_newer():
    assert updater.version_parts("v3.0.1") == [3, 0, 1]
    assert updater.version_parts("3.0.0") == [3, 0, 0]
    assert updater.version_parts("v10.2") > updater.version_parts("v9.9.9")
    assert updater.version_parts("v3.0.0-beta") == [3, 0, 0]
    assert updater.is_newer("v3.0.1", "3.0.0") is True
    assert updater.is_newer("v3.0.0", "3.0.0") is False
    assert updater.is_newer("v2.9", "3.0.0") is False


def test_pick_asset_windows_prefers_setup(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "APP_DIR_NAME", "dsh-pet-standalone-webm-chat")
    release = {"assets": {
        "dsh-pet-standalone-webm-chat-portable.zip": "z",
        "dsh-pet-standalone-webm-chat-setup.exe": "s",
        "dsh-pet-standalone-webm-macos-arm64.zip": "m",
    }}
    assert updater.pick_asset(release)["name"] == "dsh-pet-standalone-webm-chat-setup.exe"
    assert updater.pick_asset(release)["browser_download_url"] == "s"
    # 无 setup 时回退 portable.zip
    release2 = {"assets": {k: v for k, v in release["assets"].items() if "setup" not in k}}
    assert updater.pick_asset(release2)["name"] == "dsh-pet-standalone-webm-chat-portable.zip"
    # 无匹配资产
    assert updater.pick_asset({"assets": {"other.bin": "x"}}) is None


def test_pick_asset_macos_arm64(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(updater, "APP_DIR_NAME", "dsh-pet-standalone-webm-chat")
    release = {"assets": {"dsh-pet-standalone-webm-chat-macos-arm64.zip": "m"}}
    assert updater.pick_asset(release)["name"].endswith("macos-arm64.zip")


def test_pick_asset_linux_x86_64(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(updater, "APP_DIR_NAME", "dsh-pet-standalone-webm-chat")
    release = {"assets": {
        "dsh-pet-standalone-webm-chat-linux-x86_64.zip": "l",
        "dsh-pet-standalone-webm-chat-macos-arm64.zip": "m",
    }}
    assert updater.pick_asset(release)["name"] == "dsh-pet-standalone-webm-chat-linux-x86_64.zip"
    # 无 zip 时回退 tar.gz
    release2 = {"assets": {"dsh-pet-standalone-webm-chat-linux-x86_64.tar.gz": "t"}}
    assert updater.pick_asset(release2)["name"].endswith(".tar.gz")


def test_pick_asset_source_run_falls_back_to_chat(monkeypatch):
    """源码运行（无变体标识）时按 webm-chat 变体选择资产。"""
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "APP_DIR_NAME", "dsh-pet-standalone")
    release = {"assets": {"dsh-pet-standalone-webm-chat-setup.exe": "s"}}
    assert updater.pick_asset(release) is not None


def test_latest_release_parses_github_api(monkeypatch):
    import io

    def fake_ok(*args, **kwargs):
        body = json.dumps({
            "tag_name": "v3.0.1",
            "html_url": "https://github.com/x/releases",
            "body": "release notes",
            "assets": [
                {"name": "a-setup.exe", "browser_download_url": "https://dl/a-setup.exe"},
            ],
        }).encode()
        return io.BytesIO(body)

    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_ok)
    release = updater.latest_release()
    assert release["version"] == "3.0.1"  # v 前缀被剥离
    assert release["notes"] == "release notes"
    assert release["assets"]["a-setup.exe"] == "https://dl/a-setup.exe"


def test_latest_release_falls_back_to_update_json(monkeypatch):
    """GitHub API 不可达时回退 jsDelivr 上的 update.json。"""
    import io
    import urllib.error

    calls = []

    def fake_urlopen(request, *args, **kwargs):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        calls.append(url)
        if url == updater.RELEASE_API:
            raise urllib.error.URLError("blocked")
        # update.json 镜像
        body = json.dumps({
            "version": "3.0.1",
            "html_url": "https://github.com/x/releases",
            "notes": "cdn notes",
            "assets": {"a-setup.exe": "https://cdn/a-setup.exe"},
        }).encode()
        return io.BytesIO(body)

    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen)
    release = updater.latest_release()
    assert release is not None
    assert release["version"] == "3.0.1"
    assert release["notes"] == "cdn notes"
    assert release["assets"]["a-setup.exe"] == "https://cdn/a-setup.exe"
    assert updater.RELEASE_API in calls[0]
    assert calls[0] == updater.RELEASE_API


def test_latest_release_all_sources_fail(monkeypatch):
    import urllib.error

    def fake_fail(*args, **kwargs):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_fail)
    assert updater.latest_release() is None


def test_download_streams_with_progress_and_cancel(tmp_path):
    payload = b"x" * (1024 * 300)  # 300KB，跨多个 64KB 块（用默认 256KB 会只有 2 块）

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/file.bin"
        dest = tmp_path / "out.bin"
        received = []
        ok, info = updater.download(url, dest, progress_cb=lambda r, t: received.append((r, t)))
        assert ok is True
        assert dest.read_bytes() == payload
        assert received and received[-1][0] == len(payload)

        # 取消：第二个请求用 cancel_event 立即中止
        cancel = threading.Event()
        cancel.set()
        dest2 = tmp_path / "cancel.bin"
        ok2, info2 = updater.download(url, dest2, cancel_event=cancel)
        assert ok2 is False
        assert "取消" in info2
    finally:
        server.shutdown()
        server.server_close()


def test_download_failure_reports_error(tmp_path):
    ok, info = updater.download("http://127.0.0.1:1/none.bin", tmp_path / "x.bin")
    assert ok is False
    assert info  # 错误信息非空


def test_config_persists_auto_hide(tmp_path):
    """全屏自动隐藏开关必须能持久化（回归：_load 白名单漏键）。"""
    from pet.config import Config

    cfg = Config(tmp_path)
    assert cfg.get("auto_hide_fullscreen", True) is True  # 默认开启
    cfg.set("auto_hide_fullscreen", False)
    cfg.save()

    reloaded = Config(tmp_path)
    assert reloaded.get("auto_hide_fullscreen", True) is False


def test_config_persists_click_behavior_keys(tmp_path):
    """点击行为（显示余额/自言自语）与音效开关持久化。"""
    from pet.config import Config

    cfg = Config(tmp_path)
    assert cfg.get("click_show_balance", False) is False
    cfg.set("click_sound_enabled", False)
    cfg.set("click_show_balance", True)
    cfg.set("click_show_self_talk", True)
    cfg.save()

    reloaded = Config(tmp_path)
    assert reloaded.get("click_sound_enabled", True) is False
    assert reloaded.get("click_show_balance", False) is True
    assert reloaded.get("click_show_self_talk", False) is True
