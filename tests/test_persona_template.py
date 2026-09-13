# -*- coding: utf-8 -*-
import json
import re
from pet.persona_phrases import phrase_keys
from pet.persona_template import build_persona_template, template_json


def test_template_is_complete_and_safe():
    data = build_persona_template({"dialogue_mode": "custom", "dialogue_phrases": {"start": "你好，{name}"}, "api_key": "secret", "path": "C:/secret"})
    assert data["template"] == "persona-phrases/v1"
    assert set(data["phrases"]) == set(phrase_keys())
    assert data["phrases"]["start"] == ["你好，{name}"]
    assert data["phrases"]["thinking"] == []
    assert data["variables"]["command"]
    text = template_json({"dialogue_phrases": {"start": "中文\n\""}})
    assert json.loads(text)["phrases"]["start"] == ["中文\n\""]
    assert "secret" not in text and "C:/secret" not in text


def test_template_limits_and_ignores_bad_values():
    data = build_persona_template({"dialogue_phrases": {"start": [" a ", 3] * 10, "thinking": None}})
    assert data["phrases"]["start"] == ["a"] * 8
    assert data["phrases"]["thinking"] == []

def test_all_advertised_fields_reach_presentation_layer():
    """模板宣称的每个字段都必须真正到达表现层（气泡/弹窗文案渲染）。

    两层机械验证，杜绝「模板提供了字段、运行时却从未注入」的糖衣：
    1. AST 解析 pet/agent_link.py 与 pet/app.py 里全部 _dialogue/_persona_text
       调用点，断言 PARAMETERS[key] == 该 key 调用点显式注入的 kwargs 并集
       （双向：多宣称=占位符永远原样露出的谎言；少宣称=已注入却不告知）。
       动态 key 调用点（activity/pattern/model_access）按 kwargs 签名归组校验。
    2. UPSTREAM_FIELDS 的每个上下文字段都必须在桥接插件源码中出现（桥确实
       会写出），或属于 Pet 侧注入（agent_key）。
    审计依据：docs/PERSONA-TEMPLATE-FIELD-ALIGNMENT-2026-09-05.md
    """
    import ast
    from pathlib import Path

    from pet.persona_template import (
        CONDITIONAL_PARAMETERS, DISPLAY_HINTS, EVENT_SOURCES, PARAMETERS,
        UPSTREAM_FIELDS, VARIABLES,
    )

    root = Path(__file__).resolve().parent.parent
    delivered = {}
    expansion_keys = set()
    dynamic = []
    for fname in ("agent_link.py", "app.py"):
        tree = ast.parse((root / "pet" / fname).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if callee not in ("_dialogue", "_persona_text") or not node.args:
                continue
            key_arg = node.args[0] if callee == "_dialogue" else node.args[1]
            # agent_key 是 _dialogue 的统一预设路由参数（ticket 05），不是模板注入
            # 字段：模板不宣称它，AST 收集时排除，避免与 PARAMETERS 严格对齐误报。
            kws = {kw.arg for kw in node.keywords if kw.arg and kw.arg != "agent_key"}
            has_expansion = any(kw.arg is None for kw in node.keywords)
            if isinstance(key_arg, ast.Constant) and isinstance(key_arg.value, str):
                delivered.setdefault(key_arg.value, set()).update(kws)
                if has_expansion:
                    expansion_keys.add(key_arg.value)
            else:
                dynamic.append((has_expansion, frozenset(kws)))

    # 动态 key 调用点：activity（**values 展开）/ pattern（name, reasons）/ model_access（count + **conditional 展开）
    assert (True, frozenset()) in dynamic, "activity 调用点应显式传 values 字典（含 tool/command 等）"
    assert (False, frozenset({"name", "reasons"})) in dynamic, "pattern 动态调用点缺失"
    assert (False, frozenset({"count"})) in dynamic or (True, frozenset({"count"})) in dynamic, \
        "model_access 动态调用点缺失（count + **conditional 展开）"
    # activity 调用点会读记录里的 target/ok 并按需注入，但桥接写出的 tool/call
    # 记录从不含这两个字段（只在 tool/result / watchdog reasoning）——活动气泡
    # 渲染时填充物是 tool/call，因此模板不宣称（写了就是永不替换的占位符）。
    activity_fields = ("name", "tool", "label", "command", "argsKey", "callId", "step",
                       "sessionName", "projectName")
    activity_unadvertised = {"target", "ok"}
    # model_access 的 key 是变量：dynamic_delivered 需包含 count + 条件参数
    model_access_fields = {"count"} | set(CONDITIONAL_PARAMETERS["model_access.one"])
    dynamic_delivered = {
        "activity.read": set(activity_fields), "activity.search": set(activity_fields),
        "activity.edit": set(activity_fields), "activity.run": set(activity_fields),
        "activity.default": set(activity_fields),
        "pattern.warning": {"name", "reasons"}, "pattern.control": {"name", "reasons"},
        "model_access.one": set(model_access_fields), "model_access.many": set(model_access_fields),
    }

    data = build_persona_template(None)
    entries = {e["key"]: e for e in data["entries"]}

    # variables/upstream 结构 sanity：cordis 等死字段不得回流
    assert set(VARIABLES) == {
        "name", "command", "label", "body", "count", "reasons", "detail", "text",
        "tool", "toolName", "argsKey", "callId", "step",
        "sessionName", "projectName", "event",
        "errorCode", "errorMessage", "errorKind", "consecutiveRetryCount", "retry",
        "retries", "retryExhausted", "failureType",
    }
    assert "cordis" not in data["upstream"]["fields"]
    assert "sessionName" in UPSTREAM_FIELDS["base"], "Bridge 必须直接写出 sessionName"
    assert set(PARAMETERS) == set(entries) == set(phrase_keys())
    for key, entry in entries.items():
        assert key in DISPLAY_HINTS and key in EVENT_SOURCES, key
        # 严格逐 key 相等：entries.parameters 只能列该项上游方法显式注入的字段，
        # 组级上下文字段（ts/sessionId/errorCode 等）不得混入——否则 AI 会写出
        # 运行时永远原样露出的占位符（如 agent.error 宣称 {errorCode}）。
        assert entry["parameters"] == list(PARAMETERS[key]), key

    # 典型误报场景抽查：状态机/检测器触发的弹窗只有保证参数
    assert entries["start"]["parameters"] == ["name"]
    assert entries["agent.error"]["parameters"] == ["name"]
    assert entries["llm_error.api"]["parameters"] == []
    assert {"count", "errorCode", "errorMessage", "consecutiveRetryCount", "retry",
            "sessionName", "projectName"} == set(
        entries["model_access.one"]["parameters"])
    assert set(entries["approval.command"]["parameters"]) == {
        "name", "command", "toolName", "sessionName", "projectName", "label"}

    # ── 核心保证：模板宣称参数与调用点注入对齐 ──
    # 参数分两类：保证参数必须以命名 kwargs 出现（少宣称=已注入却不告知，
    # 多宣称=占位符原样露出的谎言）；条件参数经 **conditional 展开注入
    #（AST 只能看到展开标记），其集合由 CONDITIONAL_PARAMETERS 声明并保证
    # 非空展开调用点都合法。
    for key in phrase_keys():
        actual = set(delivered.get(key, set())) | set(dynamic_delivered.get(key, set()))
        if key.startswith("activity."):
            actual -= activity_unadvertised
        conditional = set(CONDITIONAL_PARAMETERS.get(key, ()))
        expected_guaranteed = set(PARAMETERS[key]) - conditional
        if key in expansion_keys:
            assert expected_guaranteed <= actual, (
                key + ": 保证参数未以命名 kwargs 注入 " + str(sorted(expected_guaranteed - actual)))
            assert actual - conditional <= expected_guaranteed, (
                key + ": 注入了未宣称的字段 " + str(sorted(actual - conditional - expected_guaranteed)))
        else:
            assert set(PARAMETERS[key]) == actual, (
                key + ": 模板宣称 " + str(sorted(PARAMETERS[key]))
                + " != 运行时注入 " + str(sorted(actual)))

    # activity 不得残留从未传入的死字段；宣称的字段必须全部真的注入
    advertised_activity = {"name", "tool", "label", "command", "argsKey", "callId",
                           "step", "sessionName", "projectName"}
    assert advertised_activity <= set(entries["activity.read"]["parameters"])
    for dead in ("toolName", "riskScore", "pluginId", "sessionLabel", "target", "ok"):
        assert dead not in entries["activity.read"]["parameters"]

    # 条件参数声明非空校验：所有带 **conditional 展开的 key 必须声明条件参数
    for key in expansion_keys:
        assert set(CONDITIONAL_PARAMETERS.get(key, ())), (
            key + " 使用了 **conditional 展开，但 CONDITIONAL_PARAMETERS 未声明")

    # 审批/提问/限流/余额：与调用点一致
    assert set(entries["approval.command"]["parameters"]) >= {"name", "command", "toolName",
                                                             "sessionName", "projectName", "label"}
    assert set(entries["question.one"]["parameters"]) >= {"name", "body", "sessionName"}
    assert {"count", "errorCode", "errorMessage"} <= set(entries["model_access.one"]["parameters"])
    assert entries["balance.result"]["parameters"] == ["text"]

    # balance.loading/balance.result 真实渲染（pet/app.py），必须可导出；死键不得回流
    assert "balance.loading" in entries and "balance.result" in entries
    assert "balance.query" not in entries

    # ── 上下文字段层：桥接插件确实写出这些字段（或 Pet 侧注入）──
    # 桥接已拆分为稳定壳 index.js + impl/<version>/ 实现层：字段由实现层写出，
    # 因此把壳与**当前 package.json.version 对应的 impl** 合并作为"桥接插件
    # 源码"检查（与壳 probeDisk 的精确选中语义一致；不合并全部历史版本，
    # 否则旧版本残留字段会让已删除字段的新版本误通过）。
    bridge_root = root / "integrations" / "dsh-pet-bridge"
    bridge_pkg = json.loads((bridge_root / "package.json").read_text(encoding="utf-8"))
    bridge_src = (bridge_root / "index.js").read_text(encoding="utf-8")
    impl_dir = bridge_root / "impl" / str(bridge_pkg.get("version", ""))
    bridge_src += "\n" + (impl_dir / "index.js").read_text(encoding="utf-8")
    # 实现层版本注册表只做 re-export，无字段；此处不合并历史版本目录。
    agent_link_src = (root / "pet" / "agent_link.py").read_text(encoding="utf-8")
    pet_side_fields = {"agent_key"}
    for group, fields in UPSTREAM_FIELDS.items():
        for field in fields:
            if field in pet_side_fields:
                assert "agent_key" in agent_link_src
                continue
            pattern = chr(92) + "b" + re.escape(field) + chr(92) + "b"
            assert re.search(pattern, bridge_src), (
                "UPSTREAM_FIELDS[" + repr(group) + "] 的 " + repr(field)
                + " 在桥接插件中不存在，模板不得宣称"
            )
