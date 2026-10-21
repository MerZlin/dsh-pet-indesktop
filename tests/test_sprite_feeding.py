# -*- coding: utf-8 -*-
"""SpriteFeedingController offscreen 单测（4.1c 投喂）。

覆盖：命中 sprite 才 accept（未命中/无 URL ignore）、统计文件同格式落盘、
吃动画经 play_once 一次性播放、气泡接缝静默、dragMove 同判定。
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

import tests.test_sprite_menu_facade as fac
from pet.sprite_feeding import SpriteFeedingController

app = QApplication.instance() or QApplication([])


def _drop_event(pos: QPointF, paths: list[str]):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    event = QDropEvent(pos, Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    event._mime_ref = mime  # QDropEvent 不持有 mime 所有权：Python 侧保活
    return event


def _drag_enter(pos: QPointF, paths: list[str]):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    event = QDragEnterEvent(pos.toPoint(), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    event._mime_ref = mime
    return event


def _make(tmp_path, config_values=None):
    shell, lib = fac._make_shell(tmp_path, config_values)
    # 生产里首帧上屏后命中图才存在；测试同步重建一帧（等同首个 paint）
    shell.sprite.bind_clip("idle1")
    shell.sprite._rebuild_pixmap()
    ctrl = SpriteFeedingController(shell.overlay, shell.sprite,
                                   shell._config, shell.behavior)
    return shell, lib, ctrl


def _spawn_hittable_child(shell, pos: QPointF):
    """生一只可逐像素命中的子宠：走真 ``spawn_pet``，再给它独立素材库 +
    spawn 后缺失的首帧命中图（口径同 ``_make`` 的主宠）。"""
    shell.spawn_pet()
    child = shell._spawned[-1]
    child_lib = fac.RichLibrary()
    child.library = child_lib
    child.bind_clip("idle1")
    child._rebuild_pixmap()
    child.set_pos(pos)
    return child, child_lib


def _stub_say(shell, sprite):
    """把某只 sprite 的跟随器 ``say`` 换成记录器，返回记录列表。"""
    said: list = []
    follower = shell._bubble_for(sprite)
    follower.say = lambda text, **kw: said.append((text, kw)) or True
    return said


def test_accept_only_over_sprite(tmp_path):
    shell, _lib, ctrl = _make(tmp_path)
    try:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        over = QPointF(shell.sprite.rect().center())
        away = QPointF(10, 10)  # sprite 在右下角，(10,10) 必空
        e1 = _drag_enter(over, [str(tmp_path / "a.txt")])
        ctrl.handle_drag_enter(e1)
        assert e1.isAccepted()
        e2 = _drag_enter(away, [str(tmp_path / "a.txt")])
        ctrl.handle_drag_enter(e2)
        assert not e2.isAccepted()
        e3 = _drag_enter(over, [])          # 无 URL
        ctrl.handle_drag_enter(e3)
        assert not e3.isAccepted()
    finally:
        shell._delete_runtime_marker()


def test_drop_records_stats_and_plays_eat_anim(tmp_path):
    shell, lib, ctrl = _make(tmp_path)
    try:
        f1 = tmp_path / "snack.txt"
        f1.write_text("yummy", encoding="utf-8")
        bubbles = []
        ctrl._bubble_cb = lambda result, sprite: bubbles.append(
            (result["files"], result["folders"], result["bytes"], sprite))
        event = _drop_event(QPointF(shell.sprite.rect().center()), [str(f1)])
        result = ctrl.handle_drop(event)
        assert event.isAccepted()
        assert result["files"] == 1 and result["bytes"] == 5
        # 统计文件同格式落盘
        stats = json.loads((tmp_path / "file_eaten_stats.json").read_text(encoding="utf-8"))
        assert stats["feed_count"] == 1
        assert stats["file_count"] == 1
        assert stats["total_bytes"] == 5
        # 吃动画一次性播放（acts 池的 act1 被选中）
        assert shell.sprite._clip_name == "act1"
        assert lib._clips["act1"].started is True
        assert bubbles == [(1, 0, 5, shell.sprite)]
    finally:
        shell._delete_runtime_marker()


def test_drop_ignored_away_from_sprite(tmp_path):
    shell, _lib, ctrl = _make(tmp_path)
    try:
        f1 = tmp_path / "snack.txt"
        f1.write_text("yummy", encoding="utf-8")
        event = _drop_event(QPointF(10, 10), [str(f1)])
        assert ctrl.handle_drop(event) is None
        assert not event.isAccepted()
        assert not (tmp_path / "file_eaten_stats.json").exists()
    finally:
        shell._delete_runtime_marker()


def test_feeding_bubble_text_format(tmp_path):
    """投喂气泡文案（file_eater._show_feedback 同格式）经 shell 回调落到 follower。"""
    shell, _lib, _ctrl = _make(tmp_path)
    try:
        said = []
        shell._bubble_follower.say = lambda text, **kw: said.append((text, kw)) or True
        f1 = tmp_path / "snack.txt"
        f1.write_text("yummy", encoding="utf-8")
        ctrl = shell._feeding
        event = _drop_event(QPointF(shell.sprite.rect().center()), [str(f1)])
        ctrl.handle_drop(event)
        assert len(said) == 1
        text, kw = said[0]
        assert "啊呜～吃掉 1 个文件" in text
        assert "累计吃掉 1 个文件" in text
        assert kw["subtitle"] == "放心，只是做个样子，文件没有删除或移动哦"
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 解读询问接缝
def test_eat_paths_calls_interpret_offer(tmp_path):
    """吃完后必须询问解读（旧 ``file_eater.eat_paths:183-186`` 的接缝）。"""
    shell, _lib, ctrl = _make(tmp_path)
    try:
        offered: list = []
        ctrl.interpret_offer = offered.append
        f1 = tmp_path / "a.txt"
        f1.write_text("x", encoding="utf-8")
        event = _drop_event(QPointF(shell.sprite.rect().center()), [str(f1)])
        ctrl.handle_drop(event)
        assert len(offered) == 1, "投喂后必须把原始路径交给解读控制器"
        assert [Path(p) for p in offered[0]] == [f1]
    finally:
        shell._delete_runtime_marker()


def test_eat_paths_without_offer_is_silent(tmp_path):
    """未注入解读控制器（无聊天变体/未接线）：投喂照旧，不抛。"""
    shell, _lib, ctrl = _make(tmp_path)
    try:
        assert ctrl.interpret_offer is None
        f1 = tmp_path / "a.txt"
        f1.write_text("x", encoding="utf-8")
        event = _drop_event(QPointF(shell.sprite.rect().center()), [str(f1)])
        assert ctrl.handle_drop(event) is not None
    finally:
        shell._delete_runtime_marker()


def test_drop_on_child_pet_eats_and_bubbles_on_child(tmp_path):
    """B02：拖到子宠 → 吃动画与气泡都落子宠，主宠一点不牵动。

    旧行为：``handle_drop`` 把 ``sprite_at`` 的命中结果丢掉，吃动画固定打在主宠
    身上、气泡固定走主宠跟随器（拖子宠喂出来的是主宠吃 + 主宠弹统计）。
    """
    shell, main_lib, _ctrl = _make(tmp_path)
    try:
        child, child_lib = _spawn_hittable_child(shell, QPointF(300, 300))
        child_center = QPointF(child.rect().center())
        assert shell.overlay.sprite_at(child_center) is child, "前提：落点命中子宠"
        main_said = _stub_say(shell, shell.sprite)
        child_said = _stub_say(shell, child)

        f1 = tmp_path / "snack.txt"
        f1.write_text("yummy", encoding="utf-8")
        result = shell._feeding.handle_drop(_drop_event(child_center, [str(f1)]))

        assert result["files"] == 1
        assert child_lib._clips["act1"].started is True, "吃动画必须落在被投喂的子宠身上"
        assert child._clip_name == "act1"
        assert main_lib._clips["act1"].started is False, "主宠不许替子宠吃"
        assert shell.sprite._clip_name != "act1"
        assert len(child_said) == 1, "气泡必须走被投喂那只自己的跟随器"
        assert not main_said
        assert "啊呜～吃掉 1 个文件" in child_said[0][0]
        assert child_said[0][1]["subtitle"] == "放心，只是做个样子，文件没有删除或移动哦"
    finally:
        shell.clear_spawned_pets()
        shell._delete_runtime_marker()


def test_overlay_drop_event_routes_to_hit_sprite(tmp_path):
    """端到端接线：壳的原生 ``dropEvent`` → 控制器 → 子宠吃 + 子宠气泡。"""
    shell, main_lib, _ctrl = _make(tmp_path)
    try:
        child, child_lib = _spawn_hittable_child(shell, QPointF(300, 300))
        main_said = _stub_say(shell, shell.sprite)
        child_said = _stub_say(shell, child)

        f1 = tmp_path / "snack.txt"
        f1.write_text("yummy", encoding="utf-8")
        event = _drop_event(QPointF(child.rect().center()), [str(f1)])
        shell.overlay.dropEvent(event)

        assert event.isAccepted()
        assert child_lib._clips["act1"].started is True
        assert main_lib._clips["act1"].started is False
        assert len(child_said) == 1 and not main_said
    finally:
        shell.clear_spawned_pets()
        shell._delete_runtime_marker()


def test_drop_on_main_pet_keeps_main_channel(tmp_path):
    """落点在主宠：口径不变（主宠吃、主宠气泡，子宠不冒泡）。"""
    shell, main_lib, _ctrl = _make(tmp_path)
    try:
        child, child_lib = _spawn_hittable_child(shell, QPointF(300, 300))
        main_center = QPointF(shell.sprite.rect().center())
        assert shell.overlay.sprite_at(main_center) is shell.sprite, "前提：落点命中主宠"
        main_said = _stub_say(shell, shell.sprite)
        child_said = _stub_say(shell, child)

        f1 = tmp_path / "snack.txt"
        f1.write_text("yummy", encoding="utf-8")
        result = shell._feeding.handle_drop(_drop_event(main_center, [str(f1)]))

        assert result["files"] == 1
        assert main_lib._clips["act1"].started is True
        assert child_lib._clips["act1"].started is False
        assert len(main_said) == 1
        assert not child_said
    finally:
        shell.clear_spawned_pets()
        shell._delete_runtime_marker()


def test_drop_truncated_dir_reports_budget_in_result_and_bubble(tmp_path, monkeypatch):
    """B03：overlay 链同样走带预算的扫描——截断标记进结果，气泡如实提示。

    旧行为：drop 事件里同步无上限递归 + 逐文件 ``stat()``，大目录/网络盘把整个
    桌宠 UI 冻在 GUI 线程上。
    """
    import pet.file_interpret.eater as file_eater

    monkeypatch.setattr(file_eater, "SCAN_FILE_LIMIT", 3)
    shell, _lib, _ctrl = _make(tmp_path)
    try:
        folder = tmp_path / "big-dir"
        folder.mkdir()
        for i in range(6):
            (folder / f"f{i}.txt").write_text("x" * 10, encoding="utf-8")
        said = _stub_say(shell, shell.sprite)

        result = shell._feeding.eat_paths([str(folder)])

        assert result["truncated"] is True
        assert result["scanned"] == 3, "触文件数预算即停扫，计数停在预算内"
        assert result["folders"] == 1 and result["bytes"] == 30
        stats = json.loads((tmp_path / "file_eaten_stats.json").read_text(encoding="utf-8"))
        assert stats["total_bytes"] == 30, "统计只记已统计的部分，格式不变"
        assert said and "只统计了前 3 项" in said[0][0], "气泡必须如实提示截断"
    finally:
        shell._delete_runtime_marker()


def test_small_drop_is_not_marked_truncated(tmp_path):
    """小目录/散文件不触预算：结果形状不变且截断标记为假。"""
    shell, _lib, _ctrl = _make(tmp_path)
    try:
        folder = tmp_path / "small-dir"
        folder.mkdir()
        (folder / "a.txt").write_text("abc", encoding="utf-8")
        f2 = tmp_path / "b.txt"
        f2.write_text("de", encoding="utf-8")
        said = _stub_say(shell, shell.sprite)

        result = shell._feeding.eat_paths([str(folder), str(f2)])

        assert result["truncated"] is False
        assert result["scanned"] == 2
        assert (result["files"], result["folders"], result["bytes"]) == (1, 1, 5)
        assert said and "只统计了前" not in said[0][0]
    finally:
        shell._delete_runtime_marker()


def test_shell_file_interpret_offer_seam_survives_rebind(tmp_path):
    """壳侧注入接缝：``set_file_interpret_offer`` 落到控制器，重挂不丢。

    壳在「主宠提升 / 屏迁移」时都会 ``_bind_feeding()`` 重建控制器——接缝必须
    由壳持有并在重挂时回填，否则拖文件解读在那些路径之后静默失效。
    """
    shell, _lib, _ctrl = _make(tmp_path)
    try:
        offered: list = []
        shell.set_file_interpret_offer(offered.append)
        assert shell._feeding.interpret_offer is not None

        shell._bind_feeding()          # 主宠提升/屏迁移的重挂路径
        assert shell._feeding.interpret_offer is not None, "重挂后接缝必须还在"

        f1 = tmp_path / "a.txt"
        f1.write_text("x", encoding="utf-8")
        event = _drop_event(QPointF(shell.sprite.rect().center()), [str(f1)])
        shell._feeding.handle_drop(event)
        assert len(offered) == 1
        assert [Path(p) for p in offered[0]] == [f1]
    finally:
        shell._delete_runtime_marker()
