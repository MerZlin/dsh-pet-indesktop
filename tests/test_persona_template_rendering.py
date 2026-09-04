# -*- coding: utf-8 -*-
from pet.persona_phrases import render_template


def test_render_template_exposes_future_upstream_fields_and_nested_payload():
    text = render_template(
        "{name}|{errorCode}|{payload.pluginId}|{data.reason}|{missing}",
        {
            "name": "DSH",
            "payload": {"pluginId": "p1", "reason": "retry", "errorCode": "E1"},
        },
    )
    assert text == "DSH|E1|p1|retry|{missing}"


def test_render_template_supports_list_index_and_format_spec():
    text = render_template(
        "{questions[0][label]}:{count:02d}",
        {"questions": [{"label": "方案 A"}], "count": 3},
    )
    assert text == "方案 A:03"
