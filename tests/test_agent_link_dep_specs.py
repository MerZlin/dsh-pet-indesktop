# -*- coding: utf-8 -*-
"""依赖规格体检 + 桥接 link 自检。

背景（issue：桥接装不上）：profile 的 `package.json` 里可能有**指向本地路径**的依赖
（`link:` / `file:`），路径里往往嵌着会变的东西——打包构建目录名、文件名里的版本号、
本机绝对路径。一旦目录改名或版本升级，spec 就指向不存在的路径，pnpm 解析失败，而报错
只透传 pnpm 的 stderr 尾巴，用户看不出是哪条依赖、该改成什么。

本文件覆盖两层：
1. 依赖规格体检（只诊断不修改）：指名报错 + 给出"疑似应改为"的候选路径；
2. 桥接 link 自检：我们自己写进 profile 的 `link:` 目标不是当前内置插件目录时，
   启动后自动刷新（只刷新已装插件的 profile，绝不在启动时替用户安装）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pet import agent_link
from pet.agent_link import DshMonitor


def _profile(tmp_path: Path, name: str = "web", deps: dict | None = None) -> Path:
    profile = tmp_path / "profiles" / name
    profile.mkdir(parents=True)
    (profile / "package.json").write_text(
        json.dumps({"dependencies": deps or {}}, ensure_ascii=False), encoding="utf-8"
    )
    return profile


def _manifest(profile: Path) -> dict:
    data = agent_link._read_manifest(profile)
    assert data is not None
    return data


# ---------------------------------------------------------------- 规格解析

class TestPathSpecTarget:
    def test_accepts_local_path_forms(self, tmp_path):
        link_target = tmp_path / "plugins" / "bridge"
        tar = tmp_path / "artifacts" / "pkg-1.0.0.tgz"
        # 两边都 resolve：macOS 的 /var → /private/var 等符号链接会让裸路径不等
        assert agent_link._path_spec_target(f"link:{link_target}", tmp_path) == link_target.resolve()
        assert agent_link._path_spec_target(f"file:{tar}", tmp_path) == tar.resolve()
        assert agent_link._path_spec_target("./sibling", tmp_path) == (tmp_path / "sibling").resolve()

    def test_decodes_percent_escapes(self, tmp_path):
        spec = "file:C:/Program%20Files/dsh/pkg-1.0.0.tgz"
        got = agent_link._path_spec_target(spec, tmp_path)
        assert got is not None and "Program Files" in str(got)

    def test_ignores_registry_and_remote_specs(self, tmp_path):
        for spec in (
            "^1.2.3", "0.1.14", "workspace:*", "npm:pkg@1.0.0",
            "github:owner/repo#path:/sub", "https://example.com/pkg-1.0.0.tgz",
        ):
            assert agent_link._path_spec_target(spec, tmp_path) is None, spec


# ---------------------------------------------------------------- 缺失诊断

class TestMissingDependencySpecs:
    def test_ok_when_target_exists(self, tmp_path):
        target = tmp_path / "plugins" / "bridge"
        target.mkdir(parents=True)
        profile = _profile(tmp_path, deps={"@dsh-pet/bridge": f"link:{target}"})
        assert agent_link._missing_dependency_specs(profile, _manifest(profile)) == []

    def test_names_dependency_and_missing_path(self, tmp_path):
        missing = tmp_path / "gone" / "ghost-ext"
        profile = _profile(tmp_path, deps={"ghost-ext": f"link:{missing}"})
        findings = agent_link._missing_dependency_specs(profile, _manifest(profile))
        assert len(findings) == 1
        assert "ghost-ext" in findings[0]
        assert "gone" in findings[0]

    def test_suggests_renamed_build_directory(self, tmp_path):
        """打包构建目录改名（dist-onedir/<name>/...）→ 给出新目录下同一相对路径。"""
        new_target = (
            tmp_path / "dist-onedir" / "new-build" / "_internal" / "integrations" / "dsh-pet-bridge"
        )
        new_target.mkdir(parents=True)
        (tmp_path / "dist-onedir" / "old-build" / "_internal" / "integrations").mkdir(parents=True)
        missing = (
            tmp_path / "dist-onedir" / "old-build" / "_internal" / "integrations" / "dsh-pet-bridge"
        )
        profile = _profile(tmp_path, deps={"@dsh-pet/bridge": f"link:{missing}"})

        finding = agent_link._missing_dependency_specs(profile, _manifest(profile))[0]

        assert str(new_target) in finding, finding

    def test_suggests_version_bumped_artifact(self, tmp_path):
        """文件名带版本的 spec（0.12.80 → 0.13.6）→ 指向磁盘上更新的同类文件。"""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        newer = artifacts / "deepseek-ai-dsh-ext-0.13.6.tgz"
        newer.write_text("x", encoding="utf-8")
        profile = _profile(
            tmp_path,
            deps={"@deepseek-ai/dsh-ext": f"file:{artifacts / 'deepseek-ai-dsh-ext-0.12.80.tgz'}"},
        )

        finding = agent_link._missing_dependency_specs(profile, _manifest(profile))[0]

        assert newer.name in finding, finding

    def test_probes_survive_permission_errors(self, tmp_path, monkeypatch):
        """回归（CI ubuntu 实测）：祖先目录无搜索权限时 `Path.is_dir()` 抛 EACCES，
        候选扫描必须退化成「没有建议」，绝不能把 PermissionError 抛进安装失败路径。

        CI 现场：tmp_path 落在 snap private /tmp 下，stat 直接 Permission denied。
        """
        real_is_dir = Path.is_dir

        def fake_is_dir(self):
            if "gone" in str(self):
                raise PermissionError(13, "Permission denied")
            return real_is_dir(self)

        monkeypatch.setattr(Path, "is_dir", fake_is_dir)
        missing = tmp_path / "gone" / "ghost-ext"
        profile = _profile(tmp_path, deps={"ghost-ext": f"link:{missing}"})

        findings = agent_link._missing_dependency_specs(profile, _manifest(profile))

        assert len(findings) == 1
        assert "ghost-ext" in findings[0]
        assert "疑似应改为" not in findings[0], "探测失败时应退化为无建议"

    def test_safe_probes_swallow_permission_errors(self, tmp_path, monkeypatch):
        """候选扫描用的两个探测助手都必须吞掉权限/竞态错误（CI ubuntu 实测 EACCES）。

        注：`Path.is_dir()` 内部也走 stat，所以"只让 mtime 排序失败"没法用打桩区分——
        这两条保证只能各自单元断言（集成面由上面的 permission 用例覆盖）。
        """
        target = tmp_path / "x"
        target.mkdir()

        def boom(*args, **kwargs):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(Path, "stat", boom)

        assert agent_link._safe_is_dir(target) is False
        assert agent_link._safe_mtime(target) == 0.0


# ---------------------------------------------------------------- 失败文案

class TestInstallFailureDiagnostics:
    def test_failure_message_names_the_broken_spec(self, tmp_path, monkeypatch):
        plugin = tmp_path / "dsh-pet-bridge"
        plugin.mkdir()
        profile = _profile(
            tmp_path,
            deps={
                "@dsh-pet/bridge": f"link:{plugin}",
                "ghost-ext": "file:W:/nonexistent/ghost-ext-0.12.80.tgz",
            },
        )
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: ["pnpm"])
        monkeypatch.setattr(
            agent_link, "_run_pnpm", lambda profile_dir, *args: (1, "ERR_PNPM_ ... 0.12.80")
        )

        ok, message, _ = DshMonitor.install_bridge()

        assert ok is False
        assert profile.name in message
        assert "ghost-ext" in message, message
        assert "0.12.80" in message, message


# ---------------------------------------------------------------- link 自检

class TestBridgeLinkStaleness:
    def _setup(self, tmp_path, monkeypatch, deps: dict):
        plugin = tmp_path / "current-build" / "integrations" / "dsh-pet-bridge"
        plugin.mkdir(parents=True)
        _profile(tmp_path, name="web", deps=deps)
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        return plugin

    def test_fresh_link_is_not_stale(self, tmp_path, monkeypatch):
        plugin = self._setup(tmp_path, monkeypatch, {})
        (tmp_path / "profiles" / "web" / "package.json").write_text(
            json.dumps({"dependencies": {agent_link.DSH_PLUGIN_NAME: f"link:{plugin}"}}),
            encoding="utf-8",
        )
        assert DshMonitor.bridge_link_stale() == []

    def test_link_to_other_build_is_stale(self, tmp_path, monkeypatch):
        other = tmp_path / "old-build"
        other.mkdir()
        self._setup(
            tmp_path, monkeypatch, {agent_link.DSH_PLUGIN_NAME: f"link:{other}"}
        )
        stale = DshMonitor.bridge_link_stale()
        assert [name for name, _spec in stale] == ["web"]

    def test_missing_link_target_is_stale(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path, monkeypatch, {agent_link.DSH_PLUGIN_NAME: "link:W:/gone/bridge"}
        )
        assert [name for name, _spec in DshMonitor.bridge_link_stale()] == ["web"]

    def test_profile_without_plugin_is_ignored(self, tmp_path, monkeypatch):
        """没装插件 ≠ 陈旧：启动自检绝不替用户安装。"""
        self._setup(tmp_path, monkeypatch, {"dsh-other": "^1.0.0"})
        assert DshMonitor.bridge_link_stale() == []


class TestRefreshStaleBridgeLinks:
    def test_refreshes_only_stale_profiles(self, tmp_path, monkeypatch):
        plugin = tmp_path / "current-build" / "integrations" / "dsh-pet-bridge"
        plugin.mkdir(parents=True)
        other = tmp_path / "old-build"
        other.mkdir()
        _profile(tmp_path, name="web", deps={agent_link.DSH_PLUGIN_NAME: f"link:{other}"})
        _profile(tmp_path, name="headless", deps={agent_link.DSH_PLUGIN_NAME: f"link:{plugin}"})
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        calls: list[tuple[str, tuple]] = []

        def fake_run(profile_dir, *args):
            calls.append((profile_dir.name, args))
            return 0, ""

        monkeypatch.setattr(agent_link, "_run_pnpm", fake_run)

        refreshed = DshMonitor.refresh_stale_bridge_links()

        assert refreshed == ["web"]
        assert [name for name, _args in calls] == ["web"]
        assert calls[0][1] == ("add", str(plugin))

    def test_refresh_repairs_broken_dep_specs_then_retries(self, tmp_path, monkeypatch):
        """陈旧 link 的 profile 里另有可修复的坏依赖时，refresh 也要先修后重试。

        启动自检与安装路径同源失败原因（manifest 指向不存在的旧路径）：裸
        _run_pnpm 一次失败就放弃，每次启动都重复同一轮静默失败，link 永远刷不新。
        """
        plugin = tmp_path / "current-build" / "integrations" / "dsh-pet-bridge"
        plugin.mkdir(parents=True)
        other = tmp_path / "old-build"
        other.mkdir()
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "ext-0.13.6.tgz").write_text("x", encoding="utf-8")
        profile = _profile(tmp_path, name="web", deps={
            agent_link.DSH_PLUGIN_NAME: f"link:{other}",
            "ext": f"file:{artifacts / 'ext-0.12.80.tgz'}",
        })
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: ["pnpm"])
        calls: list[tuple] = []

        def fake_run(profile_dir, *args):
            calls.append(args)
            if len(calls) == 1:
                return 1, "ERR_PNPM_ ... ext-0.12.80.tgz does not exist"
            # 修复已生效后的重试：模拟 pnpm add 成功并把 link 指到当前构建目录
            data = json.loads((profile_dir / "package.json").read_text(encoding="utf-8"))
            data.setdefault("dependencies", {})[agent_link.DSH_PLUGIN_NAME] = f"link:{plugin}"
            (profile_dir / "package.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            return 0, ""

        monkeypatch.setattr(agent_link, "_run_pnpm", fake_run)

        refreshed = DshMonitor.refresh_stale_bridge_links()

        assert refreshed == ["web"]
        assert len(calls) == 2, "修正坏依赖后必须重试一次"
        assert "0.13.6" in _manifest(profile)["dependencies"]["ext"]

    def test_never_installs_absent_plugin(self, tmp_path, monkeypatch):
        plugin = tmp_path / "current-build" / "integrations" / "dsh-pet-bridge"
        plugin.mkdir(parents=True)
        _profile(tmp_path, name="web", deps={"dsh-other": "^1.0.0"})
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        calls: list[str] = []
        monkeypatch.setattr(
            agent_link, "_run_pnpm", lambda profile_dir, *args: (calls.append(profile_dir.name), (0, ""))[1]
        )

        assert DshMonitor.refresh_stale_bridge_links() == []
        assert calls == []


class TestScheduling:
    def test_check_runs_at_most_once_per_monitor(self, tmp_path, monkeypatch):
        monitor = DshMonitor("dsh", tmp_path)
        seen: list[int] = []
        monkeypatch.setattr(
            DshMonitor,
            "refresh_stale_bridge_links",
            classmethod(lambda cls: (seen.append(1), [])[1]),
        )

        monitor.schedule_link_refresh_check(spawn=lambda fn: fn())
        monitor.schedule_link_refresh_check(spawn=lambda fn: fn())

        assert seen == [1], "同一次启动只自检一次"

    def test_apply_config_schedules_check_when_dsh_enabled(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QApplication
        from pet.config import Config
        from pet.agent_link import AgentLinkManager

        QApplication.instance() or QApplication([])
        scheduled: list[str] = []
        monkeypatch.setattr(
            DshMonitor,
            "schedule_link_refresh_check",
            lambda self, spawn=None: scheduled.append(self.agent_key),
        )
        cfg = Config(base=tmp_path)
        agent_cfg = dict(cfg.get("agent_link", {}))
        agent_cfg["dsh"] = True
        cfg.set("agent_link", agent_cfg)

        class Win:
            def show_bubble(self, *args, **kwargs):
                pass

            def isVisible(self):
                return True

        manager = AgentLinkManager(Win(), cfg)
        try:
            assert "dsh" in scheduled
        finally:
            manager.shutdown()

    def test_apply_config_skips_check_when_dsh_disabled(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QApplication
        from pet.config import Config
        from pet.agent_link import AgentLinkManager

        QApplication.instance() or QApplication([])
        scheduled: list[str] = []
        monkeypatch.setattr(
            DshMonitor,
            "schedule_link_refresh_check",
            lambda self, spawn=None: scheduled.append(self.agent_key),
        )
        cfg = Config(base=tmp_path)

        class Win:
            def show_bubble(self, *args, **kwargs):
                pass

            def isVisible(self):
                return True

        manager = AgentLinkManager(Win(), cfg)
        try:
            assert scheduled == []
        finally:
            manager.shutdown()


# ---------------------------------------------------------------- 坏路径实修

class TestSpecRepair:
    """web-rc8-test 现场：package.json 指着不存在的旧版路径（0.12.80，实际 0.13.6），
    必须**真修**——把能唯一确定的坏路径改写掉、备份原文件，然后让 pnpm 重生成 lockfile。"""

    def test_repairs_unique_missing_path_and_backs_up(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        newer = artifacts / "deepseek-ai-dsh-ext-0.13.6.tgz"
        newer.write_text("x", encoding="utf-8")
        profile = _profile(
            tmp_path,
            deps={
                "@deepseek-ai/dsh-ext": f"file:{artifacts / 'deepseek-ai-dsh-ext-0.12.80.tgz'}",
                "keep-me": "^1.0.0",
            },
        )

        changed = agent_link._repair_missing_dependency_specs(profile, _manifest(profile))

        assert len(changed) == 1
        assert "0.13.6" in changed[0]
        deps = _manifest(profile)["dependencies"]
        assert deps["@deepseek-ai/dsh-ext"] == f"file:{newer}"
        assert deps["keep-me"] == "^1.0.0", "无关依赖不许动"
        assert list(profile.glob("package.json.bak-*")), "改写前必须备份"

    def test_repairs_link_spec_keeping_prefix(self, tmp_path):
        new_target = tmp_path / "dist-onedir" / "new-build" / "dsh-pet-bridge"
        new_target.mkdir(parents=True)
        (tmp_path / "dist-onedir" / "old-build").mkdir(parents=True)
        missing = tmp_path / "dist-onedir" / "old-build" / "dsh-pet-bridge"
        profile = _profile(tmp_path, deps={"@dsh-pet/bridge": f"link:{missing}"})

        changed = agent_link._repair_missing_dependency_specs(profile, _manifest(profile))

        assert len(changed) == 1
        assert _manifest(profile)["dependencies"]["@dsh-pet/bridge"] == f"link:{new_target}"

    def test_leaves_unfixable_specs_alone(self, tmp_path):
        spec = "file:W:/nowhere/ghost-1.0.0.tgz"
        profile = _profile(tmp_path, deps={"ghost": spec})

        changed = agent_link._repair_missing_dependency_specs(profile, _manifest(profile))

        assert changed == []
        assert _manifest(profile)["dependencies"]["ghost"] == spec
        assert not list(profile.glob("package.json.bak-*")), "没改动就不该产生备份"

    def test_install_bridge_repairs_then_retries(self, tmp_path, monkeypatch):
        import json as _json

        plugin = tmp_path / "dsh-pet-bridge"
        plugin.mkdir()
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "ext-0.13.6.tgz").write_text("x", encoding="utf-8")
        profile = _profile(
            tmp_path, deps={"ext": f"file:{artifacts / 'ext-0.12.80.tgz'}"}
        )
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: ["pnpm"])
        calls: list[tuple] = []

        def fake_run(profile_dir, *args):
            calls.append(args)
            if len(calls) == 1:
                return 1, "ERR_PNPM_ ... ext-0.12.80.tgz does not exist"
            data = _json.loads((profile_dir / "package.json").read_text(encoding="utf-8"))
            data.setdefault("dependencies", {})[agent_link.DSH_PLUGIN_NAME] = f"link:{plugin}"
            (profile_dir / "package.json").write_text(
                _json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            return 0, ""

        monkeypatch.setattr(agent_link, "_run_pnpm", fake_run)

        ok, message, _ = DshMonitor.install_bridge()

        assert ok is True, message
        assert len(calls) == 2, "修正后必须重试一次"
        assert "0.13.6" in _manifest(profile)["dependencies"]["ext"]

    def test_install_bridge_does_not_retry_unfixable(self, tmp_path, monkeypatch):
        plugin = tmp_path / "dsh-pet-bridge"
        plugin.mkdir()
        _profile(tmp_path, deps={"ghost": "file:W:/nowhere/ghost-1.0.0.tgz"})
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: ["pnpm"])
        calls: list[tuple] = []

        def fake_run(profile_dir, *args):
            calls.append(args)
            return 1, "ERR_PNPM_ network"

        monkeypatch.setattr(agent_link, "_run_pnpm", fake_run)

        ok, message, _ = DshMonitor.install_bridge()

        assert ok is False
        assert len(calls) == 1, "没有可修正项就不该重试"
        assert "ghost" in message


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))


class TestInstallBridgeScaffoldsMissingProfile:
    """安装链缺口：全新 dsh（从未运行过）没有 profile，一键安装桥接必失败。

    dsh-app-boot 的 initProfile 会在首次运行时补出 profile；桌宠侧安装桥接
    前若一个 profile 都没有，应按同一套三件套（manifest + cordis.patch.yml +
    pnpm-workspace.yaml）补出默认 web profile 再继续，而不是把全新用户挡住。
    """

    def test_install_creates_default_web_profile_when_none_exists(self, tmp_path, monkeypatch):
        plugin = tmp_path / "bundled" / "dsh-pet-bridge"
        plugin.mkdir(parents=True)
        (tmp_path / "profiles").mkdir()
        monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", tmp_path)
        monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
        monkeypatch.setattr(agent_link, "_pnpm_command", lambda: ["pnpm"])
        calls: list[str] = []

        def fake_run(profile_dir, *args):
            calls.append(profile_dir.name)
            data = json.loads((profile_dir / "package.json").read_text(encoding="utf-8"))
            data.setdefault("dependencies", {})[agent_link.DSH_PLUGIN_NAME] = f"link:{plugin}"
            (profile_dir / "package.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            return 0, ""

        monkeypatch.setattr(agent_link, "_run_pnpm", fake_run)

        ok, message, _ = DshMonitor.install_bridge()

        assert ok is True, message
        assert calls == ["web"], "应先补出默认 web profile 再在其中安装"
        web = tmp_path / "profiles" / "web"
        assert (web / "cordis.patch.yml").is_file(), "initProfile 三件套之一"
        assert (web / "pnpm-workspace.yaml").is_file(), "initProfile 三件套之二"
        manifest = json.loads((web / "package.json").read_text(encoding="utf-8"))
        bundles = manifest["dsh"]["profile"]["bundles"]
        assert bundles[:2] == [
            "@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app",
        ], "web 预设 bundles 应与 dsh-app-boot 的模板一致"
        assert agent_link.DSH_PLUGIN_NAME in bundles, "安装后 bundles 层应登记桥接插件"
