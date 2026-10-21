# -*- coding: utf-8 -*-
"""#202 模块级 assert 当运行期校验：``-O`` 下校验必须仍然生效。

``python -O`` / ``PYTHONOPTIMIZE=1`` 会把模块级 ``assert`` 整句删除，被删掉的
不是调试断言，而是两条**运行期底线**：

- ``pet/catalog.py``：动画总数 / 动作池条数是硬编码素材契约（ANIM_FILES 是字面
  量字典）。数字漂移意味着切换动作会取到不存在的素材，表现为"点她没反应"；
- ``pet/festival/data.py``：节日 id 重复会让后一个节日在 ``FESTIVALS_BY_ID``
  里被静默覆盖，文案库/槽位按 id 反查永远查不到它——节日悄悄消失。

本文件用**真 ``-O`` 子进程**验证"优化模式下同样抛异常"（而不是只验证源码里
写了 if/raise），并用 AST 静态门钉住这两个模块不得再出现模块级 assert。
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WAIT = 60.0

#: 子进程探针前缀：显式口径的自检（**不能**用 assert——-O 下它自己就被删了）。
_PREFIX = """import sys
if sys.flags.optimize != 1:
    print('OPT-OFF', sys.flags.optimize)
    sys.exit(2)
sys.path.insert(0, {root!r})
"""

_CATALOG_PROBE = """
import pet.catalog as catalog
try:
    catalog.ANIM_FILES['__probe__'] = '__probe__.webm'
    catalog._verify_animation_pools()
except RuntimeError as exc:
    print('RAISED', exc)
else:
    print('NO-RAISE')
"""

_FESTIVAL_PROBE = """
import pet.festival.data as fd
try:
    fd._index_festivals(fd.FESTIVALS + (fd.FESTIVALS[0],))
except ValueError as exc:
    print('RAISED', exc)
else:
    print('NO-RAISE')
"""


def _run_optimized(probe: str) -> str:
    """在 ``python -O`` 子进程里跑探针，返回 stdout。"""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.run(
        [sys.executable, "-O", "-c", _PREFIX.format(root=str(ROOT)) + probe],
        capture_output=True, text=True, timeout=WAIT, env=env,
    )
    assert proc.returncode == 0, \
        f"探针异常退出 {proc.returncode}（2 = -O 未生效）: {proc.stderr[:800]}"
    return proc.stdout


# ------------------------------------------------------------------ catalog
def test_catalog_animation_count_guard_survives_optimize():
    """-O 下动画总数漂移必须照样抛异常（旧实现：assert 被整句删除）。"""
    stdout = _run_optimized(_CATALOG_PROBE)
    assert "RAISED" in stdout, f"-O 下动画数校验消失（旧 assert 语义）：{stdout!r}"
    assert "51" in stdout and "52" in stdout, f"异常文案必须含期望/实际条数：{stdout!r}"


# ------------------------------------------------------------- festival_data
def test_festival_id_guard_survives_optimize():
    """-O 下节日 id 重复必须照样抛异常（旧实现：assert 被整句删除）。"""
    stdout = _run_optimized(_FESTIVAL_PROBE)
    assert "RAISED" in stdout, f"-O 下节日 id 唯一性校验消失：{stdout!r}"
    assert "重复" in stdout, f"异常必须指出重复原因：{stdout!r}"


def test_festival_index_keeps_every_entry_when_ids_are_unique():
    """正例：全量节日表建索引后条数不变（校验不得把正常数据判成重复）。"""
    import pet.festival.data as fd

    index = fd._index_festivals(fd.FESTIVALS)
    assert len(index) == len(fd.FESTIVALS)
    assert index is not fd.FESTIVALS_BY_ID
    assert set(fd.FESTIVALS_BY_ID) == set(index)


# ------------------------------------------------------------------ 静态门
@pytest.mark.parametrize("rel_path", ["pet/catalog.py", "pet/festival/data.py"])
def test_no_module_level_assert_remains(rel_path):
    """这两个模块不得再出现模块级 assert——`-O` 会把它整句删除。"""
    tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8"))
    offenders = [node.lineno for node in tree.body if isinstance(node, ast.Assert)]
    assert offenders == [], f"{rel_path} 仍有模块级 assert（-O 下静默消失）：行 {offenders}"
