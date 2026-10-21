# -*- coding: utf-8 -*-
"""预热优先级分类（MovieLibrary._priority_names）回归。"""
from __future__ import annotations


def test_priority_names_use_loaded_manifest(tmp_path):
    """预热分类必须与运行分类同一 manifest 口径（window.py 运行侧传 manifest，
    library._priority_names 此前显式传 None）：外部角色包用 manifest 声明分类时，
    预热不得按无 manifest 分叉——否则点击动画不进 pinned 高优，首次点击同步
    ffmpeg 解码卡顿（pinned 机制要消灭的正是这条路径）。"""
    from pet.library import MovieLibrary

    lib = MovieLibrary.__new__(MovieLibrary)
    lib._asset_dir = tmp_path
    lib._manifest = {"aaa": {}, "bbb": {}}
    lib.manifest = {"clicks": ["aaa"], "idle": "bbb"}
    lib.folder_map = {}
    lib.folder_files = None
    # names() 的"可播放"口径（终审 B1 后 = _build_priority_names 的输入）需要
    # 这两张表：帧序列已采纳或有源文件才算可播。本用例走传统素材树 → _paths 有即可。
    lib._frameseq_dirs = {}
    lib._paths = {"aaa": tmp_path / "aaa.webm", "bbb": tmp_path / "bbb.webm"}
    high, low = lib._priority_names()
    assert "aaa" in high, "manifest 声明的点击动画必须进高优预热"
    assert "bbb" in low
