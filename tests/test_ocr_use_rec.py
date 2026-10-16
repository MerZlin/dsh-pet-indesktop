# -*- coding: utf-8 -*-
'''use_rec 开关（只检测档）的测试。

覆盖三层：worker 端（命令行/参数/结果整形）、宿主端（解析与 keep_empty_text）、
避字功能（只在"不需要文字内容"时才走这一档）。
'''
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtGui import QImage

from pet import text_avoid
from pet.screen_capture import ocr, ocr_worker


# ------------------------------------------------------------------ 常量与命令行
def test_flag_literals_agree():
    """两处字面量必须一致（worker 刻意不 import ocr.py，避免把 Qt 拉进识别进程）。"""
    assert ocr.OCR_NO_REC_FLAG == ocr_worker.NO_REC_FLAG


def test_worker_command_appends_the_flag_only_when_recognition_is_off():
    assert ocr.OCR_NO_REC_FLAG not in ocr.worker_command()
    assert ocr.worker_command(use_rec=True) == ocr.worker_command()
    argv = ocr.worker_command(use_rec=False)
    assert argv[-1] == ocr.OCR_NO_REC_FLAG
    assert argv[:-1] == ocr.worker_command()


def test_worker_cli_parses_the_flag(monkeypatch):
    """`main()` 认这个开关；冻结态多出来的 `--ocr-worker` 一律忽略。"""
    seen: list[bool] = []
    monkeypatch.setattr(ocr_worker, "reserve_stdout", lambda: 1)
    monkeypatch.setattr(ocr_worker, "write_result", lambda fd, blocks: None)
    monkeypatch.setattr(ocr_worker, "stdin_stream", lambda: None)
    monkeypatch.setattr(ocr_worker, "run",
                        lambda stdin, use_rec=True: (seen.append(use_rec), (0, []))[1])

    ocr_worker.main(["--ocr-worker", ocr_worker.NO_REC_FLAG])
    ocr_worker.main(["--ocr-worker"])
    ocr_worker.main([])
    assert seen == [False, True, True]


# ------------------------------------------------------------------ worker 端整形
def test_to_blocks_keeps_boxes_when_recognition_is_off():
    """只检测：没有文字也必须把框发出去，且**不能用 ndarray 的真值判断**。

    这条是真机抓出来的 bug 的回归守卫：rapidocr 的 `.boxes` 是 ndarray，
    多元素数组做 `if not boxes:` 会抛 "truth value of an array ... is ambiguous"。
    """
    boxes = np.array([[[0.0, 10.0], [100.0, 12.0], [101.0, 40.0], [1.0, 38.0]],
                      [[200.0, 10.0], [300.0, 12.0], [301.0, 40.0], [201.0, 38.0]]],
                     dtype=np.float32)
    out = ocr_worker.to_blocks(SimpleNamespace(boxes=boxes), use_rec=False)
    assert len(out) == 2
    assert all(block["text"] == "" for block in out)
    assert out[0]["box"][0] == [0.0, 10.0]
    # 空数组 / 没有 boxes 属性 → 空列表（不抛）
    assert ocr_worker.to_blocks(SimpleNamespace(boxes=np.zeros((0, 4, 2))),
                                use_rec=False) == []
    assert ocr_worker.to_blocks(SimpleNamespace(), use_rec=False) == []


def test_to_blocks_still_requires_text_when_recognition_is_on():
    assert ocr_worker.to_blocks(SimpleNamespace(txts=None, boxes=None), use_rec=True) == []


def test_load_engine_disables_recognition_through_params(monkeypatch):
    """只检测档必须**通过 rapidocr 的参数**关掉识别，而且仍然注入全部 session。"""
    captured: dict = {}

    class _FakeRapid:
        def __init__(self, params=None):
            captured["params"] = dict(params or {})

    monkeypatch.setattr(ocr_worker, "model_problem", lambda directory: None)
    monkeypatch.setattr(ocr_worker, "resolve_model_dir", lambda: __import__("pathlib").Path("."))
    monkeypatch.setattr(ocr_worker, "_new_session", lambda path, threads: object())
    monkeypatch.setattr(ocr_worker, "_attach_sessions", lambda engine, sessions: None)
    monkeypatch.setattr(ocr_worker, "emit", lambda record: None)
    import rapidocr
    monkeypatch.setattr(rapidocr, "RapidOCR", _FakeRapid)

    ocr_worker.load_engine(use_rec=False)
    assert captured["params"].get("Global.use_rec") is False
    ocr_worker.load_engine(use_rec=True)
    assert "Global.use_rec" not in captured["params"]


# ------------------------------------------------------------------ 宿主端解析
def _blocks_payload(*texts: str) -> list[dict]:
    box = [[0.0, 10.0], [100.0, 12.0], [101.0, 40.0], [1.0, 38.0]]
    return [{"text": text, "score": 0.9, "box": box} for text in texts]


def test_blocks_from_payload_drops_empty_text_unless_asked():
    raw = _blocks_payload("水", "", "山")
    assert [b.text for b in ocr.blocks_from_payload(raw)] == ["水", "山"]
    kept = ocr.blocks_from_payload(raw, keep_empty_text=True)
    assert [b.text for b in kept] == ["水", "", "山"]


def test_recognize_passes_keep_empty_text_through(monkeypatch):
    """引擎这一层要把开关透到解析处（用假 spawn + 假交换，不起真进程）。"""
    qimg = QImage(4, 4, QImage.Format.Format_RGB888)
    qimg.fill(0)
    stdout = json.dumps({"blocks": _blocks_payload("")}).encode()
    monkeypatch.setattr(ocr.OcrEngine, "_exchange", lambda self, process, frame: (stdout, b""))
    monkeypatch.setattr(ocr, "_register_worker", lambda process: None)
    monkeypatch.setattr(ocr, "_unregister_worker", lambda process: None)

    # spawn 是构造函数缝（实例属性），不是类属性
    engine = ocr.OcrEngine(spawn=lambda argv: SimpleNamespace(returncode=0))
    assert engine.recognize(qimg) == []
    kept = engine.recognize(qimg, keep_empty_text=True)
    assert len(kept) == 1 and kept[0].text == ""


# ------------------------------------------------------------------ 避字功能接线
def test_text_avoid_only_goes_detection_only_when_text_is_not_needed(monkeypatch):
    img = __import__("PIL.Image", fromlist=["Image"]).new("RGB", (64, 64))
    calls: list[dict] = []

    class _Engine:
        def recognize(self, qimg, keep_empty_text=False):
            calls.append({"keep_empty_text": keep_empty_text})
            return []

    seen_use_rec: list[bool] = []

    def fake_engine(use_rec=True):
        seen_use_rec.append(use_rec)
        return _Engine()

    monkeypatch.setattr(text_avoid, "_ocr_engine", fake_engine)
    text_avoid.read_words_from_image(img, (0.0, 0.0), need_text=True)
    text_avoid.read_words_from_image(img, (0.0, 0.0), need_text=False)
    assert seen_use_rec == [True, False], "需要文字内容时才跑识别模型"
    assert [c["keep_empty_text"] for c in calls] == [False, True], \
        "只检测档必须让宿主保留空块，否则整张图会被当成没有字"


def test_empty_text_boxes_are_kept_as_word_boxes_when_text_is_not_needed(monkeypatch):
    """只检测档：空文字的块也要变成词框（cjk_only=False 正是"见到字就避"）。"""
    from pet.screen_capture.ocr import TextBlock
    img = __import__("PIL.Image", fromlist=["Image"]).new("RGB", (64, 64))

    class _Engine:
        def recognize(self, qimg, keep_empty_text=False):
            return [TextBlock("", 0.0, ((10.0, 10.0), (30.0, 10.0),
                                        (30.0, 20.0), (10.0, 20.0)))]

    monkeypatch.setattr(text_avoid, "_ocr_engine", lambda use_rec=True: _Engine())
    words = text_avoid.read_words_from_image(img, (100.0, 200.0), need_text=False)
    assert len(words) == 1
    assert words[0].text == ""
    assert (words[0].left, words[0].top, words[0].right, words[0].bottom) == \
        (110.0, 210.0, 130.0, 220.0)


@pytest.mark.parametrize("cjk_only,expected", [(True, True), (False, False)])
def test_controller_pairs_need_text_with_cjk_only(monkeypatch, cjk_only, expected):
    """`need_text` 必须与 `cjk_only` 配对：只避汉字时绝不能走"没有文字"的那一档。"""
    from PySide6.QtWidgets import QApplication

    from pet.text_avoid_controller import TextAvoidController

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        "pet.text_avoid_controller.platform_win.window_physical_rect",
        lambda hwnd: (0, 0, 1000, 1000))

    from PySide6.QtCore import QObject

    class _Host(QObject):
        cfg = {"text_avoid": {"enabled": True, "cjk_only": cjk_only}}

        def __init__(self):
            super().__init__(None)          # 控制器把宿主当 QObject 父对象

        def _virtual_pos(self):
            from PySide6.QtCore import QPointF
            return QPointF(100, 100)

        def _stable_body_local_rect(self):
            from PySide6.QtCore import QRect
            return QRect(0, 0, 40, 40)

        def devicePixelRatioF(self):
            return 1.0

        def winId(self):
            return 1

        def _screen_available(self):
            return SimpleNamespace(availableGeometry=lambda: __import__(
                "PySide6.QtCore", fromlist=["QRect"]).QRect(0, 0, 1000, 1000))

        def _move_space(self):
            return SimpleNamespace(cx=120.0, left=0.0, right=1000.0, avail=None)

    ctrl = TextAvoidController(_Host())
    seen: list[bool] = []
    monkeypatch.setattr(text_avoid, "grab_screen",
                        lambda: __import__("PIL.Image", fromlist=["Image"]).new("RGB", (64, 64)))
    monkeypatch.setattr(text_avoid, "fingerprint", lambda img, ignore_rect=None: b"x")
    monkeypatch.setattr(text_avoid, "crop_region", lambda img, band: img)
    monkeypatch.setattr(text_avoid, "mask_boxes", lambda words, footprint: words)
    monkeypatch.setattr(text_avoid, "read_under_pet",
                        lambda footprint, need_text=True: ())
    monkeypatch.setattr(
        text_avoid, "read_words_from_image",
        lambda img, origin, need_text=True: (seen.append(need_text), ())[1])
    ctrl._last_fingerprint = None
    ctrl._inflight = True
    ctrl._refresh_once((0, 0, 64, 64), (0.0, 0.0, 40.0, 40.0))
    assert seen == [expected]
