"""The optional edition keeps its profile and cannot replace native autostart."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("edition", [False, True])
def test_launch_commands_preserve_edition_and_variant_identity(edition):
    environment = dict(os.environ)
    environment.pop("DSH_CODEX_COMPANION", None)
    environment.pop("DSH_CODEX_PROFILE", None)
    if edition:
        environment.update(DSH_CODEX_COMPANION="1", DSH_CODEX_PROFILE="/example/Fish profile")
    code = """
import json
from pet import autostart as module
print(json.dumps({'value':module.VALUE_NAME,'native':module.APP_DIR_NAME,
 'mac':module._mac_program_args(),'linux':module._linux_desktop_content(),
 'windows':module._win_command()}))
"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["value"] == value["native"] + ("-codex" if edition else "")
    if edition:
        assert "--codex-companion" in value["mac"]
        assert value["mac"][value["mac"].index("--profile") + 1] == "/example/Fish profile"
        assert "--codex-companion" in value["linux"] and "Fish profile" in value["linux"]
        assert "--codex-companion" in value["windows"] and "Fish profile" in value["windows"]
    else:
        assert value["mac"][-2:] == ["--slot", "0"]
        assert "--codex-companion" not in value["linux"] + value["windows"]
