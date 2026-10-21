# -*- coding: utf-8 -*-
"""DeepSeek Harness 一键启动器测试。"""
from __future__ import annotations

import os
import socket
from pathlib import Path
from types import SimpleNamespace

from pet.harness_launcher import _find_launch_command, is_running


import pytest


@pytest.fixture(autouse=True)
def _no_desktop_by_default(monkeypatch):
    """本文件的既有用例全部钉 web 路径：桌面端探测默认钉死「未安装/未运行」，
    双目标用例在自己的 monkeypatch 里另行覆盖（后 applied 者赢）。"""
    from pet import harness_launcher as hl

    monkeypatch.setattr(hl, "desktop_install_path", lambda: None)
    monkeypatch.setattr(hl, "desktop_process_running", lambda: False)


def test_harness_port_probe():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert is_running(port) is True
    finally:
        server.close()
    assert is_running(port) is False


def test_find_launch_command_resolves_web(monkeypatch):
    from pet import harness_launcher as hl

    monkeypatch.setattr(hl, "_which", lambda name: "dsh" if name == "dsh" else None)

    monkeypatch.setattr(hl, "_supports_no_open", lambda base: True)
    command = hl._find_launch_command()
    assert command == ["dsh", "web", "--host", "127.0.0.1", "--port", str(hl.DEFAULT_PORT), "--no-open"]

    monkeypatch.setattr(hl, "_supports_no_open", lambda base: False)
    command = hl._find_launch_command()
    assert command == ["dsh", "web", "--host", "127.0.0.1", "--port", str(hl.DEFAULT_PORT)]
    assert "--no-open" not in command


@pytest.mark.parametrize("package_installed", [True, False])
def test_find_launch_command_fallback_without_dsh(tmp_path, monkeypatch, package_installed):
    """无 dsh 命令时，按包是否存在选择 node + bin.js 或 npx。"""
    from pet import harness_launcher as hl

    root = tmp_path / "node_modules"
    bin_js = root / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    if package_installed:
        bin_js.parent.mkdir(parents=True)
        bin_js.write_text("// fixture", encoding="utf-8")
    node = str(tmp_path / "node.exe")
    # 可执行文件与包目录只取本用例输入，避免桌面增强 PATH 找到本机 DSH。
    monkeypatch.setattr(hl, "_which", {"node": node, "npx": "npx"}.get)
    monkeypatch.setattr(hl, "static_node_modules_roots", lambda: [])
    monkeypatch.setattr(hl, "global_node_modules_roots", lambda: [root])
    monkeypatch.setattr(hl, "_supports_no_open", lambda base: False)

    expected = [node, str(bin_js)] if package_installed else ["npx", "--yes", "@deepseek-ai/dsh"]
    assert hl._find_launch_command() == [
        *expected, "web", "--host", "127.0.0.1", "--port", str(hl.DEFAULT_PORT),
    ]


def test_supports_no_open_probes_help(monkeypatch, tmp_path):
    from pet import harness_launcher as hl

    hl._NO_OPEN_CACHE.clear()
    monkeypatch.setattr(hl, "_probe_cache_path", lambda: tmp_path / "nope.json")

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="--no-open  Do not open browser", stderr="")

    monkeypatch.setattr(hl.subprocess, "run", fake_run)
    assert hl._supports_no_open(["dsh"]) is True

    def fake_run_missing(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="Usage: dsh web [options]", stderr="")

    monkeypatch.setattr(hl.subprocess, "run", fake_run_missing)
    hl._NO_OPEN_CACHE.clear()
    assert hl._supports_no_open(["dsh"]) is False


def test_supports_no_open_probe_failure_defaults_false(monkeypatch, tmp_path):
    from pet import harness_launcher as hl

    hl._NO_OPEN_CACHE.clear()
    monkeypatch.setattr(hl, "_probe_cache_path", lambda: tmp_path / "nope.json")

    def fake_run_fail(*args, **kwargs):
        raise TimeoutError("probe timeout")

    monkeypatch.setattr(hl.subprocess, "run", fake_run_fail)
    assert hl._supports_no_open(["dsh"]) is False


def test_supports_no_open_disk_cache(monkeypatch, tmp_path):
    """落盘缓存：版本匹配时直接用缓存零探测；版本变了才重新慢探测。"""
    import json as _json
    from pet import harness_launcher as hl

    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(hl, "_probe_cache_path", lambda: cache_file)
    monkeypatch.setattr(hl, "_dsh_version", lambda cmd: "0.1.1-rc.2")
    hl._NO_OPEN_CACHE.clear()

    def _explode(*args, **kwargs):
        raise AssertionError("缓存命中时不应再跑慢探测")

    # 缓存命中：probe 爆炸也不应被调用
    cache_file.write_text(_json.dumps(
        {"cmd": ["dsh"], "version": "0.1.1-rc.2", "no_open": True}), encoding="utf-8")
    monkeypatch.setattr(hl, "_probe_no_open", _explode)
    assert hl._supports_no_open(["dsh"]) is True

    # 版本变了：缓存失效，回落到慢探测
    monkeypatch.setattr(hl, "_dsh_version", lambda cmd: "0.1.2")
    hl._NO_OPEN_CACHE.clear()
    monkeypatch.setattr(hl, "_probe_no_open", lambda cmd: (False, True))
    assert hl._supports_no_open(["dsh"]) is False

    # 探测失败（probe_ok=False）不写缓存，避免把超时误判固化
    hl._NO_OPEN_CACHE.clear()
    monkeypatch.setattr(hl, "_probe_no_open", lambda cmd: (False, False))
    assert hl._supports_no_open(["dsh"]) is False
    # 缓存应保持第二段写入的 0.1.2 版本内容，未被失败探测覆盖
    assert _json.loads(cache_file.read_text(encoding="utf-8"))["version"] == "0.1.2"

    # 探测失败 + 版本不匹配的旧缓存 → 兜底沿用旧答案（开机超时不再误判）
    cache_file.write_text(_json.dumps(
        {"cmd": ["dsh"], "version": "9.9.9", "no_open": True}), encoding="utf-8")
    hl._NO_OPEN_CACHE.clear()
    assert hl._supports_no_open(["dsh"]) is True


def test_launch_harness_reuses_existing_instance_on_alt_port(monkeypatch):
    """已有 dsh web 跑在官方默认 3080 时，直接复用打开，不再新起 38080。

    ``target="web"`` 显式给出：默认的 auto 已改为 service（只起服务、不开浏览器），
    本用例钉的是 web 目标「复用并打开」这条契约。
    """
    from pet import harness_launcher as hl

    opened = []
    monkeypatch.setattr(hl.webbrowser, "open", lambda url: opened.append(url))
    # 38080 无监听，3080 有
    monkeypatch.setattr(hl, "is_running", lambda port=None: int(port or 38080) == 3080)

    def _no_spawn(command):  # 不应走到启动分支
        raise AssertionError("已有实例运行时不应再 spawn")

    monkeypatch.setattr(hl, "_spawn", _no_spawn)
    status, url = hl.launch_harness(target="web")
    assert status == "already"
    assert url == "http://127.0.0.1:3080"
    assert opened == ["http://127.0.0.1:3080"]


def test_launch_harness_prefers_configured_port(monkeypatch):
    """配置端口已有实例时优先复用它，不再探测 3080。"""
    from pet import harness_launcher as hl

    opened = []
    probed = []
    monkeypatch.setattr(hl.webbrowser, "open", lambda url: opened.append(url))

    def _probe(port=None):
        probed.append(int(port or 38080))
        return True  # 第一个候选（配置端口）即有监听

    monkeypatch.setattr(hl, "is_running", _probe)
    status, url = hl.launch_harness(port=38080, target="web")
    assert status == "already"
    assert probed == [38080]
    assert opened == ["http://127.0.0.1:38080"]


def test_launch_harness_no_browser_when_autostart(monkeypatch):
    """open_browser=False（随桌宠自启动）：只起服务，任何分支都不开浏览器。"""
    from pet import harness_launcher as hl

    opened = []
    monkeypatch.setattr(hl.webbrowser, "open", lambda url: opened.append(url))

    # 分支1：已有实例 → 直接返回，不开浏览器
    monkeypatch.setattr(hl, "is_running", lambda port=None: True)
    status, url = hl.launch_harness(open_browser=False)
    assert status == "already"
    assert opened == []

    # 分支2：新起（带 --no-open）→ 不起等待线程、不开浏览器
    monkeypatch.setattr(hl, "is_running", lambda port=None: False)
    monkeypatch.setattr(hl, "_spawn", lambda command: None)
    monkeypatch.setattr(
        hl, "_find_launch_command",
        lambda port=None: ["dsh", "web", "--host", "127.0.0.1", "--port", str(hl.DEFAULT_PORT), "--no-open"],
    )
    threads = []

    def fake_thread(target=None, daemon=None, **kwargs):
        threads.append(target)
        return SimpleNamespace(start=lambda: None)

    monkeypatch.setattr(hl.threading, "Thread", fake_thread)
    status, url = hl.launch_harness(open_browser=False)
    assert status == "started"
    assert opened == []
    assert threads == [], "open_browser=False 不应启动等待开浏览器的线程"


def test_harness_autostart_hook_gates(tmp_path, monkeypatch):
    """AppShell._maybe_autostart_harness：三个门（system_tools 模块激活 /
    harness_autostart / agent_link.dsh）任一不成立都不触发；已有实例不重复拉起。

    真 Config + 真门方法（``AppShell.__new__`` 绕开重型 __init__）：门本身就是
    被测对象，用替身替掉它等于什么都没测（本批 K1 就是在这里加第三条门）。
    2026-10-21：第一门从 enable_chat 的历史误绑改为 system_tools 模块激活。
    """
    from pet import app as app_mod
    from pet import feature_activation as fa
    from pet import harness_launcher as hl
    from pet.config import Config
    from pet.features import MIGRATION_BASELINE

    spawned = []
    monkeypatch.setattr(
        app_mod.threading, "Thread",
        lambda target=None, daemon=None, name=None: SimpleNamespace(start=lambda: spawned.append(target)),
    )

    def _make(flag, dsh_link):
        cfg = Config(base=tmp_path)
        cfg.set("harness_autostart", flag)
        agent_cfg = dict(cfg.get("agent_link") or {})
        agent_cfg["dsh"] = dsh_link
        cfg.set("agent_link", agent_cfg)
        shell = app_mod.AppShell.__new__(app_mod.AppShell)  # 绕开重型 __init__
        shell.config = cfg
        return shell

    fa.freeze_snapshot_for_tests(MIGRATION_BASELINE)
    try:
        app_mod.AppShell._maybe_autostart_harness(_make(False, dsh_link=True))
        app_mod.AppShell._maybe_autostart_harness(_make(True, dsh_link=False))
        # system_tools 模块未激活：flag 与联动都开也不拉
        fa.freeze_snapshot_for_tests([fid for fid in MIGRATION_BASELINE if fid != "system_tools"])
        app_mod.AppShell._maybe_autostart_harness(_make(True, dsh_link=True))
        assert spawned == []

        fa.freeze_snapshot_for_tests(MIGRATION_BASELINE)
        app_mod.AppShell._maybe_autostart_harness(_make(True, dsh_link=True))
        assert len(spawned) == 1
    finally:
        fa.reset_snapshot_for_tests()

    # 「已有实例」的判定已下移到 launch_harness（它同时看 web 端口与桌面端单实例，
    # app 层按 web 端口提前 return 会把桌面端目标一起挡掉）。判据升级为两条：
    # ①必须把判定交给 launch_harness；②仍然一次都不 spawn。
    launched = []
    spawn_cmds = []
    real_launch = hl.launch_harness
    monkeypatch.setattr(hl, "is_running", lambda port=None: True)
    monkeypatch.setattr(hl, "_spawn", lambda command: spawn_cmds.append(command))
    monkeypatch.setattr(hl, "launch_harness",
                        lambda **kw: launched.append(kw) or real_launch(**kw))
    spawned[0]()
    assert len(launched) == 1, "已有实例的判定必须交给 launch_harness"
    assert spawn_cmds == [], "已有 dsh 实例在跑时不得重复拉起"


def test_launch_harness_browser_ownership(monkeypatch):
    from pet import harness_launcher as hl

    opened = []
    threads = []

    monkeypatch.setattr(hl.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(hl, "is_running", lambda port=None: False)
    monkeypatch.setattr(hl, "_spawn", lambda command: None)

    def fake_thread(target=None, daemon=None, **kwargs):
        started = []

        def start():
            started.append((target, daemon))

        t = SimpleNamespace(start=start)
        threads.append((t, started))
        return t

    monkeypatch.setattr(hl.threading, "Thread", fake_thread)

    # 不带 --no-open：dsh 自己开浏览器，桌宠不重复打开
    monkeypatch.setattr(
        hl, "_find_launch_command",
        lambda port=None: ["dsh", "web", "--host", "127.0.0.1", "--port", str(hl.DEFAULT_PORT)],
    )
    status, url = hl.launch_harness(target="web")
    assert status == "started"
    assert opened == []
    assert threads == []

    # 带 --no-open：桌宠等待就绪后打开浏览器
    monkeypatch.setattr(
        hl, "_find_launch_command",
        lambda port=None: ["dsh", "web", "--host", "127.0.0.1", "--port", str(hl.DEFAULT_PORT), "--no-open"],
    )
    status, url = hl.launch_harness(target="web")
    assert status == "started"
    assert threads, "带 --no-open 时应启动等待线程"
    assert opened == []


def test_spawn_injects_augmented_path(monkeypatch):
    """子进程必须继承增强 PATH：macOS Finder 启动的 .app 原 PATH 极简，
    dsh/npx 的 shebang（/usr/bin/env node）依赖子进程环境找 node。"""
    import subprocess

    from pet import harness_launcher as hl

    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(hl.subprocess, "Popen", fake_popen)
    hl._spawn(["dsh", "web"])
    env = captured["kwargs"]["env"]
    assert env["PATH"] == hl._augmented_path()
    # 增强 PATH 是完整 PATH 的超集（前缀 + 原 PATH）
    original = hl._augmented_path()
    assert env["PATH"] == original


def test_node_runtime_augments_finder_path_with_homebrew(monkeypatch):
    """Issue #67：macOS Finder 的极简 PATH 仍能覆盖 Homebrew bin。"""
    if os.name == "nt":
        return
    from pet import node_runtime

    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    monkeypatch.setattr(
        node_runtime.Path,
        "is_dir",
        lambda path: str(path) == "/opt/homebrew/bin",
    )
    captured = {}

    def fake_which(name, path=None):
        captured["name"] = name
        captured["path"] = path
        return "/opt/homebrew/bin/node"

    monkeypatch.setattr(node_runtime.shutil, "which", fake_which)

    assert node_runtime.which("node") == "/opt/homebrew/bin/node"
    assert captured == {
        "name": "node",
        "path": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    }


def test_npm_root_probe_skipped_when_npm_missing(monkeypatch):
    """PATH 上没有 npm 时不应执行 npm root -g（避免菜单点击卡 15 秒）。"""
    from pet import harness_launcher as hl

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        raise FileNotFoundError("npm not found")

    monkeypatch.setattr(hl, "_which", lambda name: None)
    monkeypatch.setattr(hl.subprocess, "run", fake_run)
    roots = hl._npm_global_roots()
    assert calls == [], "npm 不存在时不应探测 npm root -g"
    assert any(r.name == "node_modules" for r in roots)  # 静态候选仍保留


def test_npm_root_probe_runs_when_npm_present(monkeypatch):
    from pet import harness_launcher as hl

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        result = SimpleNamespace(returncode=0, stdout="/fake/global/node_modules\n")
        return result

    monkeypatch.setattr(hl, "_which", lambda name: "/fake/npm" if name == "npm" else None)
    monkeypatch.setattr(hl.subprocess, "run", fake_run)
    roots = hl._npm_global_roots()
    assert calls, "npm 存在时应执行 npm root -g"
    assert Path("/fake/global/node_modules") in roots


# ------------------------------------------------------------------ 双目标（web / 桌面端）
def test_target_web_keeps_existing_web_command(monkeypatch):
    """① target=web：构造的启动命令与现状完全一致（web 路径行为零变化）。

    必须 ``open_browser=False``：命令带 ``--no-open`` 且要开浏览器时，产品会派生
    一个最长活 90 秒的「等就绪再开浏览器」线程，会逃出用例边界（本用例只验命令）。
    """
    from pet import harness_launcher as hl

    monkeypatch.setattr(hl, "_which", lambda name: "dsh" if name == "dsh" else None)
    monkeypatch.setattr(hl, "_supports_no_open", lambda base: True)
    monkeypatch.setattr(hl, "is_running", lambda port=None: False)
    spawned = []
    monkeypatch.setattr(hl, "_spawn", lambda command: spawned.append(command))

    status, url = hl.launch_harness(target="web", open_browser=False)
    assert status == "started"
    assert spawned == [["dsh", "web", "--host", "127.0.0.1", "--port", str(hl.DEFAULT_PORT), "--no-open"]]
    assert url == "http://127.0.0.1:38080"


def test_target_desktop_launches_exe_when_installed(monkeypatch, tmp_path):
    """② target=desktop 且检测到安装：拉起桌面端 exe（不开浏览器、不等就绪）。"""
    from pet import harness_launcher as hl

    monkeypatch.setattr(hl, "DESKTOP_SUPPORTED", True)  # 非 Windows CI 上也测 desktop 路径逻辑
    exe = tmp_path / "DeepSeek Harness.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(hl, "desktop_install_path", lambda: exe)
    monkeypatch.setattr(hl, "desktop_process_running", lambda: False)
    monkeypatch.setattr(hl, "is_running", lambda port=None: False)
    spawned = []
    monkeypatch.setattr(hl, "_spawn_desktop", lambda path: spawned.append(path))
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

    status, info = hl.launch_harness(target="desktop")
    assert status == "started"
    assert spawned == [exe]
    assert opened == [], "桌面端路径不得打开浏览器"


def test_auto_resolves_to_service_without_probing_desktop(monkeypatch):
    """③ auto → service，且**不再做任何桌面端探测**（安装位/注册表/进程都不看）。

    语义收紧（2026-10，产品口径）：自启的本意是只拉起 dsh 服务，web/desktop
    界面必须是显式选择。旧语义（auto 装上桌面端就拐 desktop）让每次桌宠启动都
    把整个桌面端 GUI 拉起来。把两个探测入口换成会抛的替身：auto 只要碰它们一次
    就是回归。合法/非法/空值一并钉住——非法值照旧回落 auto（= service）。
    """
    from pet import harness_launcher as hl

    def _boom(*args, **kwargs):
        raise AssertionError("auto 不得再探测桌面端（安装位 / 进程枚举）")

    monkeypatch.setattr(hl, "desktop_install_path", _boom)
    monkeypatch.setattr(hl, "desktop_process_running", _boom)

    assert hl.resolve_launch_target("auto") == "service"
    assert hl.resolve_launch_target("") == "service"
    assert hl.resolve_launch_target("  AUTO  ") == "service"
    assert hl.resolve_launch_target("bogus") == "service", "非法值回退 auto（= service）"
    assert hl.resolve_launch_target("service") == "service"
    assert hl.resolve_launch_target("web") == "web"
    assert hl.resolve_launch_target("desktop") == "desktop"


def test_auto_launch_is_headless_service(monkeypatch):
    """③ auto 走 web 启动路径，但强制 headless：不开浏览器、不派生等待线程。

    ``open_browser`` 取默认 True——service 的定义就是「只起服务」，调用方传什么
    都改变不了这一点（自启路径靠这条保证不弹浏览器）。
    """
    from pet import harness_launcher as hl

    opened: list[str] = []
    threads: list = []
    spawned: list[list[str]] = []
    monkeypatch.setattr(hl.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(hl, "is_running", lambda port=None: False)
    monkeypatch.setattr(hl, "_spawn", lambda command: spawned.append(list(command)))
    monkeypatch.setattr(
        hl.threading, "Thread",
        lambda target=None, daemon=None, **kwargs: threads.append(target),
    )
    monkeypatch.setattr(
        hl, "_find_launch_command",
        lambda port=None: ["dsh", "web", "--host", "127.0.0.1", "--port", str(hl.DEFAULT_PORT), "--no-open"],
    )

    status, url = hl.launch_harness(target="auto")
    assert status == "started" and url == "http://127.0.0.1:38080"
    assert spawned and spawned[0][1] == "web", "auto 必须照常把 dsh web 服务拉起来"
    assert opened == [], "auto（service）不得打开浏览器"
    assert threads == [], "auto（service）不得派生「等就绪再开页面」线程"


def test_service_target_stays_headless_even_with_open_browser_true(monkeypatch):
    """显式 service 目标：即使调用方传 ``open_browser=True`` 也不开浏览器。

    与 web 目标的判别只在这一格：web + open_browser=True + --no-open 会派生
    等待线程（见 ``test_launch_harness_browser_ownership``），service 不会。
    """
    from pet import harness_launcher as hl

    opened: list[str] = []
    threads: list = []
    spawned: list[list[str]] = []
    monkeypatch.setattr(hl.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(hl, "is_running", lambda port=None: False)
    monkeypatch.setattr(hl, "_spawn", lambda command: spawned.append(list(command)))
    monkeypatch.setattr(
        hl.threading, "Thread",
        lambda target=None, daemon=None, **kwargs: threads.append(target),
    )
    monkeypatch.setattr(
        hl, "_find_launch_command",
        lambda port=None: ["dsh", "web", "--host", "127.0.0.1", "--port", str(hl.DEFAULT_PORT), "--no-open"],
    )

    status, url = hl.launch_harness(target="service", open_browser=True)
    assert status == "started"
    assert spawned, "service 必须照常起 dsh web 进程"
    assert opened == [] and threads == [], "service 目标必须强制 headless"


def test_service_target_does_not_open_browser_on_reused_instance(monkeypatch):
    """已有实例在线：service 报 already 但不开浏览器（web 目标这里会开）。"""
    from pet import harness_launcher as hl

    opened: list[str] = []
    monkeypatch.setattr(hl.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(hl, "is_running", lambda port=None: True)
    monkeypatch.setattr(
        hl, "_spawn", lambda command: (_ for _ in ()).throw(AssertionError("已有实例不得再拉起"))
    )

    status, url = hl.launch_harness(target="service", open_browser=True)
    assert status == "already" and url == "http://127.0.0.1:38080"
    assert opened == [], "service 复用已有实例时同样不得打开浏览器"


def test_explicit_desktop_not_installed_is_not_found(monkeypatch):
    """显式 desktop + 未安装 → not-found（静默回落 web 会让菜单选择失真）。"""
    from pet import harness_launcher as hl

    monkeypatch.setattr(hl, "DESKTOP_SUPPORTED", True)
    monkeypatch.setattr(hl, "desktop_install_path", lambda: None)
    monkeypatch.setattr(hl, "desktop_process_running", lambda: False)
    status, info = hl.launch_harness(target="desktop")
    assert status == "not-found" and info == "desktop"


def test_desktop_already_running_short_circuits(monkeypatch):
    """桌面端进程已在跑 → already，绝不重复拉起（Electron 单实例语义）。"""
    from pet import harness_launcher as hl

    monkeypatch.setattr(hl, "DESKTOP_SUPPORTED", True)
    monkeypatch.setattr(hl, "desktop_process_running", lambda: True)
    monkeypatch.setattr(hl, "_spawn_desktop", lambda path: (_ for _ in ()).throw(AssertionError("不得重复拉起")))
    status, info = hl.launch_harness(target="desktop")
    assert (status, info) == ("already", "desktop")


def test_self_launched_desktop_never_killed_on_exit(monkeypatch):
    """④ desktop 由 pet 拉起后，退出收口**完全不碰**终止链路（只登记不终止）。

    消融判别：desktop 分支连身份核验都不该做——命中
    ``process_command_line`` / ``is_running_pid`` / ``_terminate_process_tree``
    任一个，就说明它掉进了 web 的收口路径（那才是会误杀用户主力应用的那条路）。
    """
    from pet import harness_launcher as hl

    touched: list[str] = []

    def _touch(name: str, result):
        def _stub(*args, **kwargs):
            touched.append(name)
            return result
        return _stub

    # 构造一个「活着的自拉起 desktop」登记：真 Popen 句柄由假子进程替身担任
    class _FakeProc:
        pid = 424242
        def poll(self):
            return None

    monkeypatch.setattr(hl, "_terminate_process_tree", _touch("_terminate_process_tree", True))
    monkeypatch.setattr(hl, "is_running_pid", _touch("is_running_pid", True))
    monkeypatch.setattr(
        hl, "process_command_line", _touch("process_command_line", "DeepSeek Harness.exe"))

    with hl._OWNERSHIP_LOCK:
        hl._SELF_LAUNCHED_PIDS.clear()
        hl._SELF_LAUNCHED_KIND.clear()
        hl._LAUNCHED_CHILDREN.clear()
        hl._LAUNCHED_CHILDREN.append(_FakeProc())
        hl._record_self_launched(424242, kind="desktop")
    try:
        terminated = hl.stop_self_launched_harness()
        assert terminated == [], "desktop 自拉起登记不得被退出收口终止"
        assert touched == [], f"desktop 分支不得触及任何核验/终止边界：{touched}"
        assert 424242 in hl._SELF_LAUNCHED_PIDS, "登记保留（可考），只是不杀"
    finally:
        with hl._OWNERSHIP_LOCK:
            hl._SELF_LAUNCHED_PIDS.clear()
            hl._SELF_LAUNCHED_KIND.clear()
            hl._LAUNCHED_CHILDREN.clear()


def test_dead_desktop_registration_is_forgotten(monkeypatch):
    """desktop 登记的子进程已退出（用户关了桌面端）→ 销登记，不累积死 pid。

    「不杀」不等于「不管」：收口顺序必须是「先按句柄判死销记，再对活 desktop
    跳过终止」；反了的话每次退出都留一条永不消费的死 pid。
    """
    from pet import harness_launcher as hl

    touched: list[str] = []
    monkeypatch.setattr(
        hl, "_terminate_process_tree",
        lambda pid, proc=None: touched.append("_terminate_process_tree") or True)

    class _DeadProc:
        pid = 424244
        def poll(self):
            return 0  # 桌面端已被用户关掉

    with hl._OWNERSHIP_LOCK:
        hl._SELF_LAUNCHED_PIDS.clear()
        hl._SELF_LAUNCHED_KIND.clear()
        hl._LAUNCHED_CHILDREN.clear()
        hl._LAUNCHED_CHILDREN.append(_DeadProc())
        hl._record_self_launched(424244, kind="desktop")
    try:
        terminated = hl.stop_self_launched_harness()
        assert hl._SELF_LAUNCHED_PIDS == [], "已退出的 desktop 登记必须销掉（不累积死 pid）"
        assert terminated == [] and touched == [], "销登记不是终止，绝不发终止命令"
    finally:
        with hl._OWNERSHIP_LOCK:
            hl._SELF_LAUNCHED_PIDS.clear()
            hl._SELF_LAUNCHED_KIND.clear()
            hl._LAUNCHED_CHILDREN.clear()


def test_spawn_desktop_registers_kind_desktop(monkeypatch, tmp_path):
    """真 ``_spawn_desktop`` 的登记语义：pid 入表且 kind=desktop（「不杀」的依据）。"""
    from pet import harness_launcher as hl

    exe = tmp_path / "DeepSeek Harness.exe"
    exe.write_bytes(b"MZ")

    class _RealFakeProc(hl.subprocess.Popen):
        def __init__(self):
            self.pid = 424245
        def poll(self):
            return None

    monkeypatch.setattr(hl.subprocess, "Popen", lambda command, **kw: _RealFakeProc())
    with hl._OWNERSHIP_LOCK:
        hl._SELF_LAUNCHED_PIDS.clear()
        hl._SELF_LAUNCHED_KIND.clear()
        hl._LAUNCHED_CHILDREN.clear()
    try:
        hl._spawn_desktop(exe)
        assert hl._SELF_LAUNCHED_PIDS == [424245]
        assert hl._SELF_LAUNCHED_KIND.get(424245) == "desktop", "kind 错记会让收口误杀桌面端"
    finally:
        with hl._OWNERSHIP_LOCK:
            hl._SELF_LAUNCHED_PIDS.clear()
            hl._SELF_LAUNCHED_KIND.clear()
            hl._LAUNCHED_CHILDREN.clear()


# ------------------------------------------------------------------ 桌面端安装探测
def test_registry_display_name_accepts_bounded_version_suffix():
    """卸载项 DisplayName 实测为「DeepSeek Harness 0.2.0-rc.2」：只认全名会让
    自定义安装位（默认位不存在时的唯一一路）永远探测不到。版本后缀受约束接受。"""
    from pet import harness_launcher as hl

    assert hl._display_name_matches("DeepSeek Harness") is True
    assert hl._display_name_matches("  DeepSeek Harness 0.2.0-rc.2  ") is True
    assert hl._display_name_matches("DeepSeek Harness 1.0") is True
    # 放宽成「前缀相同」会误认别的程序：只接受后面跟版本号（数字开头）的形态
    assert hl._display_name_matches("DeepSeek Harness Helper") is False
    assert hl._display_name_matches("DeepSeek HarnessX 1.0") is False
    # 只有空白差异仍是同一个产品（两侧 strip 后等价）
    assert hl._display_name_matches("DeepSeek Harness ") is True
    assert hl._display_name_matches("") is False
    assert hl._display_name_matches(None) is False


def test_registry_display_icon_strips_icon_index():
    """DisplayIcon 形如 ``"C:\\...\\DeepSeek Harness.exe",0``：不剥索引会拼出带
    ``,0`` 的假路径，``is_file()`` 永远为假。"""
    from pet import harness_launcher as hl

    exe = r"C:\Program Files\DeepSeek Harness\DeepSeek Harness.exe"
    assert hl._strip_icon_index(f'"{exe}",0') == exe
    assert hl._strip_icon_index(f"{exe},0") == exe
    assert hl._strip_icon_index(f"{exe},-1") == exe
    assert hl._strip_icon_index(exe) == exe
    assert hl._strip_icon_index(f"{exe},") == f"{exe},"
    assert hl._strip_icon_index(r"C:\x\a,0.exe") == r"C:\x\a,0.exe"
    assert hl._strip_icon_index("") == ""


def test_registry_probe_finds_versioned_name_and_icon_path(tmp_path, monkeypatch):
    """端到端（winreg 替身）：DisplayName 带版本 + 只有 DisplayIcon（带 ``,0``）→ 命中 exe。"""
    import sys
    import types

    from pet import harness_launcher as hl

    exe = tmp_path / "DeepSeek Harness.exe"
    exe.write_bytes(b"MZ")

    class _Key:
        def __init__(self, subs=(), values=None):
            self._subs = list(subs)
            self._values = dict(values or {})
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    sub_key = _Key(values={
        "DisplayName": "DeepSeek Harness 0.2.0-rc.2",
        "InstallLocation": "",
        "DisplayIcon": f'"{exe}",0',
    })
    root_key = _Key(subs=["DeepSeekHarness"])

    fake = types.ModuleType("winreg")
    fake.HKEY_CURRENT_USER = 1
    fake.HKEY_LOCAL_MACHINE = 2
    fake.OpenKey = lambda root, path: root_key if isinstance(root, int) else sub_key
    fake.QueryInfoKey = lambda key: (len(key._subs), 0, 0)
    fake.EnumKey = lambda key, i: key._subs[i]
    def _query_value(key, name):
        if name not in key._values:
            raise OSError(f"missing {name}")
        return key._values[name], 6
    fake.QueryValueEx = _query_value

    monkeypatch.setattr(hl, "DESKTOP_SUPPORTED", True)
    monkeypatch.setitem(sys.modules, "winreg", fake)

    assert hl._desktop_exe_from_registry() == exe


def test_desktop_unsupported_platform_keeps_web_default(monkeypatch):
    """非 Windows（本轮不支持桌面端）：auto 走 service，显式 desktop 明确报不支持。

    占位实现曾把 macOS 的 ``/Applications/*.app`` 目录当成桌面端可执行文件：
    auto 会拐到 desktop、然后 Popen 一个目录。能力位钉死后不许回归；auto 现已
    统一落到 service（与平台无关，非 Windows 只是「更不可能拐到 desktop」）。
    """
    from pet import harness_launcher as hl

    monkeypatch.setattr(hl, "DESKTOP_SUPPORTED", False)
    assert hl.desktop_install_path() is None
    assert hl.desktop_processes() == []
    assert hl.desktop_process_running() is False
    assert hl.resolve_launch_target("auto") == "service", "auto 恒为 service（不探测桌面端）"
    assert hl.resolve_launch_target("desktop") == "desktop"
    assert hl.launch_harness(target="desktop") == ("unsupported", "desktop")


def test_desktop_aborts_when_cancel_check_hits(monkeypatch, tmp_path):
    """desktop 直接路径的 abort 闸：cancel_check 命中 → aborted，绝不 spawn。

    回归（2026-10 双目标尾项）：app 的 cancel_check 原来只认退出/会话结束标记，
    「探测期间用户把开关关掉」不算数——desktop 路径上这一格的代价是真弹出一个
    用户刚说不想要的 GUI 窗口（web 路径只是多一个后台 node）。
    """
    from pet import harness_launcher as hl

    exe = tmp_path / "DeepSeek Harness.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(hl, "DESKTOP_SUPPORTED", True)
    monkeypatch.setattr(hl, "desktop_install_path", lambda: exe)
    monkeypatch.setattr(hl, "desktop_process_running", lambda: False)
    spawned: list = []
    monkeypatch.setattr(hl, "_spawn_desktop", lambda path: spawned.append(path))

    assert hl.launch_harness(target="desktop", cancel_check=lambda: True) == ("aborted", "desktop")
    assert spawned == [], "abort 路径绝不 spawn 桌面端"


def test_self_launched_web_still_collected(monkeypatch):
    """对照：web 自拉起登记照旧被收口终止（原行为不破）。"""
    from pet import harness_launcher as hl

    killed = []
    def _fake_terminate(pid, proc=None):
        killed.append(pid)
        if proc is not None:
            proc.dead = True
        return True
    monkeypatch.setattr(hl, "_terminate_process_tree", _fake_terminate)
    monkeypatch.setattr(hl, "is_running_pid", lambda pid: True)
    monkeypatch.setattr(hl, "process_command_line", lambda pid: "node dsh web --port 38080")
    monkeypatch.setattr(hl, "_pid_image_path", lambda pid: "node.exe")

    class _FakeProc:
        pid = 424243
        dead = False
        def poll(self):
            # 存活直到被终止（收口链路：存活复核 → 终止 → poll 确认死亡）
            return 0 if self.dead else None

    with hl._OWNERSHIP_LOCK:
        hl._SELF_LAUNCHED_PIDS.clear()
        hl._SELF_LAUNCHED_KIND.clear()
        hl._LAUNCHED_CHILDREN.clear()
        hl._LAUNCHED_CHILDREN.append(_FakeProc())
        hl._record_self_launched(424243, kind="web")
    try:
        terminated = hl.stop_self_launched_harness()
        assert 424243 in terminated, "web 自拉起登记必须照常收口"
    finally:
        with hl._OWNERSHIP_LOCK:
            hl._SELF_LAUNCHED_PIDS.clear()
            hl._SELF_LAUNCHED_KIND.clear()
            hl._LAUNCHED_CHILDREN.clear()
