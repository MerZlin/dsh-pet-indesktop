# -*- coding: utf-8 -*-
"""随包分发的游戏帧率后端（PresentMon）——打包与文档面测试。

本文件只覆盖**打包**这一面：后端二进制本体、许可证随包、构建脚本接线、非 Windows
不捆它、以及冻结包里的路径契约。采集逻辑本身的测试在 ``tests/test_game_fps.py``
与 ``tests/test_fps_monitor.py``（另两份分工），这里一个字节都不碰它们的判定。

四类断言：

1. **资源本体**：``resources/presentmon/`` 里只有那一个 x64 后端 + 它的 ``LICENSE.txt``
   （没有 arm64 变体、没有第二个 exe）；
2. **门有牙**：``scripts/verify_presentmon.py`` 对仓库里那一份必须零退出，对**篡改过的**
   副本（截断 / 把 PE 头改成 ARM64 / 改名）必须非零退出——真跑子进程看退出码，
   不看代码（门是否"真的会拦"只能由被拦住的运行证明）；
3. **构建接线**：``build_onedir.ps1`` 在 PyInstaller 之前校验、把 ``resources\\presentmon``
   打成 ``--add-data``、并在**产物里**再验一次；``build_linux.sh`` / ``build_macos.sh``
   一个字节都不捆它（源码文本断言——行为测试看不到"有没有接上"）；
4. **冻结布局契约**：``--add-data`` 的目标目录 == ``pet/game_fps.py`` 的
   ``BACKEND_RELATIVE_DIR``，且 ``sys._MEIPASS`` 指向那个布局时
   ``bundled_backend_path()`` 就解析到后端本体。

二进制未入库时（干净克隆、用户决定不入库）第 1、2 条按 ``skip`` 处理并写明原因：
**打包门本身**（第 3 条 + 构建脚本里的两次调用）不依赖二进制存在，仍是硬门。
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_presentmon.py"
BUILD_SCRIPT = ROOT / "scripts" / "build_onedir.ps1"
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"
PACKAGING_DOC = ROOT / "docs" / "ONEDIR_PACKAGING.md"
RESOURCE_DIR = ROOT / "resources" / "presentmon"
LICENSE_FILE = RESOURCE_DIR / "LICENSE.txt"

SHELL_SCRIPTS = {
    "macos": ROOT / "scripts" / "build_macos.sh",
    "linux": ROOT / "scripts" / "build_linux.sh",
}

#: 上游 v2.6.0 release 资产的字节数（与 sha256 互为交叉证据：GitHub release API
#: 的 ``size`` 字段）。它不是从 ``pet/island/game_fps.py`` 抄来的第二份真值。
RELEASE_ASSET_SIZE = 980320

from pet.island import game_fps  # noqa: E402  —— 期望值只有一份，直接问产品

BACKEND = RESOURCE_DIR / game_fps.BACKEND_EXE_NAME
BACKEND_MISSING_REASON = (
    f"{BACKEND.relative_to(ROOT).as_posix()} 不在树上（未入库的本地构建树）；"
    "二进制本体与篡改判定跳过——构建脚本里的两道门（源码文本）不依赖它，照旧生效"
)


def _run(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(ROOT), check=False,
    )


def _json_run(*args: str) -> dict:
    import json

    result = _run("--json", *args)
    payload = json.loads(result.stdout)
    payload["_exit"] = result.returncode
    return payload


# --------------------------------------------------------------------------- 一、资源本体


@pytest.mark.skipif(not BACKEND.is_file(), reason=BACKEND_MISSING_REASON)
class TestShippedResource:
    def test_only_the_x64_backend_is_shipped(self):
        """目录里只有那一个后端 + 许可文本：多一个 exe 就是多一个没人校验的二进制。"""
        assert BACKEND.is_file()
        assert LICENSE_FILE.is_file()
        names = sorted(path.name for path in RESOURCE_DIR.iterdir())
        assert names == sorted([game_fps.BACKEND_EXE_NAME, "LICENSE.txt"]), names
        assert [p.name for p in RESOURCE_DIR.glob("*.exe")] == [game_fps.BACKEND_EXE_NAME]

    def test_no_arm_variant_is_smuggled_in(self):
        """上游 x64 与 ARM64 是两个资产，产品只支持 x64——不留 ARM 命名的文件。"""
        for path in RESOURCE_DIR.iterdir():
            lowered = path.name.lower()
            assert "arm" not in lowered, f"发现 ARM 架构资产：{path.name}"
            assert "aarch" not in lowered, f"发现 ARM 架构资产：{path.name}"

    def test_release_asset_identity(self):
        """字节数与上游 release 资产一致（与 sha256 互为交叉证据）。"""
        import hashlib

        payload = BACKEND.read_bytes()
        assert len(payload) == RELEASE_ASSET_SIZE
        assert hashlib.sha256(payload).hexdigest() == game_fps.REQUIRED_SHA256

    def test_gate_accepts_the_shipped_backend(self):
        """门对仓库里这一份必须说 OK（构建接线就以它为准）。"""
        payload = _json_run()
        assert payload["_exit"] == 0, payload
        assert payload["ok"] is True
        assert payload["machine_name"] == "x86-64"
        assert payload["size"] == RELEASE_ASSET_SIZE

    def test_license_is_verbatim_upstream_mit(self):
        text = LICENSE_FILE.read_text(encoding="utf-8")
        assert "Intel Corporation" in text
        assert "Permission is hereby granted, free of charge" in text
        assert "WITHOUT WARRANTY OF ANY KIND" in text

    def test_license_text_is_embedded_verbatim_in_notices(self):
        """THIRD_PARTY_NOTICES 里那份许可必须与随包文件逐字相同（否则声明是装饰）。"""
        import re

        notices = NOTICES.read_text(encoding="utf-8")
        section = notices.split("## 6. PresentMon", 1)[1]
        blocks = re.findall(r"```text\n(.*?)```\n", section, re.S)
        assert blocks, "通知文件里没有许可正文代码块"
        assert blocks[-1].strip("\n") == LICENSE_FILE.read_text(
            encoding="utf-8").strip("\n")


# --------------------------------------------------------------------------- 二、门有牙


@pytest.mark.skipif(not BACKEND.is_file(), reason=BACKEND_MISSING_REASON)
class TestGateHasTeeth:
    """门必须能拦住"不对的那一份"——这是它在构建管线里唯一的作用。"""

    def _tamper(self, tmp_path: Path, mutate) -> Path:
        target = tmp_path / game_fps.BACKEND_EXE_NAME
        payload = bytearray(BACKEND.read_bytes())
        mutate(payload)
        target.write_bytes(bytes(payload))
        return target

    def test_truncated_copy_is_rejected(self, tmp_path):
        target = tmp_path / game_fps.BACKEND_EXE_NAME
        target.write_bytes(BACKEND.read_bytes()[:100_000])
        payload = _json_run("--path", str(target))
        assert payload["_exit"] == 1
        assert payload["sha256_ok"] is False
        assert any("sha256" in problem for problem in payload["problems"])

    def test_arm64_copy_is_rejected_even_with_the_x64_name(self, tmp_path):
        """只改 PE 头里的机器类型：名字对、sha256 也就不同——两条都要能指出来。"""

        def to_arm64(payload: bytearray) -> None:
            offset = struct.unpack_from("<I", payload, 0x3C)[0]
            struct.pack_into("<H", payload, offset + 4, 0xAA64)

        target = self._tamper(tmp_path, to_arm64)
        payload = _json_run("--path", str(target))
        assert payload["_exit"] == 1
        assert payload["machine_name"] == "ARM64"
        assert payload["machine_ok"] is False
        assert any("架构" in problem for problem in payload["problems"])

    def test_renamed_copy_is_rejected(self, tmp_path):
        """同名不同版靠文件名挡住：换成别的名字即使内容对也要红（版本是身份的一部分）。"""
        target = tmp_path / "PresentMon-2.6.0-x64 (1).exe"
        target.write_bytes(BACKEND.read_bytes())
        payload = _json_run("--path", str(target))
        assert payload["_exit"] == 1
        assert payload["sha256_ok"] is True
        assert payload["name_ok"] is False
        assert any("文件名" in problem for problem in payload["problems"])

    def test_missing_file_is_rejected(self, tmp_path):
        payload = _json_run("--path", str(tmp_path / "nope.exe"))
        assert payload["_exit"] == 1
        assert payload["is_file"] is False

    def test_gate_is_read_only_and_writes_nothing(self):
        """门在构建管线里只读：跑完之后资源目录逐字节不变。"""
        before = sorted(
            (path.name, path.stat().st_size, path.stat().st_mtime_ns)
            for path in RESOURCE_DIR.iterdir())
        assert _run().returncode == 0
        after = sorted(
            (path.name, path.stat().st_size, path.stat().st_mtime_ns)
            for path in RESOURCE_DIR.iterdir())
        assert before == after


class TestGateGuardsAgainstSecondTruth:
    def test_gate_reads_expectations_from_the_product_module(self):
        """期望值必须来自 ``pet/island/game_fps.py``（单一真相源），不许在门里抄一份。

        抄一份的下场是"改了产品没改门"或反过来，两件事都会让门失去意义——
        所以断言的是**它 import 了产品模块**，不是它写了什么字面量。
        """
        text = SCRIPT.read_text(encoding="utf-8")
        assert "from pet.island import game_fps" in text
        assert "game_fps.BACKEND_EXE_NAME" in text
        assert "game_fps.REQUIRED_SHA256" in text
        # 反向断言：门里不许再有这两个常量的**定义**（副本真值）
        assert "REQUIRED_SHA256 =" not in text
        assert "BACKEND_EXE_NAME =" not in text


# --------------------------------------------------------------------------- 三、构建接线


def _build_text() -> str:
    return BUILD_SCRIPT.read_text(encoding="utf-8")


class TestWindowsBuildWiring:
    def test_gate_runs_before_pyinstaller(self):
        text = _build_text()
        assert "verify_presentmon.py" in text
        assert text.index("verify_presentmon") < text.index("python -m PyInstaller")

    def test_gate_failure_aborts_the_build(self):
        text = _build_text()
        call = text.index("python scripts\\verify_presentmon.py")
        block = text[call:text.index("\n", text.index("throw", call))]
        assert "$LASTEXITCODE" in block and "throw" in block

    def test_resource_dir_is_added_to_the_bundle(self):
        """``--add-data`` 的源与目标都必须等于产品那边声明的相对目录。"""
        rel = "/".join(game_fps.BACKEND_RELATIVE_DIR)
        win = rel.replace("/", "\\")
        expected = '--add-data "' + win + ";" + win + '"'
        assert expected in _build_text(), f"没有把 {rel} 打进包：期望 {expected}"

    def test_bundle_is_verified_after_the_build(self):
        """源目录那一份验过不等于产物里有：--add-data 在源缺失时不报错。"""
        text = _build_text()
        assert text.count("verify_presentmon.py") == 2, "源码侧 + 产物侧，两道"
        assert "--path" in text
        assert "_internal\\resources\\presentmon" in text
        # 文件名也不许在脚本里写第二份字面量（换版本只需改产品常量）
        assert game_fps.BACKEND_EXE_NAME not in text
        assert "game_fps.BACKEND_EXE_NAME" in text


class TestOtherPlatformsDoNotBundleIt:
    """macOS / Linux 不加载也不捆这个 Windows 后端（非 Windows 整段按不支持呈现）。"""

    @pytest.mark.parametrize("name", sorted(SHELL_SCRIPTS))
    def test_shell_builds_never_mention_the_backend(self, name):
        text = SHELL_SCRIPTS[name].read_text(encoding="utf-8")
        assert "presentmon" not in text.lower()
        assert "PresentMon" not in text

    def test_ci_builds_go_through_the_gated_script(self):
        """CI 的 Windows 构建走 ``build_onedir.ps1``，因此上面两道门对 CI 同样生效。"""
        workflow = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(
            encoding="utf-8")
        assert "build_onedir.ps1" in workflow


# --------------------------------------------------------------------------- 四、冻结布局契约


class TestFrozenLayoutContract:
    """``--add-data resources\\presentmon;resources\\presentmon`` ↔ 运行期解析路径。

    构建脚本只知道 `--add-data` 的目标串，运行期只知道 ``BACKEND_RELATIVE_DIR``；
    两者不一致时包是"打进去了但找不到"——这一段用 ``sys._MEIPASS`` 假布局把契约钉住。
    """

    def test_runtime_path_is_meipass_relative(self, tmp_path, monkeypatch):
        relative = Path(*game_fps.BACKEND_RELATIVE_DIR)
        target = tmp_path / relative
        target.mkdir(parents=True)
        bundled = target / game_fps.BACKEND_EXE_NAME
        bundled.write_bytes(b"stub")  # 路径契约与内容无关
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        assert game_fps.bundled_backend_path() == bundled
        assert game_fps.backend_problem() == "", "冻结布局里那一份应被判为可用"
        state, reason = game_fps.backend_state()
        if sys.platform == "win32":
            assert (state, reason) == ("", "")
        else:
            # 非 Windows：平台不支持优先于"文件在不在"（这一段整段不占位）
            assert state == "unsupported" and reason

    def test_source_tree_layout_is_the_same_relative_dir(self, tmp_path, monkeypatch):
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        resolved = game_fps.bundled_backend_path()
        assert resolved.parent.name == game_fps.BACKEND_RELATIVE_DIR[-1]
        assert resolved.parent.parent.name == game_fps.BACKEND_RELATIVE_DIR[-2]
        assert resolved.parent.parent.parent == ROOT


# --------------------------------------------------------------------------- 五、文档与许可登记


class TestDocumentationIsRegistered:
    def test_packaging_doc_describes_the_backend_and_the_gates(self):
        text = PACKAGING_DOC.read_text(encoding="utf-8")
        assert "resources/presentmon" in text or "resources\\presentmon" in text
        assert "verify_presentmon" in text
        assert "x86-64" in text

    def test_notices_register_the_backend(self):
        text = NOTICES.read_text(encoding="utf-8")
        assert "PresentMon" in text
        assert game_fps.BACKEND_EXE_NAME in text
        assert game_fps.REQUIRED_SHA256 in text
        assert "resources/presentmon" in text

    def test_gitignore_documents_the_intentionally_committed_binary(self):
        """二进制是刻意入库的：不加说明会被当成误提交（同 assets/icon.ico 的处置）。"""
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "resources/presentmon" in text
        assert "THIRD_PARTY_NOTICES" in text
