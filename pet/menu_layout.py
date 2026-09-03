"""Versioned context-menu layout domain."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from importlib import resources
from typing import Collection, Mapping


DEFAULT_LAYOUT_ID = "modern-default-v1"


@dataclass(frozen=True)
class ResolvedMenuLayout:
    nodes: tuple[dict, ...]
    source: str
    diagnostics: tuple[str, ...]


def _insert_at_template_anchor(
    target: list[dict], node: dict, template: list[dict], template_index: int
) -> None:
    """Insert beside the nearest template sibling without reordering user nodes."""
    target_ids = [str(candidate.get("id") or "") for candidate in target]
    for sibling in reversed(template[:template_index]):
        sibling_id = str(sibling.get("id") or "")
        if sibling_id in target_ids:
            target.insert(target_ids.index(sibling_id) + 1, node)
            return
    for sibling in template[template_index + 1 :]:
        sibling_id = str(sibling.get("id") or "")
        if sibling_id in target_ids:
            target.insert(target_ids.index(sibling_id), node)
            return
    target.append(node)


def _merge_future_default_actions(
    user_nodes: list[dict],
    default_nodes: list[dict],
    *,
    registered: set[str],
    normalization: list[str],
) -> None:
    """Add actions introduced after a user layout was saved.

    The editor has no delete operation: visibility is the explicit way to hide an
    action. Therefore absence can safely mean "this override predates the action".
    Existing nodes, including hidden ones, keep their order and parent.
    """

    def collect_action_ids(nodes) -> set[str]:
        action_ids: set[str] = set()
        for node in nodes if isinstance(nodes, list) else ():
            if not isinstance(node, dict):
                continue
            if node.get("type") == "action":
                action_ids.add(str(node.get("id") or ""))
            action_ids.update(collect_action_ids(node.get("children", [])))
        return action_ids

    present = collect_action_ids(user_nodes)
    required = {"modern_settings", "quit"}

    def merge(target: list[dict], template: list[dict]) -> None:
        for index, default_node in enumerate(template):
            if not isinstance(default_node, dict):
                continue
            node_type = default_node.get("type")
            node_id = str(default_node.get("id") or "")
            if node_type == "action":
                if node_id in present or node_id not in registered or node_id in required:
                    continue
                _insert_at_template_anchor(target, deepcopy(default_node), template, index)
                present.add(node_id)
                normalization.append(f"default-action-added:{node_id}")
                continue
            if node_type != "submenu":
                continue
            matching_submenu = next(
                (
                    candidate
                    for candidate in target
                    if isinstance(candidate, dict)
                    and candidate.get("type") == "submenu"
                    and str(candidate.get("id") or "") == node_id
                ),
                None,
            )
            if matching_submenu is not None:
                children = matching_submenu.get("children")
                if not isinstance(children, list):
                    children = []
                    matching_submenu["children"] = children
                merge(children, default_node.get("children", []))
                continue

            missing_children: list[dict] = []
            merge(missing_children, default_node.get("children", []))
            if missing_children:
                submenu = deepcopy(default_node)
                submenu["children"] = missing_children
                _insert_at_template_anchor(target, submenu, template, index)

    merge(user_nodes, default_nodes)


def load_default_menu_layout(layout_id: str = DEFAULT_LAYOUT_ID) -> dict:
    """Load a fresh copy of a bundled, versioned menu layout."""
    if layout_id != DEFAULT_LAYOUT_ID:
        raise ValueError(f"unknown menu layout: {layout_id}")
    path = resources.files("pet.menu_templates").joinpath(f"{layout_id}.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("layout_id") != layout_id or data.get("schema_version") != 1:
        raise ValueError(f"invalid menu layout: {layout_id}")
    return data


def materialize_implicit_separators(raw_layout: Mapping) -> dict:
    """Turn legacy section boundaries into editable separator nodes.

    Runtime rendering keeps supporting old ``section`` metadata. The editor
    upgrades it so every layout-affecting divider is visible and editable.
    """
    layout = deepcopy(dict(raw_layout))
    used_ids: set[str] = set()

    def collect(nodes) -> None:
        for node in nodes if isinstance(nodes, list) else ():
            if not isinstance(node, dict):
                continue
            used_ids.add(str(node.get("id") or ""))
            collect(node.get("children", []))

    collect(layout.get("nodes", []))
    counter = 1

    def next_id() -> str:
        nonlocal counter
        while f"user.separator-{counter}" in used_ids:
            counter += 1
        result = f"user.separator-{counter}"
        used_ids.add(result)
        counter += 1
        return result

    def upgrade(nodes) -> list[dict]:
        if not isinstance(nodes, list):
            return []
        upgraded: list[dict] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            copied = deepcopy(node)
            if copied.get("type") == "submenu":
                copied["children"] = upgrade(copied.get("children", []))
            upgraded.append(copied)
        if any(node.get("type") == "separator" for node in upgraded):
            return upgraded
        result: list[dict] = []
        previous_section = ""
        for node in upgraded:
            section = str(node.get("section") or "")
            if result and section and previous_section and section != previous_section:
                result.append({"type": "separator", "id": next_id(), "visible": True})
            result.append(node)
            if section:
                previous_section = section
        return result

    layout["nodes"] = upgrade(layout.get("nodes", []))
    return layout


def merge_default_menu_actions(
    raw_layout: Mapping, *, registered_actions: Collection[str]
) -> tuple[dict, tuple[str, ...]]:
    """Return an editable layout containing actions added by newer defaults."""
    merged = deepcopy(dict(raw_layout))
    nodes = merged.get("nodes")
    if (
        merged.get("schema_version") != 1
        or merged.get("layout_id") != "user"
        or not isinstance(nodes, list)
    ):
        return merged, ()
    try:
        default_layout = load_default_menu_layout()
    except (OSError, ValueError, json.JSONDecodeError):
        return merged, ()
    normalization: list[str] = []
    _merge_future_default_actions(
        nodes,
        default_layout.get("nodes", []),
        registered=set(registered_actions),
        normalization=normalization,
    )
    return merged, tuple(normalization)


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

    working_layout = deepcopy(dict(raw_layout))
    if source == "user":
        working_layout, migration_diagnostics = merge_default_menu_actions(
            working_layout, registered_actions=registered
        )
        normalization.extend(migration_diagnostics)
    working_nodes = working_layout.get("nodes", [])

    def resolve_nodes(nodes) -> tuple[dict, ...]:
        resolved: list[dict] = []
        for node in nodes if isinstance(nodes, list) else ():
            if not isinstance(node, dict) or not node.get("visible", True):
                continue
            node_type = node.get("type")
            if node_type == "separator":
                # Separators are explicit layout nodes. The renderer normalizes
                # leading/trailing/consecutive dividers after capability filters.
                resolved.append({
                    "type": "separator",
                    "id": str(node.get("id") or ""),
                    "visible": True,
                })
                continue
            if node_type == "action":
                action_id = str(node.get("id") or "")
                if action_id not in registered:
                    normalization.append(f"unknown-action:{action_id}")
                    continue
                if action_id in registered and action_id in available:
                    action = {"type": "action", "id": action_id, "visible": True}
                    if node.get("section"):
                        action["section"] = str(node["section"])
                    alias = str(node.get("alias") or "").strip()[:40]
                    if alias:
                        action["alias"] = alias
                    if "icon" in node:
                        action["icon"] = str(node.get("icon") or "none")[:40]
                    resolved.append(action)
                continue
            if node_type == "submenu":
                children = resolve_nodes(node.get("children", []))
                children = _normalize_separators(children)
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
                    alias = str(node.get("alias") or "").strip()[:40]
                    if alias:
                        submenu["alias"] = alias
                    if "icon" in node:
                        submenu["icon"] = str(node.get("icon") or "none")[:40]
                    resolved.append(submenu)
        return _normalize_separators(tuple(resolved))

    def _normalize_separators(nodes: tuple[dict, ...]) -> tuple[dict, ...]:
        normalized: list[dict] = []
        for node in nodes:
            if node.get("type") == "separator":
                if not normalized or normalized[-1].get("type") == "separator":
                    continue
            normalized.append(node)
        while normalized and normalized[-1].get("type") == "separator":
            normalized.pop()
        return tuple(normalized)

    resolved_nodes = list(resolve_nodes(working_nodes))

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
