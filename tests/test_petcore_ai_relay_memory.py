# -*- coding: utf-8 -*-
"""身份接力记忆适配层（petcore 移植）：facts.jsonl 语义 + 运行时接线 + 设置页。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests import test_petcore_ai_mod as ai_mod

relay = None


def setup_module():
    global relay
    relay = _load_relay()


def _load_relay():
    import importlib.util
    import sys

    path = ai_mod.MOD_DIR / "host" / "relay_store.py"
    spec = importlib.util.spec_from_file_location("ai_chat_relay_store", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def memdir(tmp_path: Path) -> Path:
    return tmp_path / "identity-memory"


def _append(memdir: Path, content: str, **kwargs) -> dict:
    return relay.append_fact(memdir, content, **kwargs)


# ---------------------------------------------------------------- 追加基础


def test_append_default_source(memdir):
    r = _append(memdir, "我喜欢吃辣")
    assert r["appended"] is True
    assert r["entry"]["source"] == "inferred"
    assert r["entry"]["status"] == "active"
    assert r["entry"]["id"] and r["entry"]["time"]


def test_append_source_preserved(memdir):
    r = _append(memdir, "主人说的重要事", source="owner")
    assert r["entry"]["source"] == "owner"


def test_empty_content_rejected(memdir):
    r = _append(memdir, "   ")
    assert r["appended"] is False
    assert "内容为空" in r["error"]
    assert relay.parse_facts(memdir) == []


def test_secret_content_rejected(memdir):
    for text in ("sk-abcdefghijklmnop", "api_key = abcdef123", "password: hunter22"):
        r = _append(memdir, text)
        assert r["appended"] is False
        assert "凭据" in r["error"]
    assert relay.parse_facts(memdir) == []


def test_plain_text_allowed(memdir):
    r = _append(memdir, "聊聊 token 的概念，别存密钥")
    assert r["appended"] is True


def test_duplicate_identical_content_blocked(memdir):
    assert _append(memdir, "同一句话")["appended"] is True
    r = _append(memdir, "同一句话")
    assert r["appended"] is False and r.get("duplicated") is True
    assert len(relay.parse_facts(memdir)) == 1


# ---------------------------------------------------------------- 同键新压旧


def test_subject_supersedes_old_active(memdir):
    _append(memdir, "上课时间是周三", subject="课表")
    r = _append(memdir, "上课时间改成周五", subject="课表")
    assert r["appended"] is True
    facts = relay.parse_facts(memdir)
    assert len(facts) == 2
    old = next(f for f in facts if f["content"] == "上课时间是周三")
    assert old["status"] == "superseded"
    assert old["supersededBy"] == r["entry"]["id"]
    assert "同键新记忆" in old["retractNote"]
    assert [f["content"] for f in relay.read_facts(memdir)] == ["上课时间改成周五"]


def test_subject_does_not_touch_other_subjects(memdir):
    _append(memdir, "上课时间是周三", subject="课表")
    _append(memdir, "我喜欢吃辣", subject="口味")
    _append(memdir, "上课时间改成周五", subject="课表")
    facts = relay.parse_facts(memdir)
    spicy = next(f for f in facts if f["content"] == "我喜欢吃辣")
    assert spicy["status"] == "active"


def test_no_subject_pure_append(memdir):
    _append(memdir, "第一句", subject="键A")
    _append(memdir, "第二句")
    _append(memdir, "第三句", subject="键A")
    second = next(f for f in relay.parse_facts(memdir) if f["content"] == "第二句")
    assert second["status"] == "active"


# ---------------------------------------------------------------- 关联


def test_related_by_id_and_fragment(memdir):
    a = _append(memdir, "主人喜欢喝咖啡", source="owner")
    b = _append(memdir, "主人周五有课", source="owner")
    r = _append(memdir, "记得提醒主人带伞", related=[a["entry"]["id"], "咖啡"])
    assert r["appended"] is True
    rel = r["entry"]["related"]
    assert a["entry"]["id"] in rel and b["entry"]["id"] not in rel


def test_related_unresolved_reported(memdir):
    r = _append(memdir, "新记忆", related=["不存在的内容片段"])
    assert r["appended"] is True
    assert r["unresolved"] == ["不存在的内容片段"]


# ---------------------------------------------------------------- 检索


def test_recall_hits_content_and_subject(memdir):
    _append(memdir, "我养了一只猫叫团子", source="owner")
    _append(memdir, "红网投稿没过", source="evidence", subject="红网")
    hits = relay.recall_facts(memdir, "红网")
    assert any("投稿" in h["content"] for h in hits)


def test_recall_source_ranking_owner_first(memdir):
    _append(memdir, "课程是周三", source="inferred")
    _append(memdir, "课程是周五", source="tool")
    _append(memdir, "课程是周一", source="owner")
    hits = relay.recall_facts(memdir, "课程")
    assert hits[0]["content"] == "课程是周一"


def test_recall_empty_query_recent_first(memdir):
    _append(memdir, "第一条")
    _append(memdir, "第二条")
    _append(memdir, "第三条")
    hits = relay.recall_facts(memdir, "")
    assert [h["content"] for h in hits] == ["第三条", "第二条", "第一条"]


def test_recall_hard_limit_20(memdir):
    for i in range(30):
        _append(memdir, f"话题词 第{i}条")
    hits = relay.recall_facts(memdir, "话题词", limit=100)
    assert len(hits) <= 20


def test_recall_related_expansion(memdir):
    b = _append(memdir, "办件提醒定在 11-07", source="tool")
    _append(memdir, "衡阳办件要跟进", source="owner", subject="衡阳", related=[b["entry"]["id"]])
    hits = relay.recall_facts(memdir, "衡阳")
    related = [h for h in hits if h.get("via") == "related"]
    assert related and related[0]["content"] == "办件提醒定在 11-07"


def test_recall_skips_superseded(memdir):
    _append(memdir, "课程是周三", source="owner", subject="课表")
    _append(memdir, "课程是周五", source="owner", subject="课表")
    hits = relay.recall_facts(memdir, "课程")
    assert [h["content"] for h in hits] == ["课程是周五"]


def test_recall_include_all_flag(memdir):
    _append(memdir, "课程是周三", subject="课表")
    _append(memdir, "课程是周五", subject="课表")
    hits = relay.recall_facts(memdir, "课程", all=True)
    assert len(hits) == 2


# ---------------------------------------------------------------- 作废与归档


def test_retract_latest_match_from_end(memdir):
    _append(memdir, "过期信息甲")
    _append(memdir, "过期信息甲，补充乙")
    r = relay.retract_fact(memdir, "过期信息甲", note="记错了")
    assert r["found"] is True
    assert r["entry"]["content"] == "过期信息甲，补充乙"
    assert r["entry"]["status"] == "superseded"
    assert r["entry"]["retractNote"] == "记错了"


def test_retract_empty_query(memdir):
    r = relay.retract_fact(memdir, "  ")
    assert r["found"] is False and "内容片段" in r["error"]


# ---------------------------------------------------------------- 读取与统计


def test_read_facts_default_active_latest_first(memdir):
    _append(memdir, "A")
    _append(memdir, "B", subject="键")
    _append(memdir, "B2", subject="键")
    assert [f["content"] for f in relay.read_facts(memdir)] == ["B2", "A"]


def test_stats_counts(memdir):
    _append(memdir, "A")
    _append(memdir, "B", subject="键")
    _append(memdir, "B2", subject="键")
    s = relay.store_stats(memdir)
    assert s["factCount"] == 3 and s["activeCount"] == 2 and s["supersededCount"] == 1


def test_bad_lines_skipped(memdir):
    path = memdir / "facts.jsonl"
    memdir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "这不是 JSON\n" + json.dumps({"content": "正常条目", "source": "owner"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    facts = relay.parse_facts(memdir)
    assert len(facts) == 1 and facts[0]["source"] == "owner"


def test_read_identity_text_missing_file(memdir):
    assert relay.read_identity_text(memdir, "persona.md") == ""


def test_read_identity_text_caps(memdir):
    memdir.mkdir(parents=True, exist_ok=True)
    (memdir / "handover.md").write_text("短交接书", encoding="utf-8")
    assert relay.read_identity_text(memdir, "handover.md") == "短交接书"


# ---------------------------------------------------------------- 运行时接线


def _runtime(tmp_path: Path):
    context = ai_mod._context(tmp_path)
    data = tmp_path / "data"
    context.ports.storage = SimpleNamespace(data_dir=lambda owner: data / str(owner))
    runtime = ai_mod._create_host(context).runtime_factory(context)
    return runtime, context


def _relay_on(tmp_path: Path, directory: Path):
    runtime, context = _runtime(tmp_path)
    context.ports.config.set("ai_memory_relay_enabled", True)
    context.ports.config.set("ai_memory_relay_dir", str(directory))
    return runtime, context


def test_relay_disabled_by_default_keeps_old_backend(tmp_path: Path):
    runtime, context = _runtime(tmp_path)
    runtime._remember_text("记住我喜欢红茶")
    assert not (tmp_path / "identity-memory" / "facts.jsonl").exists(), (
        "默认关闭时不得碰身份接力目录")
    assert "红茶" in runtime._memory_context("我喜欢喝什么"), "旧后端照常工作"


def test_relay_remember_writes_facts_jsonl(tmp_path: Path):
    directory = tmp_path / "shared-memory"
    runtime, _context = _relay_on(tmp_path, directory)
    runtime._remember_text("记住我不喜欢吃辣")
    path = directory / "facts.jsonl"
    assert path.is_file()
    facts = relay.parse_facts(directory)
    assert len(facts) == 1
    assert facts[0]["content"] == "我不喜欢吃辣"
    assert facts[0]["source"] == "owner", "主人的原话按 owner 档记，与 DSH 侧同档"


def test_relay_remember_and_recall_round_trip(tmp_path: Path):
    directory = tmp_path / "shared-memory"
    runtime, _context = _relay_on(tmp_path, directory)
    runtime._remember_text("记住我不喜欢吃辣")
    context = runtime._memory_context("吃辣")
    assert "不喜欢吃辣" in context


def test_relay_does_not_write_when_memory_disabled(tmp_path: Path):
    directory = tmp_path / "shared-memory"
    runtime, context = _relay_on(tmp_path, directory)
    context.ports.config.set("ai_memory_enabled", False)
    runtime._remember_text("记住我不喜欢吃辣")
    assert not (directory / "facts.jsonl").exists()


def test_relay_prompt_injects_persona_and_handover(tmp_path: Path):
    directory = tmp_path / "shared-memory"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "persona.md").write_text("我是小鲸。", encoding="utf-8")
    (directory / "handover.md").write_text("正在做身体计划。", encoding="utf-8")
    runtime, _context = _relay_on(tmp_path, directory)
    prompt = runtime._prompt()
    assert "我是小鲸。" in prompt
    assert "正在做身体计划。" in prompt


def test_relay_prompt_respects_persona_switch(tmp_path: Path):
    directory = tmp_path / "shared-memory"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "persona.md").write_text("我是小鲸。", encoding="utf-8")
    runtime, context = _relay_on(tmp_path, directory)
    context.ports.config.set("ai_memory_relay_persona", False)
    assert "我是小鲸。" not in runtime._prompt()


def test_relay_prompt_missing_files_still_works(tmp_path: Path):
    directory = tmp_path / "shared-memory"
    runtime, _context = _relay_on(tmp_path, directory)
    prompt = runtime._prompt()
    assert prompt, "人格卡缺失时退回人设提示词，不得返回空串"


def test_relay_dir_defaults_to_home_dot_dsh(tmp_path: Path):
    runtime, context = _runtime(tmp_path)
    context.ports.config.set("ai_memory_relay_enabled", True)
    assert runtime._relay_dir() == Path.home() / ".dsh" / "identity-memory"


# ---------------------------------------------------------------- 设置页


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_settings_page_relay_rows_round_trip(qt_app, tmp_path: Path):
    from petcore.config import Config

    context = ai_mod._context(tmp_path)
    definition = ai_mod._create_host(context)
    host = SimpleNamespace(
        config=Config(tmp_path),
        features=SimpleNamespace(ports=SimpleNamespace(api=ai_mod.FakeApi())),
    )
    page = definition.settings_factory(host)
    try:
        assert page.relay_check.isChecked() is False, "接力开关默认关"
        page.relay_check.setChecked(True)
        page.relay_dir_edit.setText(str(tmp_path / "shared-memory"))
        page.relay_dir_edit.editingFinished.emit()
        page.relay_persona_check.setChecked(False)
        assert host.config.get("ai_memory_relay_enabled") is True
        assert host.config.get("ai_memory_relay_dir") == str(tmp_path / "shared-memory")
        assert host.config.get("ai_memory_relay_persona") is False
    finally:
        page.close()
