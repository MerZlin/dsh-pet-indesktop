# -*- coding: utf-8 -*-
"""语音报时设置页的呈现回归：后端参数折叠、凭据组、文案与默认后端。

机主 2026-10-13 原话：「现在语音界面的各种信息太复杂了，需要整理，写得更人性化和简洁一点」。
四个后端 12 个字段原本全部同时铺开。这一组用例锁四件事：

1. 当前后端的字段平铺在最上面，其余后端**折起来但看得见**（折叠头 + 展开按钮）；
2. 换后端时当前后端排到最前，其余后端回到收起态；
3. 凭据字段单独进「要你自己去拿的凭据」组，界面不再出现接口地址 / 资源号 / 错误码 /
   外部音色表这类技术细节；
4. 默认后端是免凭据的 edge（面向公众发包时开箱即有声）。

行会被「语音」域收进一张平卡（现代设置对话框的域收集机制只搬 ``SettingRow``），
所以折叠必须落在行上——这些用例既看设置页自身，也在真实对话框里看搬家之后的样子。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pet import tts  # noqa: E402
from pet.settings_widgets import SettingRow, SettingsCard  # noqa: E402
from pet.voice_chime import DEFAULT_BACKEND, clean_backend, default_chime_config  # noqa: E402
from pet.voice.chime_settings import VoiceChimeSettingsPage  # noqa: E402
from pet.voice_chime_tts_panel import (  # noqa: E402
    COLLAPSE_TEXT,
    CREDENTIAL_HINT,
    CREDENTIAL_TITLE,
    DISABLED_SUFFIX,
    EXPAND_TEXT,
)

#: 设置页上不许再出现的技术细节（2026-10-13 机主口径：那些只留在 provider 的
#: docstring/注释里，界面不讲课）。
TECHNICAL_MARKERS = (
    "http",
    ".com",
    "www.",
    "seed-tts",
    "uranus",
    "55000000",
    "voices.md",
    "ark-skills",
    "接口",
    "错误码",
    "资源号",
    "uuid",
    "sapi5",
    "ffmpeg",
    "comtypes",
    "keyring",
)

BACKEND_IDS = ("doubao", "edge", "mimo", "sapi")


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _page(tmp_path, backend: str | None = None) -> VoiceChimeSettingsPage:
    from pet.config import Config

    _qapp()
    cfg = Config(base=tmp_path)
    if backend is not None:
        cfg.set("voice_chime_tts_backend", backend)
    return VoiceChimeSettingsPage(cfg)


def _block_card(widget) -> SettingsCard:
    """承载「合成后端」参数块的设置卡（家页与域平卡都是它）。"""
    for card in widget.findChildren(SettingsCard):
        if any(row.objectName() == "settingRow_tts_credentials" for row in card.rows):
            return card
    raise AssertionError("找不到承载后端参数块的设置卡")


def _keys(card: SettingsCard) -> list[str]:
    return [row.objectName().replace("settingRow_", "") for row in card.rows]


def _fold_header(page, provider_id: str) -> SettingRow:
    row = page.findChild(SettingRow, f"settingRow_tts_backend_fold_{provider_id}")
    assert row is not None, f"{provider_id} 的折叠头行必须存在"
    return row


# ============================================================ 折叠


def test_current_backend_fields_are_flat_and_the_rest_are_folded(tmp_path):
    """默认（edge）：它的字段可见，其余后端的行全部收起，但折叠头看得见。"""
    page = _page(tmp_path)
    assert page.backend_select.currentData() == "edge"

    for spec in tts.get("edge").fields:
        assert page._field_rows[("edge", spec.name)].isVisibleTo(page), (
            f"当前后端 edge 的 {spec.name} 必须平铺可见"
        )

    for provider_id in ("doubao", "mimo", "sapi"):
        header = _fold_header(page, provider_id)
        assert header.isVisibleTo(page), "折起来≠藏起来：折叠头必须看得见"
        assert header.label.text() == f"{tts.get(provider_id).label.split('（')[0]}{DISABLED_SUFFIX}"
        assert header.control.text() == EXPAND_TEXT, "默认收起：按钮该写着「展开」"
        for spec in tts.get(provider_id).fields:
            assert not page._field_rows[(provider_id, spec.name)].isVisibleTo(page), (
                f"非当前后端 {provider_id} 的 {spec.name} 必须默认收起"
            )


def test_fold_button_expands_and_collapses_a_backend(tmp_path):
    """点「展开」看得见别人的字段，再点「收起」收回去（字段键/读写不因此改变）。"""
    page = _page(tmp_path)
    header = _fold_header(page, "mimo")
    button = header.control

    button.click()
    assert button.text() == COLLAPSE_TEXT
    for spec in tts.get("mimo").fields:
        assert page._field_rows[("mimo", spec.name)].isVisibleTo(page), (
            f"展开后 {spec.name} 必须可见"
        )

    button.click()
    assert button.text() == EXPAND_TEXT
    for spec in tts.get("mimo").fields:
        assert not page._field_rows[("mimo", spec.name)].isVisibleTo(page)


def test_switching_backend_puts_it_on_top_and_folds_the_others(tmp_path):
    """换后端：它的字段紧跟「失败自动回退」，其余后端折叠且按注册顺序排在后面。"""
    page = _page(tmp_path)
    page.backend_select.setCurrentData("doubao")

    card = _block_card(page)
    keys = _keys(card)
    fallback = keys.index("voice_chime_tts_fallback")
    plain = [spec.key for spec in tts.get("doubao").fields if spec.kind != "secret"]
    secret = [spec.key for spec in tts.get("doubao").fields if spec.kind == "secret"]
    assert keys[fallback + 1] == "tts_backend_fold_doubao", (
        "当前后端的折叠头只该是隐藏着占位，紧跟其后才是它的字段"
    )
    start = fallback + 2
    assert keys[start : start + len(plain)] == plain, "当前后端的字段必须平铺在最上面"
    assert keys[start + len(plain)] == "tts_credentials", "凭据组跟在普通字段之后"
    assert keys[start + len(plain) + 1 : start + len(plain) + 1 + len(secret)] == secret
    assert not _fold_header(page, "doubao").isVisibleTo(page)

    # 其余后端：各自的折叠头紧挨自己的字段，且顺序不随切换乱掉
    for provider_id in ("edge", "mimo", "sapi"):
        header_at = keys.index(f"tts_backend_fold_{provider_id}")
        first_field = tts.get(provider_id).fields[0].key
        assert keys[header_at + 1] == first_field, f"{provider_id} 的折叠头必须紧挨它的字段"
        assert _fold_header(page, provider_id).isVisibleTo(page)
        for spec in tts.get(provider_id).fields:
            assert not page._field_rows[(provider_id, spec.name)].isVisibleTo(page)

    # 刚展开过的后端换出去之后也要回到收起态（非当前后端一律折起来）
    _fold_header(page, "sapi").control.click()
    assert page._field_rows[("sapi", "rate")].isVisibleTo(page)
    page.backend_select.setCurrentData("mimo")
    assert not page._field_rows[("sapi", "rate")].isVisibleTo(page)
    assert _fold_header(page, "sapi").control.text() == EXPAND_TEXT


def test_dependency_hidden_field_stays_hidden_when_its_backend_is_expanded(tmp_path):
    """字段间依赖（音色设计模型没有内置音色）在展开的非当前后端里同样成立。"""
    page = _page(tmp_path)
    page._field_widgets["voice_chime_mimo_model"].setCurrentData("mimo-v2.5-tts-voicedesign")
    _fold_header(page, "mimo").control.click()

    assert page._field_rows[("mimo", "style")].isVisibleTo(page)
    assert not page._field_rows[("mimo", "voice")].isVisibleTo(page)

    page.backend_select.setCurrentData("mimo")
    assert page._field_rows[("mimo", "style")].isVisibleTo(page)
    assert not page._field_rows[("mimo", "voice")].isVisibleTo(page)


# ============================================================ 凭据组


def test_credential_field_sits_in_its_own_titled_group(tmp_path):
    """有 Key 的后端：凭据行单独跟一个组标题，标题直说用途、说明只讲存在哪。"""
    page = _page(tmp_path, backend="mimo")

    header = page.credential_group_row
    assert header.label.text() == CREDENTIAL_TITLE
    assert header.hint_label.text() == CREDENTIAL_HINT
    assert "钥匙串" in CREDENTIAL_HINT and "配置文件" in CREDENTIAL_HINT
    assert header.isVisibleTo(page)

    card = _block_card(page)
    keys = _keys(card)
    assert keys[keys.index("tts_credentials") + 1] == "voice_chime_mimo_api_key"
    assert page._field_rows[("mimo", "api_key")].isVisibleTo(page)

    # 同一时刻别的后端的凭据行仍在各自的折叠里（不跟着当前后端跑出来）
    assert not page._field_rows[("doubao", "api_key")].isVisibleTo(page)


def test_credential_group_is_hidden_when_the_backend_needs_no_key(tmp_path):
    """免凭据的后端（edge）：整组不出现，界面不留空标题。"""
    page = _page(tmp_path, backend="edge")
    assert not page.credential_group_row.isVisibleTo(page)
    assert not tts.secret_fields(tts.get("edge")), "前提：edge 没有凭据字段"


def test_secret_row_hint_tells_where_it_is_saved(tmp_path, monkeypatch):
    """凭据行只说状态（已保存 / 还没设置 / 没保存上），存放位置交给组标题那一句。"""
    from pet import tts_secrets

    monkeypatch.setattr(tts_secrets, "get", lambda ref: "")
    monkeypatch.setattr(tts_secrets, "persisted", lambda ref: False)
    page = _page(tmp_path, backend="mimo")
    key_row = page._field_rows[("mimo", "api_key")]
    assert key_row.hint_label.text().endswith("还没设置")

    monkeypatch.setattr(tts_secrets, "get", lambda ref: "already-saved")
    monkeypatch.setattr(tts_secrets, "persisted", lambda ref: True)
    page._refresh_secret_hints()
    assert key_row.hint_label.text().endswith("已保存")

    # 值只在当前进程内存里（钥匙串没写成）≠「已保存」：行里必须直说本进程临时。
    monkeypatch.setattr(tts_secrets, "persisted", lambda ref: False)
    page._refresh_secret_hints()
    hint = key_row.hint_label.text()
    assert "已保存" not in hint, f"没写进钥匙串就不能说「已保存」：{hint}"
    assert "仅本次运行" not in hint, f"独立设置进程退出后这次运行也就结束了：{hint}"
    assert "本进程" in hint and "重启" in hint


def test_secret_write_failure_is_reported_and_never_claims_saved(tmp_path, monkeypatch):
    """系统钥匙串写失败（模块在、系统拒绝写）：必须报失败，不得说「已保存」。

    修前的两种假话都出在这里：``_apply_secret`` 丢掉 ``set()`` 的返回值，行状态由
    「keyring 模块在不在」推导——模块在就报「已保存」（明明没保存进钥匙串），模块不在
    就报「已保存（仅本次运行）」（而设置页默认跑在独立进程里，主程序根本读不到）。
    """
    from PySide6.QtWidgets import QApplication, QMessageBox

    from pet import tts_secrets

    app = QApplication.instance() or QApplication([])
    warnings: list = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))

    class _BrokenKeyring:
        def get_password(self, service, ref):
            return None

        def set_password(self, service, ref, value):
            raise RuntimeError("system refused")

        def delete_password(self, service, ref):
            pass

    monkeypatch.setattr(tts_secrets, "_keyring_module", lambda: _BrokenKeyring())
    tts_secrets.reset_memory()

    page = _page(tmp_path, backend="mimo")
    page.mimo_key_edit.setText("sk-new")
    page.apply_to_config()
    app.processEvents()

    assert warnings, "凭据没能保存上时必须有一条看得见的提示"
    assert "没能保存上" in str(warnings[0][2])
    assert "本进程" in str(warnings[0][2])
    assert page.mimo_key_edit.text() == "", "值已收进内存，输入框照旧清空（不把明文留在控件里）"
    hint = page._field_rows[("mimo", "api_key")].hint_label.text()
    assert "已保存" not in hint, f"没写进钥匙串就不能说「已保存」：{hint}"
    assert "没能保存上" in hint


# ============================================================ 文案


def test_field_copy_is_human_and_short():
    """字段名说人话（音色/语速/音调/API Key），提示一句话且不超过 20 字。"""
    for provider in tts.providers():
        assert provider.fields, f"{provider.id} 必须有字段"
        for spec in provider.fields:
            assert len(spec.label) <= 8, f"{spec.key} 字段名太长：{spec.label}"
            assert 0 < len(spec.hint) <= 20, (
                f"{spec.key} 的提示必须是一句话且不超过 20 字：{spec.hint}"
            )


def _page_rows(page) -> list[SettingRow]:
    return page.findChildren(SettingRow)


def test_page_never_teaches_technical_details(tmp_path):
    """接口地址 / 资源号 / 错误码 / 外部文档这类细节一个字都不许出现在界面上。"""
    page = _page(tmp_path, backend="mimo")
    assert _page_rows(page)
    for row in _page_rows(page):
        text = f"{row.label.text()} {row.hint_label.text()}".lower()
        for marker in TECHNICAL_MARKERS:
            assert marker not in text, f"{row.objectName()} 又把技术细节摆上界面了：{text}"


def test_provider_docstrings_keep_the_technical_details():
    """删掉的技术细节必须还在 provider 模块里（换个地方存档，不是丢掉）。"""
    from pet.tts import doubao, edge, mimo

    assert "openspeech.bytedance.com" in doubao.__doc__
    assert "seed-tts-2.0" in doubao.__doc__
    assert "55000000" in doubao.__doc__
    assert "platform.xiaomimimo.com" in mimo.__doc__
    assert "NoAudioReceived" in edge.__doc__


# ============================================================ 默认后端


def test_default_backend_is_edge_and_free_of_credentials(tmp_path):
    """默认后端＝edge（免凭据）：陌生人开箱就有声，失败照走既有自动回退。"""
    from pet.config import Config

    assert DEFAULT_BACKEND == "edge"
    assert Config(base=tmp_path).data["voice_chime_tts_backend"] == "edge"
    assert default_chime_config()["voice_chime_tts_backend"] == "edge"
    assert clean_backend("") == "edge" and clean_backend("NoSuchBackend") == "edge"
    assert tts.get("edge").is_fallback is True, "edge 必须仍是后备后端"
    assert not tts.secret_fields(tts.get("edge")), "默认后端不得要求任何凭据"


# ============================================================ 域平卡（真实对话框）


def test_domain_card_keeps_the_current_backend_on_top(tmp_path, monkeypatch):
    """行被搬进「语音」域的平卡后，折叠与行序仍然成立（容器在那一步会散架）。"""
    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    try:
        labels = [dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())]
        voice_page = dialog.pages.widget(labels.index("语音"))
        card = _block_card(voice_page)

        keys = _keys(card)
        assert keys[keys.index("voice_chime_tts_backend") + 1] == "voice_chime_tts_fallback"
        assert keys.index("voice_chime_voice") == keys.index("voice_chime_tts_fallback") + 2, (
            "默认后端 edge 的字段必须排在「失败自动回退」之后"
        )
        # 展示用的折叠头/凭据组标题也得落在「语音」域，不能被「待分类（开发期）」收走
        for key in ("tts_credentials",) + tuple(
            f"tts_backend_fold_{provider_id}" for provider_id in BACKEND_IDS
        ):
            row = dialog.findChild(SettingRow, f"settingRow_{key}")
            assert row is not None, f"{key} 行必须存在"
            assert voice_page.isAncestorOf(row), f"{key} 必须在「语音」域"
    finally:
        dialog.reject()
        app.processEvents()


def test_search_reveals_a_row_inside_a_folded_backend(tmp_path, monkeypatch):
    """搜索命中折叠里的行：必须自动展开，否则「搜到了却看不见」。"""
    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    try:
        labels = [dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())]
        voice_page = dialog.pages.widget(labels.index("语音"))
        match_key = "settingRow_voice_chime_doubao_speaker"
        assert not dialog.findChild(SettingRow, match_key).isVisibleTo(voice_page), (
            "前提：默认（edge）下豆包的字段是收起的"
        )

        dialog.search_edit.setText("豆包 2.0")
        app.processEvents()
        matched = dialog._search_matches[dialog._search_index]
        assert matched.objectName() == match_key
        assert matched.isVisibleTo(voice_page), "命中折叠里的行必须自动展开"
        assert dialog.sidebar.currentItem().text() == "语音"
    finally:
        dialog.reject()
        app.processEvents()
