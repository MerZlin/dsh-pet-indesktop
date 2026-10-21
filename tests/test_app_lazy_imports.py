# -*- coding: utf-8 -*-
"""内存瘦身第二刀的机器化守卫：``pet.app`` 的导入图不拖进按配置关闭的功能模块。

实测依据（tools/import_cost.py，每个模块一个全新子进程的 tracemalloc 增量）：

    MB      module
  4.75  pet.island.balance     （档位文案/价格提示表；默认配置从不查余额）
  2.95  pet.festival.service   （连带 festival/calendar/data
  ＋五份节日文案表；总开关默认关闭）

合计 ~7.7MB Python 堆在启动时白白常驻。两个 import 已收到真正的构造/使用点
（``AppShell._ensure_festival_service`` / ``_show_balance_payload`` /
``_island_tier_hint`` / ``_balance_worker``），本测试用**子进程**验证「只
import pet.app 不会带出它们」——进程内断言不可靠，别的用例可能已经导入过。

刻意不拦的模块（有实测理由，见 PR 报告「未采纳方向」）：
``pet.window``（被 platform_win 模块级导入，overlay 拓扑也绕不开）、
``pet.voice.chime_service``（真实配置下 click 自言自语朗读开着，
``_chime_wanted()`` 为真，本来就需要）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_CHILD = r'''
import json, sys
import pet.app  # noqa: F401 - 被测：只导入入口模块，不起 QApplication
deferred = ["pet.island.balance", "pet.festival.service", "pet.festival.data",
            "pet.festival.quotes_cn", "pet.festival.quotes_west_movie"]
print(json.dumps({name: name in sys.modules for name in deferred}))
'''


def _run_child() -> dict:
    env = dict(os.environ)
    env.setdefault('QT_QPA_PLATFORM', 'offscreen')
    proc = subprocess.run(
        [sys.executable, '-c', _CHILD],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f'子进程导入 pet.app 失败:\n{proc.stderr[-3000:]}'
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_importing_app_defers_balance_module():
    loaded = _run_child()
    assert loaded['pet.island.balance'] is False, (
        'pet.island.balance（4.75MB 档位文案表）不得在 import pet.app 时常驻：'
        '默认配置不查余额，它必须留到 _show_balance_payload/_island_tier_hint'
    )


def test_importing_app_defers_festival_modules():
    loaded = _run_child()
    offenders = [name for name in (
        'pet.festival.service', 'pet.festival.data',
        'pet.festival.quotes_cn', 'pet.festival.quotes_west_movie',
    ) if loaded[name]]
    assert not offenders, (
        f'节日提醒总开关默认关闭，这些模块不该在 import pet.app 时载入: {offenders}'
    )
