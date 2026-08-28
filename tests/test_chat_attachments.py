# -*- coding: utf-8 -*-
"""聊天附件：上限校验、超限提示与文件选择器过滤。

覆盖：
- validate_attachment_additions 纯函数（附件数量 / 图片单张与总大小 / 文本总长）；
- ChatComposer 在超限时发出 notice 信号并给出明确文案（不再静默跳过）；
- 文件选择器过滤不再宣称支持 PDF（只发文件名的问题所在）。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from pet.chat.widgets import (
    MAX_ATTACHMENTS,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_TOTAL_BYTES,
    MAX_TEXT_TOTAL_CHARS,
    ChatComposer,
    validate_attachment_additions,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _text(tmp_path, name, chars):
    p = tmp_path / name
    p.write_text("x" * chars, encoding="utf-8")
    return p


def _image(tmp_path, name, size):
    p = tmp_path / name
    p.write_bytes(b"\x00" * size)
    return p


# --------------------------------------------------------------------------
# validate_attachment_additions 纯函数测试
# --------------------------------------------------------------------------
class TestAttachmentLimits:
    def test_max_attachments_limit(self, tmp_path):
        files = [_text(tmp_path, f"f{i}.txt", 10) for i in range(MAX_ATTACHMENTS + 2)]
        accepted, warnings = validate_attachment_additions([], [str(f) for f in files])
        assert len(accepted) == MAX_ATTACHMENTS
        assert len(warnings) == 2
        assert any("最多 10 个" in w for w in warnings)

    def test_oversized_single_image_rejected(self, tmp_path):
        big = _image(tmp_path, "big.png", MAX_IMAGE_BYTES + 1)
        accepted, warnings = validate_attachment_additions([], [str(big)])
        assert accepted == []
        assert len(warnings) == 1
        assert "超过 10MB" in warnings[0]

    def test_image_total_limit(self, tmp_path):
        # 每张都低于单张 10MB 上限，但三张合计超过 20MB 总上限
        size = MAX_IMAGE_TOTAL_BYTES // 3 + 1
        assert size < MAX_IMAGE_BYTES
        imgs = [_image(tmp_path, f"i{i}.png", size) for i in range(3)]
        accepted, warnings = validate_attachment_additions([], [str(i) for i in imgs])
        assert len(accepted) == 2
        assert any("图片总大小超过 20MB" in w for w in warnings)

    def test_text_total_limit(self, tmp_path):
        t1 = _text(tmp_path, "a.txt", MAX_TEXT_TOTAL_CHARS // 2 + 1)
        t2 = _text(tmp_path, "b.txt", MAX_TEXT_TOTAL_CHARS // 2 + 1)
        accepted, warnings = validate_attachment_additions([], [str(t1), str(t2)])
        assert len(accepted) == 1
        assert any("文本总长超过 20 万字符" in w for w in warnings)

    def test_duplicate_and_nonfile_ignored_without_warning(self, tmp_path):
        f = _text(tmp_path, "a.txt", 10)
        missing = tmp_path / "missing.txt"
        accepted, warnings = validate_attachment_additions([f], [str(f), str(missing)])
        assert accepted == []
        assert warnings == []

    def test_mixed_within_limits_accepted(self, tmp_path):
        t = _text(tmp_path, "a.txt", 1000)
        img = _image(tmp_path, "a.png", 1000)
        accepted, warnings = validate_attachment_additions([], [str(t), str(img)])
        assert set(accepted) == {t.resolve(), img.resolve()}
        assert warnings == []

    def test_cumulative_limits_with_existing(self, tmp_path):
        existing = _text(tmp_path, "e.txt", MAX_TEXT_TOTAL_CHARS // 2 + 1)
        new = _text(tmp_path, "n.txt", MAX_TEXT_TOTAL_CHARS // 2)
        accepted, warnings = validate_attachment_additions([existing], [str(new)])
        # 已有文本已占用一半以上预算，新增会超出总长上限
        assert accepted == []
        assert any("文本总长超过 20 万字符" in w for w in warnings)


# --------------------------------------------------------------------------
# ChatComposer 集成：超限必须明确提示（notice 信号），不静默跳过
# --------------------------------------------------------------------------
class TestComposerNotice:
    def test_oversized_image_flash_notice(self, tmp_path):
        _app()
        composer = ChatComposer()
        big = _image(tmp_path, "big.png", MAX_IMAGE_BYTES + 1)
        notices = []
        composer.notice.connect(notices.append)
        composer.add_attachments([str(big)])
        assert composer.attachment_paths == []
        assert notices, "超限必须发出 notice 信号"
        assert any("超过 10MB" in n for n in notices)

    def test_max_attachments_notice(self, tmp_path):
        _app()
        composer = ChatComposer()
        first = [_text(tmp_path, f"f{i}.txt", 10) for i in range(MAX_ATTACHMENTS)]
        composer.add_attachments([str(f) for f in first])
        assert len(composer.attachment_paths) == MAX_ATTACHMENTS

        notices = []
        composer.notice.connect(notices.append)
        extra = _text(tmp_path, "extra.txt", 10)
        composer.add_attachments([str(extra)])
        assert len(composer.attachment_paths) == MAX_ATTACHMENTS
        assert any("最多 10 个" in n for n in notices)

    def test_image_payloads_notice_for_oversized(self, tmp_path):
        _app()
        composer = ChatComposer()
        big = _image(tmp_path, "big.jpg", MAX_IMAGE_BYTES + 1)
        # 即使被加入（例如手工塞进 attachment_paths），生成 payload 时也要提示
        composer.attachment_paths = [big]
        notices = []
        composer.notice.connect(notices.append)
        payloads = composer.image_payloads()
        assert payloads == []
        assert any("超过 10MB" in n for n in notices)


class TestAttachmentPromptBudget:
    def test_text_total_capped_in_prompt(self, tmp_path):
        _app()
        composer = ChatComposer()
        t1 = _text(tmp_path, "a.txt", MAX_TEXT_TOTAL_CHARS)
        t2 = _text(tmp_path, "b.txt", MAX_TEXT_TOTAL_CHARS // 2)
        composer.attachment_paths = [t1, t2]
        prompt = composer.attachment_prompt()
        # 全部预算被第一个文件占满，第二个文件内容被截断到 0
        assert "x" * MAX_TEXT_TOTAL_CHARS in prompt
        assert "y" * (MAX_TEXT_TOTAL_CHARS // 2) not in prompt


# --------------------------------------------------------------------------
# 文件选择器过滤不再宣称支持 PDF
# --------------------------------------------------------------------------
class TestAttachmentFilter:
    def test_filter_no_longer_advertises_pdf(self, monkeypatch):
        _app()
        captured = {}

        def fake_get_open_file_names(*args, **kwargs):
            captured["filter"] = args[3] if len(args) > 3 else kwargs.get("filter", "")
            return [], ""

        monkeypatch.setattr(
            "pet.chat.widgets.QFileDialog.getOpenFileNames",
            fake_get_open_file_names,
        )
        composer = ChatComposer()
        composer.choose_attachments()
        assert "*.pdf" not in captured["filter"]
        # 文本与真实图片格式仍保留
        assert "*.txt" in captured["filter"]
        assert "*.png" in captured["filter"]
