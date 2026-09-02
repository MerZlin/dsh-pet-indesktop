"""Versioned context-menu layout domain."""
from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Collection, Mapping


DEFAULT_LAYOUT_ID = "modern-default-v1"


@dataclass(frozen=True)
class ResolvedMenuLayout:
    nodes: tuple[dict, ...]
    source: str
    diagnostics: tuple[str, ...]


def load_default_menu_layout(layout_id: str = DEFAULT_LAYOUT_ID) -> dict:
    """Load a fresh copy of a bundled, versioned menu layout."""
    if layout_id != DEFAULT_LAYOUT_ID:
        raise ValueError(f"unknown menu layout: {layout_id}")
    path = resources.files("pet.menu_templates").joinpath(f"{layout_id}.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("layout_id") != layout_id or data.get("schema_version") != 1:
        raise ValueError(f"invalid menu layout: {layout_id}")
    return data


def resolve_menu_layout(
    raw_layout: Mapping | None,
    *,
    registered_actions: Collection[str],
    available_actions: Collection[str],
) -> ResolvedMenuLayout:
    """Resolve persisted layout data without exposing validation mechanics."""
    registered = set(registered_actions)
    available = set(available_actions)

    def safe_fallback(diagnostics: tuple[str, ...]) -> ResolvedMenuLayout:
        nodes = tuple(
            {"type": "action", "id": action_id, "visible": True}
            for action_id in ("modern_settings", "quit")
            if action_id in registered and action_id in available
        )
        return ResolvedMenuLayout(nodes, "fallback", diagnostics)

    source = "user"
    if raw_layout is None:
        try:
            raw_layout = load_default_menu_layout()
        except (OSError, ValueError, json.JSONDecodeError):
            return safe_fallback(("default-layout-unavailable",))
        source = "default"
    seen: set[str] = set()
    diagnostics: list[str] = []
    normalization: list[str] = []
    schema_version = raw_layout.get("schema_version")
    if schema_version != 1:
        diagnostics.append(f"unsupported-schema:{schema_version}")

    def visit(nodes, submenu_depth: int = 0) -> None:
        for node in nodes if isinstance(nodes, list) else ():
            if not isinstance(node, dict):
                continue
            if node.get("type") == "action":
                action_id = str(node.get("id") or "")
                if action_id in seen:
                    diagnostics.append(f"duplicate-action:{action_id}")
                seen.add(action_id)
            if node.get("type") == "submenu":
                if submenu_depth >= 1:
                    diagnostics.append(
                        f"submenu-depth-exceeded:{str(node.get('id') or '')}"
                    )
                visit(node.get("children", []), submenu_depth + 1)

    visit(raw_layout.get("nodes", []))
    if diagnostics:
        return safe_fallback(tuple(diagnostics))

    def resolve_nodes(nodes) -> tuple[dict, ...]:
        resolved: list[dict] = []
        for node in nodes if isinstance(nodes, list) else ():
            if not isinstance(node, dict) or not node.get("visible", True):
                continue
            node_type = node.get("type")
            if node_type == "action":
                action_id = str(node.get("id") or "")
                if action_id not in registered:
                    normalization.append(f"unknown-action:{action_id}")
                    continue
                if action_id in registered and action_id in available:
                    action = {"type": "action", "id": action_id, "visible": True}
                    if node.get("section"):
                        action["section"] = str(node["section"])
                    resolved.append(action)
                continue
            if node_type == "submenu":
                children = resolve_nodes(node.get("children", []))
                if children:
                    submenu = {
                        "type": "submenu",
                        "id": str(node.get("id") or ""),
                        "label": str(node.get("label") or ""),
                        "visible": True,
                        "children": children,
                    }
                    if node.get("section"):
                        submenu["section"] = str(node["section"])
                    resolved.append(submenu)
        return tuple(resolved)

    resolved_nodes = list(resolve_nodes(raw_layout.get("nodes", [])))

    def contains_action(nodes: tuple[dict, ...] | list[dict], action_id: str) -> bool:
        return any(
            (node.get("type") == "action" and node.get("id") == action_id)
            or contains_action(node.get("children", ()), action_id)
            for node in nodes
        )

    for action_id in ("modern_settings", "quit"):
        if action_id in registered and action_id in available and not contains_action(
            resolved_nodes, action_id
        ):
            resolved_nodes.append(
                {"type": "action", "id": action_id, "visible": True}
            )
            normalization.append(f"required-action-restored:{action_id}")

    return ResolvedMenuLayout(
        tuple(resolved_nodes),
        "normalized" if normalization else source,
        tuple(normalization),
    )
