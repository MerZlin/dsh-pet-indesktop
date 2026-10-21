"""macOS 设置子进程从启动起不注册 Dock 图标。"""
import plistlib
from pathlib import Path
import subprocess
import sys


def test_macos_bundle_starts_as_agent_before_optional_main_dock_icon(tmp_path):
    script = Path("scripts/build_macos.sh").read_text(encoding="utf-8")
    marker = '<<\'MACOS_PLIST\'\n'
    assert marker in script, "启动策略必须在签名前写入正式构建产物"
    code = script.split(marker, 1)[1].split("\nMACOS_PLIST", 1)[0]
    assert script.index(marker) < script.index("codesign --force")
    path = tmp_path / "Info.plist"
    path.write_bytes(plistlib.dumps({"CFBundleName": "pet", "OtherKey": [1, 2]}))
    subprocess.run([sys.executable, "-", str(path)], input=code, text=True, check=True)
    assert plistlib.loads(path.read_bytes()) == {
        "CFBundleName": "pet", "OtherKey": [1, 2], "LSUIElement": True,
    }
