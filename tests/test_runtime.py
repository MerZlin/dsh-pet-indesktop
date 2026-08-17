# -*- coding: utf-8 -*-
"""目录/常量完整性测试（无需 GUI）。"""

from pet import catalog


def test_catalog_integrity():
    assert len(catalog.ANIM_FILES) == 51
    assert len(catalog.ACTS) == 42
    assert len(catalog.CLICKS) == 3
    assert len(catalog.MOVES) == 3
    assert catalog.IDLE in catalog.ANIM_FILES
    assert catalog.TURN in catalog.ANIM_FILES
    assert catalog.DRAG in catalog.ANIM_FILES
    assert all(n in catalog.ANIM_FILES for n in catalog.CLICKS + catalog.MOVES)
    assert catalog.FRAME_MS > 0