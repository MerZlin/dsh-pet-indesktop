# -*- coding: utf-8 -*-
"""Windows 环境适配回归：node/pnpm 定位不再写死（issue: 桌宠找不到 pnpm）。

现象：用户用 nvm（POSIX/nvm-windows）管理 Node、或把 pnpm 装在自己的目录里，
桌宠（PyInstaller 打包、常由开机自启/Explorer 拉起）仍报
「需要 pnpm，自动安装失败」。两处「环境写死」：

1. ``node_runtime.augmented_path()`` 在 Windows 上直接返回进程 PATH，没有任何
   包管理器目录兜底——GUI 进程继承的常是**登录时缓存**的旧环境块，用户刚装好的
   nvm/pnpm 目录不在里面；
2. ``agent_link`` 找 pnpm JS 入口只认 ``node_modules/pnpm/bin/pnpm.mjs`` 一种布局：
   nvm 版本目录（``node_modules`` / ``lib/node_modules``）、pnpm ≤10 的
   ``bin/pnpm.cjs``、``.cmd`` 包装脚本、独立安装的 ``pnpm.exe`` 全部漏掉。

平台无关：目录都用 tmp_path 构造，Windows 专有行为（PATHEXT 解析）单独 skip。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pet import node_runtime


def _dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class _FakeWindowsLayout:
    """一台 Windows 机器的目录画像：nvm-windows + pnpm + npm + Volta。"""

    def __init__(self, tmp_path: Path) -> None:
        self.home = _dir(tmp_path / "user")
        self.nvm_home = _dir(tmp_path / "nvm")
        self.nvm_v18 = _dir(self.nvm_home / "v18.20.5")
        self.nvm_v20 = _dir(self.nvm_home / "v20.11.1")
        self.nvm_symlink = _dir(tmp_path / "nodejs")
        self.pnpm_home = _dir(tmp_path / "pnpm-home")
        self.appdata = _dir(tmp_path / "roaming")
        self.npm_global = _dir(self.appdata / "npm")
        self.local = _dir(tmp_path / "local")
        _dir(self.local / "pnpm")  # pnpm 独立安装的默认 bin 目录
        self.volta_bin = _dir(self.local / "Volta" / "bin")
        self.program_files = _dir(tmp_path / "pf")
        self.nodejs_dir = _dir(self.program_files / "nodejs")
        self.missing = tmp_path / "pf86" / "nodejs"  # 故意不存在

    @property
    def env(self) -> dict[str, str]:
        return {
            "NVM_HOME": str(self.nvm_home),
            "NVM_SYMLINK": str(self.nvm_symlink),
            "PNPM_HOME": str(self.pnpm_home),
            "APPDATA": str(self.appdata),
            "LOCALAPPDATA": str(self.local),
            "ProgramFiles": str(self.program_files),
            "ProgramFiles(x86)": str(self.missing.parent),
            "USERPROFILE": str(self.home),
            "VOLTA_HOME": str(self.local / "Volta"),
        }


# ============================================================================
# 1. node_runtime：Windows 增强 PATH
# ============================================================================
class TestWindowsAugmentedPath:
    def test_manager_dirs_cover_nvm_pnpm_and_defaults(self, tmp_path):
        layout = _FakeWindowsLayout(tmp_path)
        got = [str(p) for p in node_runtime._windows_extra_bin_dirs(layout.env, layout.home)]

        assert str(layout.nvm_v18) in got, "nvm-windows 版本目录（全局包与 pnpm.cmd 落在这里）"
        assert str(layout.nvm_v20) in got
        assert str(layout.nvm_symlink) in got
        assert str(layout.pnpm_home) in got
        assert str(layout.npm_global) in got, "%APPDATA%\\npm（npm 全局 shim）"
        assert str(layout.local / "pnpm") in got, "%LOCALAPPDATA%\\pnpm（pnpm 独立安装默认目录）"
        assert str(layout.volta_bin) in got
        assert str(layout.nodejs_dir) in got

    def test_missing_dirs_are_not_injected(self, tmp_path):
        layout = _FakeWindowsLayout(tmp_path)
        got = [str(p) for p in node_runtime._windows_extra_bin_dirs(layout.env, layout.home)]

        assert str(layout.missing) not in got, "不存在的目录不该塞进 PATH"
        assert "" not in got

    def test_augmented_path_keeps_process_path_first(self, tmp_path, monkeypatch):
        """进程 PATH 优先（不劫持用户既有选择），注册表旧值与包管理器目录补在其后。"""
        layout = _FakeWindowsLayout(tmp_path)
        stale_system32 = _dir(tmp_path / "windows" / "system32")
        registry_only = _dir(layout.home / ".local" / "bin")

        monkeypatch.setattr(node_runtime, "_is_windows", lambda: True)
        monkeypatch.setattr(node_runtime, "_windows_registry_env", lambda *a, **k: {})
        monkeypatch.setattr(node_runtime, "_windows_registry_path_entries", lambda: [str(registry_only)])
        for key, value in layout.env.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("PATH", str(stale_system32))

        entries = node_runtime.augmented_path().split(os.pathsep)

        assert entries[0] == str(stale_system32)
        assert entries.index(str(registry_only)) == 1, "注册表里更完整的 PATH 必须补进来"
        assert str(layout.nvm_v18) in entries
        assert len(entries) == len(set(entries)), "增强 PATH 不应有重复项"

    @pytest.mark.skipif(os.name != "nt", reason="shutil.which 的 PATHEXT 解析是 Windows 行为")
    def test_which_finds_pnpm_cmd_from_stale_env(self, tmp_path, monkeypatch):
        """回归：登录时缓存的环境块里没有 nvm，桌宠仍要能找到 pnpm.cmd。"""
        layout = _FakeWindowsLayout(tmp_path)
        _file(layout.nvm_v18 / "pnpm.cmd", "@echo off\n")
        monkeypatch.setattr(node_runtime, "_windows_registry_env", lambda *a, **k: {})
        monkeypatch.setattr(node_runtime, "_windows_registry_path_entries", lambda: [])
        for key, value in layout.env.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("PATH", str(_dir(tmp_path / "windows" / "system32")))

        found = node_runtime.which("pnpm")

        assert found is not None
        assert Path(found).parent == layout.nvm_v18
        assert Path(found).name.lower() == "pnpm.cmd"

    def test_posix_extras_are_prepended(self, tmp_path, monkeypatch):
        """POSIX 语义不变（Issue #67：Finder 极简 PATH 前置包管理器目录）。"""
        pnpm_home = _dir(tmp_path / "pnpm-home")
        monkeypatch.setattr(node_runtime, "_is_windows", lambda: False)
        monkeypatch.setattr(node_runtime, "_posix_extra_bin_dirs", lambda env, home: [pnpm_home])
        monkeypatch.setenv("PATH", str(tmp_path / "usr-bin"))
        entries = node_runtime.augmented_path().split(os.pathsep)

        assert entries[0] == str(pnpm_home)

    def test_percent_vars_are_expanded_case_insensitively(self, tmp_path):
        value = ";".join([str(tmp_path / "windows"), "%nvm_symlink%", str(tmp_path / "pf" / "nodejs"), ""])
        entries = node_runtime._split_path_entries(value, {"NVM_SYMLINK": str(tmp_path / "nodejs")}, sep=";")
        assert entries == [
            str(tmp_path / "windows"),
            str(tmp_path / "nodejs"),
            str(tmp_path / "pf" / "nodejs"),
        ]

    def test_dedupe_is_case_insensitive_on_windows(self):
        entries = [r"C:\Windows", r"c:\windows", r"C:\NVM", "", "  "]
        assert node_runtime._dedupe_path_entries(entries, case_insensitive=True) == [
            r"C:\Windows",
            r"C:\NVM",
        ]

    def test_posix_extra_dirs_honour_pnpm_home(self, tmp_path, monkeypatch):
        pnpm_home = _dir(tmp_path / "pnpm-home")
        nvm_dir = _dir(tmp_path / "custom-nvm" / "versions" / "node" / "v18.20.5" / "bin")
        monkeypatch.setenv("PNPM_HOME", str(pnpm_home))
        monkeypatch.setenv("NVM_DIR", str(tmp_path / "custom-nvm"))
        got = [str(p) for p in node_runtime._posix_extra_bin_dirs(os.environ, tmp_path / "home")]

        assert str(pnpm_home) in got
        assert str(nvm_dir) in got, "$NVM_DIR 指向自定义 nvm 目录（用户 B 的场景）"


# ============================================================================
# 2. node_runtime：全局 node_modules 根目录
# ============================================================================
class TestGlobalNodeModulesRoots:
    def test_windows_roots_cover_nvm_and_pnpm_global(self, tmp_path, monkeypatch):
        layout = _FakeWindowsLayout(tmp_path)
        nvm_modules = _dir(layout.nvm_v18 / "node_modules")
        pnpm_global = _dir(layout.local / "pnpm" / "global" / "5" / "node_modules")
        npm_global = _dir(layout.appdata / "npm" / "node_modules")
        monkeypatch.setattr(node_runtime, "_is_windows", lambda: True)
        monkeypatch.setattr(node_runtime, "_windows_registry_env", lambda *a, **k: {})
        for key, value in layout.env.items():
            monkeypatch.setenv(key, value)

        roots = [str(p) for p in node_runtime.global_node_modules_roots()]

        assert str(nvm_modules) in roots
        assert str(pnpm_global) in roots
        assert str(npm_global) in roots
        assert all(Path(p).is_dir() for p in roots), "共享根目录只返回真实存在的"

    def test_posix_roots_cover_custom_nvm_dir(self, tmp_path, monkeypatch):
        modules = _dir(tmp_path / "custom-nvm" / "versions" / "node" / "v18.20.5" / "lib" / "node_modules")
        monkeypatch.setattr(node_runtime, "_is_windows", lambda: False)
        monkeypatch.setenv("NVM_DIR", str(tmp_path / "custom-nvm"))

        roots = [str(p) for p in node_runtime.global_node_modules_roots()]

        assert str(modules) in roots


# ============================================================================
# 3. Linux / macOS：POSIX 分支的目录发现与布局（CI 的 ubuntu / macos runner 上真跑）
# ============================================================================
def _same_path(found, expected: Path) -> bool:
    return found is not None and Path(found).resolve() == expected.resolve()


class _FakePosixHome:
    """一台 Linux/macOS 机器的家目录画像（nvm / volta / fnm / pnpm / yarn / bun）。"""

    def __init__(self, tmp_path: Path) -> None:
        self.home = _dir(tmp_path / "home")
        self.local_bin = _dir(self.home / ".local" / "bin")
        self.local_lib = _dir(self.home / ".local" / "lib" / "node_modules")
        self.local_share_pnpm = _dir(self.home / ".local" / "share" / "pnpm")
        self.npm_global_bin = _dir(self.home / ".npm-global" / "bin")
        self.npm_global_lib = _dir(self.home / ".npm-global" / "lib" / "node_modules")
        self.library_pnpm = _dir(self.home / "Library" / "pnpm")
        self.nvm_bin = _dir(self.home / ".nvm" / "versions" / "node" / "v18.20.5" / "bin")
        self.nvm_lib = _dir(self.home / ".nvm" / "versions" / "node" / "v18.20.5" / "lib" / "node_modules")
        self.volta_bin = _dir(self.home / ".volta" / "bin")
        self.volta_lib = _dir(self.home / ".volta" / "tools" / "image" / "node" / "v20.11.1" / "lib" / "node_modules")
        self.bun_bin = _dir(self.home / ".bun" / "bin")
        self.bun_lib = _dir(self.home / ".bun" / "install" / "global" / "node_modules")
        self.yarn_bin = _dir(self.home / ".yarn" / "bin")
        self.yarn_lib = _dir(self.home / ".config" / "yarn" / "global" / "node_modules")
        self.asdf_shims = _dir(self.home / ".asdf" / "shims")
        self.pnpm_store = _dir(self.home / ".local" / "share" / "pnpm" / "global" / "5" / "node_modules")

    def use(self, monkeypatch) -> None:
        """把这个假家目录装进 node_runtime 的平台/家目录 seam，并隔离宿主环境。

        只 mock ``_is_windows`` / ``_home`` 还不够：POSIX 的 nvm/fnm 探测会按
        ``NVM_DIR`` / ``FNM_DIR`` 等环境变量重定向根目录——CI runner 的真实
        环境若带着这些变量（如 GitHub runner 常设 ``NVM_DIR``），探测就会绕开
        临时家目录、找不到本类构造的布局（ubuntu CI 实测红）。测试要的是
        「这台假机器」，宿主环境变量在这里全是噪声，统一清掉。
        注意：``VOLTA_HOME`` 等即使保留也不影响各目录断言（volta/bun/yarn/
        asdf/pnpm 的默认位置都来自 ``_POSIX_HOME_BIN_DIRS``、与 home 相对），
        但一并清除可防止探测被宿主值带偏。
        """
        monkeypatch.setattr(node_runtime, "_is_windows", lambda: False)
        monkeypatch.setattr(node_runtime, "_home", lambda: self.home)
        for name in (
            "NVM_DIR",
            "NVM_HOME",
            "NVM_SYMLINK",
            "FNM_DIR",
            "VOLTA_HOME",
            "BUN_INSTALL",
            "PNPM_HOME",
        ):
            monkeypatch.delenv(name, raising=False)


class TestPosixEnvironment:
    def test_bin_dirs_cover_all_managers(self, tmp_path):
        """Linux/macOS 各包管理器目录都要能补进 PATH。"""
        posix = _FakePosixHome(tmp_path)
        got = [str(p) for p in node_runtime._posix_extra_bin_dirs({}, posix.home)]

        for expected in (
            posix.local_bin,
            posix.local_share_pnpm,
            posix.npm_global_bin,
            posix.library_pnpm,  # macOS 的 pnpm 独立安装
            posix.nvm_bin,
            posix.volta_bin,
            posix.bun_bin,
            posix.yarn_bin,
            posix.asdf_shims,
        ):
            assert str(expected) in got, expected

    def test_bin_dirs_honour_env_overrides(self, tmp_path):
        """自定义 NVM_DIR / PNPM_HOME / FNM_DIR 也要认（$NVM_DIR 非默认家目录）。"""
        custom = _dir(tmp_path / "custom")
        nvm_bin = _dir(custom / "nvm" / "versions" / "node" / "v22.12.0" / "bin")
        pnpm_home = _dir(custom / "pnpm-home")
        fnm_bin = _dir(custom / "fnm" / "node-versions" / "v20.11.1" / "installation" / "bin")
        env = {
            "NVM_DIR": str(custom / "nvm"),
            "PNPM_HOME": str(pnpm_home),
            "FNM_DIR": str(custom / "fnm"),
        }
        got = [str(p) for p in node_runtime._posix_extra_bin_dirs(env, tmp_path / "home")]

        assert str(nvm_bin) in got
        assert str(pnpm_home) in got
        assert str(fnm_bin) in got

    def test_augmented_path_prepends_extras_before_process_path(self, tmp_path, monkeypatch):
        """POSIX 语义：额外目录**前置**（Issue #67 Finder 极简 PATH），且不重复。"""
        posix = _FakePosixHome(tmp_path)
        posix.use(monkeypatch)
        usr_bin = str(_dir(tmp_path / "usr-bin"))
        monkeypatch.setenv("PATH", usr_bin)

        entries = node_runtime.augmented_path().split(os.pathsep)

        assert entries.index(str(posix.nvm_bin)) < entries.index(usr_bin)
        assert len(entries) == len(set(entries)), "增强 PATH 不应有重复项"

    def test_static_roots_cover_homebrew_and_npm_global(self, tmp_path, monkeypatch):
        """Apple Silicon(Homebrew) / Intel(Homebrew) / linuxbrew 与 npm 全局根。"""
        posix = _FakePosixHome(tmp_path)
        posix.use(monkeypatch)

        roots = [Path(p) for p in node_runtime.static_node_modules_roots()]

        # 用 Path 比较：在 Windows 宿主上跑 POSIX 分支时分隔符会被规范化
        assert Path("/opt/homebrew/lib/node_modules") in roots, "macOS arm64 Homebrew"
        assert Path("/usr/local/lib/node_modules") in roots, "macOS Intel Homebrew"
        assert Path("/home/linuxbrew/.linuxbrew/lib/node_modules") in roots, "Linuxbrew"
        assert posix.npm_global_lib in roots
        assert posix.local_lib in roots

    def test_roots_cover_nvm_volta_fnm_pnpm_yarn_bun(self, tmp_path, monkeypatch):
        """Linux/macOS 全局包根：版本管理器版本目录 + pnpm/yarn/bun 全局目录。"""
        posix = _FakePosixHome(tmp_path)
        fnm_lib = _dir(posix.home / ".local" / "share" / "fnm" / "node-versions" / "v20.11.1" / "installation" / "lib" / "node_modules")
        monkeypatch.setattr(node_runtime, "_POSIX_ABS_NODE_MODULES", ())
        posix.use(monkeypatch)

        roots = [str(p) for p in node_runtime.global_node_modules_roots()]

        assert str(posix.nvm_lib) in roots
        assert str(posix.volta_lib) in roots
        assert str(posix.local_lib) in roots
        assert str(posix.npm_global_lib) in roots
        assert str(posix.yarn_lib) in roots
        assert str(posix.bun_lib) in roots
        assert str(posix.pnpm_store) in roots
        assert str(fnm_lib) in roots
        assert all(Path(p).is_dir() for p in roots), "共享根目录只返回真实存在的"

    def test_stale_nvm_dir_env_falls_back_to_default_home(self, tmp_path, monkeypatch):
        """产品缺陷回归：NVM_DIR/FNM_DIR 指向已移除/失效目录时不得丢掉默认布局。

        真实 Linux 场景：环境里 NVM_DIR 残留旧路径（升级/换机/配置删除），目录
        已不存在——若按该变量直接探测，默认家目录下真实存在的 nvm/fnm 布局会
        整个被丢弃，桌宠找不到全局 pnpm/npm（「需要 pnpm，自动安装失败」）。
        """
        posix = _FakePosixHome(tmp_path)
        posix.use(monkeypatch)
        monkeypatch.setenv("NVM_DIR", str(tmp_path / "stale-nvm"))  # 不存在
        monkeypatch.setenv("FNM_DIR", str(tmp_path / "stale-fnm"))  # 不存在
        fnm_lib = _dir(posix.home / ".local" / "share" / "fnm" / "node-versions" / "v20.11.1" / "installation" / "lib" / "node_modules")

        entries = node_runtime.augmented_path().split(os.pathsep)
        assert str(posix.nvm_bin) in entries, "NVM_DIR 失效时仍应找到默认 ~/.nvm 布局"

        roots = [str(p) for p in node_runtime.global_node_modules_roots()]
        assert str(posix.nvm_lib) in roots
        assert str(fnm_lib) in roots, "FNM_DIR 失效时仍应找到默认 fnm 布局"

    def test_node_modules_root_layout_is_recognized(self, tmp_path, monkeypatch):
        """共享根本身就是全局 node_modules 时（nvm 的 lib/node_modules）也要命中。"""
        from pet import agent_link

        root = _dir(tmp_path / "nvm" / "versions" / "node" / "v18.20.5" / "lib" / "node_modules")
        cli = _file(root / "pnpm" / "bin" / "pnpm.cjs")
        monkeypatch.setattr(agent_link, "_which", lambda name: None)
        monkeypatch.setattr(agent_link, "_package_roots", lambda: [root])

        assert _same_path(agent_link._find_pnpm_cli(), cli)

    def test_pnpm_found_without_any_path_entry(self, tmp_path, monkeypatch):
        """端到端：PATH 里什么都没有时（macOS .app / Linux 桌面启动器）仍能定位 pnpm。"""
        from pet import agent_link

        posix = _FakePosixHome(tmp_path)
        cli = _file(posix.local_lib / "pnpm" / "bin" / "pnpm.cjs")
        monkeypatch.setattr(node_runtime, "_POSIX_ABS_NODE_MODULES", ())
        posix.use(monkeypatch)
        monkeypatch.setenv("PATH", str(_dir(tmp_path / "empty-bin")))
        monkeypatch.setattr(agent_link, "_which", lambda name: None)

        assert _same_path(agent_link._find_pnpm_cli(), cli)

    def test_posix_shell_shim_is_resolved_to_js_entry(self, tmp_path, monkeypatch):
        """POSIX 包装脚本（无扩展名、`$basedir` 相对路径）要解析出背后的 .cjs。

        布局是 POSIX（nvm 的 `bin/pnpm` → `lib/node_modules/pnpm/bin/pnpm.cjs`），
        但解析逻辑与平台无关，三平台都跑。
        """
        from pet import agent_link

        node_root = _dir(tmp_path / "nvm" / "versions" / "node" / "v18.20.5")
        cli = _file(node_root / "lib" / "node_modules" / "pnpm" / "bin" / "pnpm.cjs")
        shim = _file(
            node_root / "bin" / "pnpm",
            '#!/bin/sh\nbasedir=$(dirname "$0")\nexec node "$basedir/../lib/node_modules/pnpm/bin/pnpm.cjs" "$@"\n',
        )
        os.chmod(shim, 0o755)
        monkeypatch.setattr(agent_link, "_which", lambda name: str(shim) if name == "pnpm" else None)
        monkeypatch.setattr(agent_link, "_package_roots", lambda: [])

        assert _same_path(agent_link._find_pnpm_cli(), cli)

    def test_shell_shim_command_runs_without_node_prefix(self, tmp_path, monkeypatch):
        """POSIX 包装脚本自带 shebang：直接执行，不再拼 [node, cli]。"""
        from pet import agent_link

        shim = _file(tmp_path / "pnpm", '#!/bin/sh\nexec node "$basedir/pnpm.cjs"\n')
        monkeypatch.setattr(agent_link, "_pnpm_cli", lambda: str(shim))
        monkeypatch.setattr(agent_link, "_which", lambda name: None)

        assert agent_link._pnpm_command() == [str(shim)]


class TestVersionManagerDiscovery:
    """issue 实报的 nvm 现场：pnpm 装在 ~/nvm/.../bin/pnpm（**无点号** nvm）。

    reporter 原话：pnpm 在 ~/nvm/versions/node/v18.20.5/bin/pnpm，
    而源码只按 shim 同级的 node_modules 找，于是只能自己改源码。
    """

    def test_posix_nvm_without_dot_is_covered(self, tmp_path, monkeypatch):
        """GUI 启动（macOS .app / 桌面启动器）拿不到 shell 里设的 NVM_DIR，
        只认 ~/.nvm 会漏掉自定义根 ~/nvm。"""
        from pet import agent_link

        home = _dir(tmp_path / "home")
        node_root = home / "nvm" / "versions" / "node" / "v18.20.5"
        bin_dir = _dir(node_root / "bin")
        lib = _dir(node_root / "lib" / "node_modules")
        cli = _file(lib / "pnpm" / "bin" / "pnpm.cjs")
        monkeypatch.setattr(node_runtime, "_is_windows", lambda: False)
        monkeypatch.setattr(node_runtime, "_home", lambda: home)
        monkeypatch.setattr(node_runtime, "_POSIX_ABS_NODE_MODULES", ())
        monkeypatch.delenv("NVM_DIR", raising=False)

        assert str(bin_dir) in [str(p) for p in node_runtime._posix_extra_bin_dirs({}, home)]
        assert str(lib) in [str(p) for p in node_runtime.global_node_modules_roots()]

        monkeypatch.setattr(agent_link, "_which", lambda name: None)
        assert _same_path(agent_link._find_pnpm_cli(), cli)

    def test_posix_nvm_dir_env_plus_defaults_all_scanned(self, tmp_path):
        """NVM_DIR 自定义根 + ~/.nvm + ~/nvm 三者都要扫（谁存在算谁）。"""
        home = _dir(tmp_path / "home")
        custom_bin = _dir(tmp_path / "custom-nvm" / "versions" / "node" / "v20.11.1" / "bin")
        dot_bin = _dir(home / ".nvm" / "versions" / "node" / "v18.20.5" / "bin")
        plain_bin = _dir(home / "nvm" / "versions" / "node" / "v22.12.0" / "bin")

        got = [
            str(p)
            for p in node_runtime._version_manager_bin_dirs(
                {"NVM_DIR": str(tmp_path / "custom-nvm")},
                home,
                windows=False,
            )
        ]

        assert str(custom_bin) in got
        assert str(dot_bin) in got
        assert str(plain_bin) in got

    def test_windows_default_nvm_home_when_env_missing(self, tmp_path):
        """nvm-windows 默认装在 %APPDATA%\\nvm：环境变量缺失时也要认。"""
        appdata = _dir(tmp_path / "AppData" / "Roaming")
        version_dir = _dir(appdata / "nvm" / "v20.11.1")

        got = [
            str(p)
            for p in node_runtime._version_manager_bin_dirs(
                {"APPDATA": str(appdata)},
                tmp_path / "home",
                windows=True,
            )
        ]

        assert str(version_dir) in got


class TestConfiguredPnpmBin:
    """手动指定 pnpm 入口（config.pnpm_bin）：面向"环境特殊又不想改环境变量"的用户。

    优先级：config.pnpm_bin → DSH_PNPM_BIN → 内置自动发现；配错只回落，不致命。
    """

    def _isolate(self, monkeypatch):
        from pet import agent_link

        monkeypatch.setattr(agent_link, "_configured_pnpm_bin", "")
        monkeypatch.delenv("DSH_PNPM_BIN", raising=False)
        monkeypatch.setattr(node_runtime, "_is_windows", lambda: False)
        monkeypatch.setattr(node_runtime, "_POSIX_ABS_NODE_MODULES", ())

    def test_configured_file_wins_over_discovery(self, tmp_path, monkeypatch):
        from pet import agent_link

        self._isolate(monkeypatch)
        cli = _file(tmp_path / "custom" / "pnpm.cjs")
        monkeypatch.setattr(agent_link, "_which", lambda name: None)
        monkeypatch.setattr(agent_link, "global_node_modules_roots", lambda: [])

        agent_link.set_configured_pnpm_bin(str(cli))

        assert _same_path(agent_link._find_pnpm_cli(), cli)

    def test_configured_directory_is_resolved(self, tmp_path, monkeypatch):
        from pet import agent_link

        self._isolate(monkeypatch)
        cli = _file(tmp_path / "tools" / "node_modules" / "pnpm" / "bin" / "pnpm.cjs")
        monkeypatch.setattr(agent_link, "_which", lambda name: None)
        monkeypatch.setattr(agent_link, "global_node_modules_roots", lambda: [])

        agent_link.set_configured_pnpm_bin(str(tmp_path / "tools"))

        assert _same_path(agent_link._find_pnpm_cli(), cli)

    def test_configured_wins_over_env(self, tmp_path, monkeypatch):
        from pet import agent_link

        self._isolate(monkeypatch)
        configured = _file(tmp_path / "configured" / "pnpm.cjs")
        env_cli = _file(tmp_path / "env" / "pnpm.cjs")
        monkeypatch.setenv("DSH_PNPM_BIN", str(env_cli))
        monkeypatch.setattr(agent_link, "_which", lambda name: None)
        monkeypatch.setattr(agent_link, "global_node_modules_roots", lambda: [])

        agent_link.set_configured_pnpm_bin(str(configured))

        assert _same_path(agent_link._find_pnpm_cli(), configured)

    def test_env_still_used_when_config_empty(self, tmp_path, monkeypatch):
        from pet import agent_link

        self._isolate(monkeypatch)
        env_cli = _file(tmp_path / "env" / "pnpm.cjs")
        monkeypatch.setenv("DSH_PNPM_BIN", str(env_cli))
        monkeypatch.setattr(agent_link, "_which", lambda name: None)
        monkeypatch.setattr(agent_link, "global_node_modules_roots", lambda: [])

        agent_link.set_configured_pnpm_bin("")

        assert _same_path(agent_link._find_pnpm_cli(), env_cli)

    def test_broken_config_falls_back_to_discovery(self, tmp_path, monkeypatch):
        """配错路径不该让桥接彻底装不上：只记警告并回落到自动发现。"""
        from pet import agent_link

        self._isolate(monkeypatch)
        home = _dir(tmp_path / "home")
        cli = _file(home / ".nvm" / "versions" / "node" / "v18.20.5" / "lib" / "node_modules" / "pnpm" / "bin" / "pnpm.cjs")
        monkeypatch.setattr(node_runtime, "_home", lambda: home)
        monkeypatch.setattr(agent_link, "_which", lambda name: None)

        agent_link.set_configured_pnpm_bin(str(tmp_path / "nope" / "pnpm"))

        assert _same_path(agent_link._find_pnpm_cli(), cli)

    def test_apply_config_syncs_configured_value(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QApplication

        from pet import agent_link
        from pet.config import Config

        QApplication.instance() or QApplication([])
        monkeypatch.setattr(agent_link, "_configured_pnpm_bin", "")
        cfg = Config(base=tmp_path)
        cfg.set("pnpm_bin", "/opt/custom/pnpm")

        class Win:
            def show_bubble(self, *args, **kwargs):
                pass

            def isVisible(self):
                return True

        manager = agent_link.AgentLinkManager(Win(), cfg)
        try:
            assert agent_link.configured_pnpm_bin() == "/opt/custom/pnpm"
        finally:
            manager.shutdown()
            agent_link.set_configured_pnpm_bin("")


# ============================================================================
# 4. agent_link：pnpm / npm JS 入口多布局发现（Windows 布局为主）
# ============================================================================


class TestPnpmCliDiscovery:
    def test_resolves_windows_cmd_shim_to_real_entry(self, tmp_path, monkeypatch):
        """nvm-windows / Program Files 布局：pnpm 是 .cmd 包装，真实入口在包装脚本里。"""
        from pet import agent_link

        node_root = tmp_path / "nvm" / "v18.20.5"
        shim = _file(
            node_root / "pnpm.cmd",
            '@ECHO off\nendLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\node_modules\\pnpm\\bin\\pnpm.mjs" %*\n',
        )
        cli = _file(node_root / "node_modules" / "pnpm" / "bin" / "pnpm.mjs")
        monkeypatch.setattr(agent_link, "_which", lambda name: str(shim) if name == "pnpm" else None)

        assert _same_path(agent_link._find_pnpm_cli(), cli)

    def test_resolves_lib_node_modules_cjs_layout(self, tmp_path, monkeypatch):
        """用户 B 的场景：nvm 下 pnpm 的入口是 lib/node_modules/pnpm/bin/pnpm.cjs。"""
        from pet import agent_link

        node_root = tmp_path / "nvm" / "versions" / "node" / "v18.20.5"
        cli = _file(node_root / "lib" / "node_modules" / "pnpm" / "bin" / "pnpm.cjs")
        monkeypatch.setattr(agent_link, "_which", lambda name: None)
        monkeypatch.setattr(agent_link, "_package_roots", lambda: [node_root / "bin", node_root])

        assert _same_path(agent_link._find_pnpm_cli(), cli)

    def test_supports_cjs_behind_unparsable_shim(self, tmp_path, monkeypatch):
        """pnpm ≤10 的 JS 入口是 pnpm.cjs：只认 pnpm.mjs 的旧实现会漏。"""
        from pet import agent_link

        nodejs_dir = tmp_path / "nodejs"
        cli = _file(nodejs_dir / "node_modules" / "pnpm" / "bin" / "pnpm.cjs")
        shim = _file(nodejs_dir / "pnpm.cmd", "@echo off\n")  # 壳里没有可解析的路径
        monkeypatch.setattr(agent_link, "_which", lambda name: str(shim) if name == "pnpm" else None)
        monkeypatch.setattr(agent_link, "_package_roots", lambda: [])

        assert _same_path(agent_link._find_pnpm_cli(), cli)

    def test_dsh_pnpm_bin_accepts_js_file_and_directory(self, tmp_path, monkeypatch):
        from pet import agent_link

        cli = _file(tmp_path / "custom" / "pnpm.cjs")
        monkeypatch.setattr(agent_link, "_which", lambda name: None)
        monkeypatch.setattr(agent_link, "_package_roots", lambda: [])

        monkeypatch.setenv("DSH_PNPM_BIN", str(cli))
        assert _same_path(agent_link._find_pnpm_cli(), cli)

        monkeypatch.setenv("DSH_PNPM_BIN", str(cli.parent))
        assert _same_path(agent_link._find_pnpm_cli(), cli)

    def test_pnpm_command_runs_standalone_binary_without_node(self, tmp_path, monkeypatch):
        """独立安装的 pnpm.exe 自带 node：不能再拼 [node, cli]。"""
        from pet import agent_link

        exe = _file(tmp_path / "pnpm-home" / "pnpm.exe", "MZ")
        monkeypatch.setattr(agent_link, "_pnpm_cli", lambda: str(exe))
        monkeypatch.setattr(agent_link, "_which", lambda name: None)

        assert agent_link._pnpm_command() == [str(exe)]

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(agent_link.subprocess, "run", fake_run)
        rc, _ = agent_link._run_pnpm(tmp_path, "add", "pkg")
        assert rc == 0
        assert calls[0] == [str(exe), "add", "pkg"]

    def test_pnpm_command_prefixes_js_cli_with_node(self, tmp_path, monkeypatch):
        from pet import agent_link

        cli = _file(tmp_path / "pnpm.mjs")
        monkeypatch.setattr(agent_link, "_pnpm_cli", lambda: str(cli))
        monkeypatch.setattr(
            agent_link,
            "_which",
            lambda name: "C:/nodejs/node.exe" if name == "node" else None,
        )

        assert agent_link._pnpm_command() == ["C:/nodejs/node.exe", str(cli)]

    def test_pnpm_command_wraps_cmd_shim_with_cmd_exe(self, tmp_path, monkeypatch):
        """对齐 harness_launcher._wrap_cmd 的实测结论：Windows 上 .cmd 必须经 cmd 启动。"""
        from pet import agent_link

        shim = _file(tmp_path / "pnpm.cmd", "@echo off\n")
        monkeypatch.setattr(agent_link, "_pnpm_cli", lambda: str(shim))

        assert agent_link._pnpm_command() == ["cmd.exe", "/c", str(shim)]

    def test_run_pnpm_failure_messages_name_the_escape_hatch(self, tmp_path, monkeypatch):
        from pet import agent_link

        monkeypatch.setattr(agent_link, "_pnpm_cli", lambda: None)
        monkeypatch.setattr(agent_link, "_which", lambda name: None)
        rc, out = agent_link._run_pnpm(tmp_path, "add", "pkg")
        assert rc == 127 and "node" in out

        monkeypatch.setattr(
            agent_link,
            "_which",
            lambda name: "C:/nodejs/node.exe" if name == "node" else None,
        )
        rc, out = agent_link._run_pnpm(tmp_path, "add", "pkg")
        assert rc == 127
        assert "DSH_PNPM_BIN" in out, "失败文案要给出环境变量逃生阀"

    def test_npm_cli_found_in_nvm_lib_layout(self, tmp_path, monkeypatch):
        from pet import agent_link

        node_root = tmp_path / "nvm" / "v18.20.5"
        cli = _file(node_root / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js")
        monkeypatch.setattr(agent_link, "_which", lambda name: str(node_root / "npm") if name == "npm" else None)
        monkeypatch.setattr(agent_link, "_package_roots", lambda: [node_root])

        assert _same_path(agent_link._npm_cli(), cli)


# ============================================================================
# 5. harness_launcher：复用共享全局根目录（一键启动 DSH）
# ============================================================================
class TestHarnessGlobalRoots:
    def test_posix_launch_finds_dsh_under_npm_global_root(self, tmp_path, monkeypatch):
        """macOS/Linux：dsh 装在全局根（PATH 上没有 dsh）也要能一键拉起。"""
        from pet import harness_launcher as hl

        home = _dir(tmp_path / "home")
        root = _dir(home / ".npm-global" / "lib" / "node_modules")
        bin_js = _file(root / "@deepseek-ai" / "dsh" / "lib" / "bin.js", "#!/usr/bin/env node\n")
        monkeypatch.setattr(node_runtime, "_is_windows", lambda: False)
        monkeypatch.setattr(node_runtime, "_home", lambda: home)
        monkeypatch.setattr(node_runtime, "_POSIX_ABS_NODE_MODULES", ())
        monkeypatch.setattr(hl, "_which", lambda name: "/usr/local/bin/node" if name == "node" else None)
        monkeypatch.setattr(hl, "_supports_no_open", lambda command: True)

        assert root in hl._npm_global_roots()
        assert hl._find_launch_command(1234) == [
            "/usr/local/bin/node",
            str(bin_js),
            "web",
            "--host",
            "127.0.0.1",
            "--port",
            "1234",
            "--no-open",
        ]

    def test_manual_launch_finds_dsh_under_nvm_windows_root(self, tmp_path, monkeypatch):
        from pet import harness_launcher as hl
        from pet import node_runtime

        root = _dir(tmp_path / "nvm" / "v18.20.5" / "node_modules")
        bin_js = _file(root / "@deepseek-ai" / "dsh" / "lib" / "bin.js", "//")
        monkeypatch.setattr(hl, "global_node_modules_roots", lambda: [root])
        # 静态候选根（%APPDATA%\npm 等）取自真实环境变量：开发机若装了全局 dsh，
        # 真实路径会先于本用例的 nvm 夹具命中，用例就不再只依赖夹具（CI 机器没有
        # 全局 dsh，故此缺陷只在本地暴露）。与同文件 POSIX 用例一致地清空静态
        # 候选，保证断言只反映「版本管理器根也能被找到」这一条产品语义。
        monkeypatch.setattr(node_runtime, "_WINDOWS_NODE_MODULES", ())
        monkeypatch.setattr(hl, "_which", lambda name: "C:/nodejs/node.exe" if name == "node" else None)
        monkeypatch.setattr(hl, "_supports_no_open", lambda command: False)

        assert root in hl._npm_global_roots()
        assert hl._find_launch_command(1234) == [
            "C:/nodejs/node.exe",
            str(bin_js),
            "web",
            "--host",
            "127.0.0.1",
            "--port",
            "1234",
        ]
