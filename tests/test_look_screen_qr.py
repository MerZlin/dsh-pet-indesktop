# -*- coding: utf-8 -*-
"""看看屏幕二维码识别（本 fork 新增功能，全部测试收纳于此独立文件）。

架构说明：upstream 的 pet/window.py、pet/vision.py 主体零改动。二维码逻辑以
纯附加块挂在 pet/vision.py 末尾，靠模块名重绑（capture_screen_bytes /
ask_about_screen）对调用方透明生效；开关键 look_screen_qr_enabled 读 config.json。
因此本文件的测试全部针对 vision 层包装与设置页插入，不触碰 PetWindow。
"""

import pytest

from pet import vision


@pytest.fixture(autouse=True)
def _clean_qr_state():
    """模块级二维码状态在用例间清零，防泄漏。"""
    vision._last_look_qr_texts.clear()
    yield
    vision._last_look_qr_texts.clear()


# ================================================================ 解码核心

def _make_qr_pil(text, scale=8):
    """生成二维码灰度图（zxing-cpp 3.x write_barcode_to_image → PIL）。"""
    import zxingcpp
    from PIL import Image
    bc = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
    img = zxingcpp.write_barcode_to_image(bc, scale=scale)
    return Image.frombytes("L", (img.shape[1], img.shape[0]), bytes(memoryview(img)))


def test_decode_qr_codes_finds_qr_text():
    """屏幕大画布上的二维码应被本地解码出原文。"""
    pytest.importorskip("zxingcpp")
    from PIL import Image
    canvas = Image.new("RGB", (1920, 1080), (240, 240, 245))
    canvas.paste(_make_qr_pil("https://example.com/dshpet").convert("RGB"), (1400, 700))
    assert vision.decode_qr_codes(canvas) == ["https://example.com/dshpet"]


def test_decode_qr_codes_dedupes_and_caps_results():
    """同一内容去重；结果数量受 MAX_QR_RESULTS 上限保护。"""
    pytest.importorskip("zxingcpp")
    from PIL import Image
    canvas = Image.new("RGB", (1600, 900), (250, 250, 250))
    canvas.paste(_make_qr_pil("dup-content").convert("RGB"), (50, 50))
    canvas.paste(_make_qr_pil("dup-content", scale=6).convert("RGB"), (400, 50))
    canvas.paste(_make_qr_pil("b").convert("RGB"), (50, 500))
    canvas.paste(_make_qr_pil("c").convert("RGB"), (700, 500))
    canvas.paste(_make_qr_pil("d").convert("RGB"), (1200, 500))
    texts = vision.decode_qr_codes(canvas)
    assert len(texts) == vision.MAX_QR_RESULTS
    assert len(set(texts)) == len(texts)
    assert "dup-content" in texts


def test_decode_qr_codes_no_barcode_returns_empty():
    """无码画面返回空列表而非报错。"""
    pytest.importorskip("zxingcpp")
    from PIL import Image
    assert vision.decode_qr_codes(Image.new("RGB", (800, 600), (255, 255, 255))) == []


def test_decode_qr_codes_none_image_returns_empty():
    assert vision.decode_qr_codes(None) == []


def test_decode_qr_codes_missing_lib_degrades_to_empty(monkeypatch):
    """zxing-cpp 缺失（如精简构建）时静默降级为空列表，绝不抛错影响主流程。"""
    import builtins
    from PIL import Image
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "zxingcpp":
            raise ImportError("No module named 'zxingcpp'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert vision.decode_qr_codes(Image.new("RGB", (100, 100), (255, 255, 255))) == []


def test_decode_qr_codes_skips_1d_barcodes():
    """1D 商品条码（Code128 等）刻意不识别：屏幕上多为噪音内容。"""
    pytest.importorskip("zxingcpp")
    import zxingcpp
    from PIL import Image
    bc = zxingcpp.create_barcode("1234567890", zxingcpp.BarcodeFormat.Code128)
    img = zxingcpp.write_barcode_to_image(bc, scale=4)
    pil = Image.frombytes("L", (img.shape[1], img.shape[0]), bytes(memoryview(img)))
    canvas = Image.new("RGB", (600, 400), (255, 255, 255))
    canvas.paste(pil.convert("RGB"), (100, 150))
    assert vision.decode_qr_codes(canvas) == []


def test_format_qr_notice_single_and_multi():
    assert vision.format_qr_notice(["https://a.b/c"]) == "【二维码】https://a.b/c"
    multi = vision.format_qr_notice(["x", "y"])
    assert "【二维码×2】" in multi
    assert "1. x" in multi and "2. y" in multi


# ================================================================ 模块重绑与包装

def test_module_names_rebound_to_qr_aware_wrappers():
    """调用方（window._look_worker 的 vision_mod.<名字> 查找）必须命中包装。"""
    assert vision.capture_screen_bytes is vision._qr_aware_capture_screen_bytes
    assert vision.ask_about_screen is vision._qr_aware_ask_about_screen


def test_capture_wrapper_records_qr_and_returns_jpeg(monkeypatch):
    """包装截屏：原始分辨率解码、记录状态、返回与 upstream 同款缩放/编码的 JPEG。"""
    fake_img = object()
    seen = {}

    def fake_decode(img):
        seen["img"] = img
        return ["https://qr.example/x"]

    monkeypatch.setattr(vision, "capture_screen_image", lambda: fake_img)
    monkeypatch.setattr(vision, "decode_qr_codes", fake_decode)
    monkeypatch.setattr(vision, "_look_qr_enabled", lambda: True)
    monkeypatch.setattr(vision, "image_to_jpeg_bytes", lambda img: b"jpeg-" + str(id(img)).encode())

    data = vision.capture_screen_bytes()

    assert seen["img"] is fake_img, "必须用原始分辨率图解码（缩放后小码必死）"
    assert list(vision._last_look_qr_texts) == ["https://qr.example/x"]
    assert data.startswith(b"jpeg-")


def test_capture_wrapper_skips_decode_when_disabled(monkeypatch):
    """开关关闭：不调用解码、状态清空，其余行为不变。"""
    calls = []
    monkeypatch.setattr(vision, "capture_screen_image", lambda: object())
    monkeypatch.setattr(vision, "decode_qr_codes", lambda img: calls.append(img) or ["https://x"])
    monkeypatch.setattr(vision, "_look_qr_enabled", lambda: False)
    monkeypatch.setattr(vision, "image_to_jpeg_bytes", lambda img: b"jpeg")

    assert vision.capture_screen_bytes() == b"jpeg"
    assert calls == []
    assert vision._last_look_qr_texts == []


def test_ask_wrapper_prepends_notice(monkeypatch):
    """截到二维码时：回复以【二维码】原文开头，模型点评在后；状态读取后清空。"""
    vision._last_look_qr_texts.extend(["https://qr.example/x"])

    def fake_upstream_ask(image, app_info, system_prompt, p, pet_name=""):
        return "屏幕上有个链接呢。"

    monkeypatch.setattr(vision, "_upstream_ask_about_screen", fake_upstream_ask)
    reply = vision.ask_about_screen(b"jpeg", "app | t", "sys", object(), pet_name="小鱼")

    assert reply.startswith("【二维码】https://qr.example/x\n")
    assert reply.endswith("屏幕上有个链接呢。")
    assert vision._last_look_qr_texts == [], "状态应读取后清空，防陈旧内容泄漏到下次"


def test_ask_wrapper_outputs_qr_even_when_model_fails(monkeypatch):
    """模型失败但二维码已本地解出：内容仍要输出，不吞进错误气泡。"""
    vision._last_look_qr_texts.extend(["https://qr.example/y"])

    def fake_upstream_ask(*a, **kw):
        raise vision.VisionError("网络断了")

    monkeypatch.setattr(vision, "_upstream_ask_about_screen", fake_upstream_ask)
    reply = vision.ask_about_screen(b"jpeg", "", "sys", object())
    assert reply.startswith("【二维码】https://qr.example/y\n")
    assert "画面点评没发出来" in reply


def test_ask_wrapper_reraises_when_no_qr(monkeypatch):
    """无二维码状态时模型失败：维持 upstream 语义原样抛错。"""
    def fake_upstream_ask(*a, **kw):
        raise vision.VisionError("网络断了")

    monkeypatch.setattr(vision, "_upstream_ask_about_screen", fake_upstream_ask)
    with pytest.raises(vision.VisionError):
        vision.ask_about_screen(b"jpeg", "", "sys", object())


def test_ask_wrapper_without_qr_keeps_legacy_reply(monkeypatch):
    """无二维码时行为与 upstream 完全一致：回复即模型原文，不加前缀。"""
    monkeypatch.setattr(vision, "_upstream_ask_about_screen", lambda *a, **kw: "就一句点评")
    assert vision.ask_about_screen(b"jpeg", "app", "sys", object()) == "就一句点评"


# ================================================================ 开关与持久化

def test_look_qr_toggle_defaults_and_disk_roundtrip(tmp_path):
    """新配置默认开；设置进程写盘后无需 reload、直接读盘即可生效。"""
    import json
    from pet.config import Config
    cfg = Config(base=tmp_path)
    assert cfg.get("look_screen_qr_enabled") is True
    cfg.save()  # 落盘，模拟主进程已有配置文件

    # 模拟独立设置进程直接写盘（不经主进程内存）
    path = cfg.path
    disk = json.loads(path.read_text(encoding="utf-8"))
    disk["look_screen_qr_enabled"] = False
    path.write_text(json.dumps(disk, ensure_ascii=False), encoding="utf-8")

    fresh = Config(base=tmp_path)
    assert fresh.get("look_screen_qr_enabled") is False


def test_look_qr_toggle_string_bool_normalized(tmp_path):
    """手改 config.json 写字符串布尔（bool("false") is True 老坑）不得误开。"""
    import json
    from pet.config import Config
    cfg = Config(base=tmp_path)
    cfg.set("look_screen_qr_enabled", "false")
    cfg.save()  # save() 走 _normalize_pet_settings 归一化
    assert cfg.get("look_screen_qr_enabled") is False


def test_ai_settings_page_persists_qr_toggle():
    """modern 设置页：「识别屏幕二维码」开关随 save() 写回 config。"""
    import os
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        pytest.skip("无显示环境下不起设置页")
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    from pet.chat.models import ChatSettings, ProviderConfig

    QApplication.instance() or QApplication([])
    from pet.chat.ai_settings_page import _AiSettingsPage

    stored = {}
    shared_provider = ProviderConfig.from_dict("test-p", {"model": "m", "api_key": ""})
    settings = ChatSettings(providers={"test-p": shared_provider}, active_provider="test-p")
    config = SimpleNamespace(
        get=lambda k, d=None: stored.get(k, d),
        set=lambda k, v: stored.__setitem__(k, v),
        chat_settings=lambda: settings,
        set_chat_settings=lambda s: None,
    )
    page = _AiSettingsPage(config)
    # 默认（config 无值）应为开
    assert page.look_qr_check.isChecked() is True
    page.look_qr_check.setChecked(False)
    page.save()
    assert stored["look_screen_qr_enabled"] is False
    page.look_qr_check.setChecked(True)
    page.save()
    assert stored["look_screen_qr_enabled"] is True


def test_legacy_dialog_has_qr_toggle_wiring():
    """legacy 设置对话框同样包含二维码开关的读取与保存（源级守卫）。"""
    import inspect
    from pet.chat import settings_dialog
    src = inspect.getsource(settings_dialog)
    assert 'config.get("look_screen_qr_enabled", True)' in src
    assert "config.set('look_screen_qr_enabled'" in src
