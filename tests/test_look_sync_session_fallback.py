# -*- coding: utf-8 -*-
"""issue #366：识屏问答的「无聊天窗」兜底同步。

回归：v4.3.4（a9c42ac6）把 ``PetInstance.sync_look_to_chat`` 兜底分支的取角色
写成 ``self._identity_config()``——那是 ``AppShell`` 的方法，``PetInstance`` 没有。
于是「本次进程从未打开过聊天窗」时的识屏同步必抛 AttributeError，被 ``except``
吞成 ERROR 日志，会话一行没写（issue #366 实机日志）。

正确取源是 ``self.config``：``PetInstance`` 的配置面本来就是每宠路由视图
（主宠 = slot0 视图、子宠 = slot-N 视图，见 app.py 两个构造点），``character``
每宠键直接可读；``SessionStore`` 同样按实例隔离，身份与会话目录必须同视图。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

from pet import pet_profiles  # noqa: E402
from pet.app import PetInstance  # noqa: E402
from pet.chat.session_store import SessionStore  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.settings_slot_config import route_settings_config  # noqa: E402


def _make_instance(tmp_path) -> tuple[PetInstance, object]:
    """真实 ``PetInstance``（聊天窗从未打开 → ``chat_window=None``）。

    配置面用与生产主宠同构的 slot0 路由视图（app.py ``AppShell`` 构造点），
    不用裸全局 ``Config``——那样测不出「读错身份面」这族回归。
    """
    shell = SimpleNamespace(enable_chat=True)
    view = route_settings_config(Config(base=tmp_path, instance_id="slot-0"))
    return PetInstance(shell, view), view


def test_look_sync_fallback_writes_session_without_chat_window(tmp_path):
    """聊天窗从未打开时，识屏问答兜底同步必须落进本宠角色的会话。

    红线（issue #366）：修复前 ``self._identity_config()`` 抛 AttributeError
    被 except 吞掉，会话一行没写。
    """
    inst, view = _make_instance(tmp_path)
    assert inst.chat_window is None, "前提：本次进程从未打开过聊天窗（兜底分支）"

    assert pet_profiles.write_profile_settings(
        view.dir, 0, {"character": "look-cat"}).status == "ok"
    inst.sync_look_to_chat("[看看屏幕]", "屏幕上是一只猫。")

    store = SessionStore(view.dir, view.instance_id)
    sessions = store.list("look-cat")
    assert sessions, "会话没有写进去 = 兜底分支崩了（issue #366）"
    assert [(m.role, m.content) for m in sessions[0].messages] == [
        ("user", "[看看屏幕]"), ("assistant", "屏幕上是一只猫。")]


def test_look_sync_fallback_uses_the_per_pet_character(tmp_path):
    """兜底同步的角色必须取每宠视图的 ``character``，不是全局默认残留。

    slot0 档案选了非默认角色而全局面仍是出厂默认时，会话必须落在
    ``look-cat`` 名下——防止回归成「读全局面拿到默认角色、串进默认角色的
    会话」（a9c42ac6 想修、但搬错类的那个同族缺陷）。
    """
    from pet import catalog

    inst, view = _make_instance(tmp_path)
    assert pet_profiles.write_profile_settings(
        view.dir, 0, {"character": "look-cat"}).status == "ok"
    assert view.get("character") == "look-cat"
    assert view.get("character") != catalog.DEFAULT_CHARACTER, (
        "前提：look-cat 必须不是出厂默认，断言才有区分度")

    inst.sync_look_to_chat("[看看屏幕]", "看到了。")

    store = SessionStore(view.dir, view.instance_id)
    assert store.list("look-cat"), "问答必须写进档案选定角色的会话"
