"""从 config.py 的 normalize 代码生成设置元数据的初稿（构建期，不运行期）。"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "petcore" / "config.py"
OUT = ROOT / "petcore" / "settings_schema_ranges.py"


def _literal(node):
    return node.value if isinstance(node, ast.Constant) else None


def _key_from_get(node) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
        return None
    if node.args and isinstance(node.args[0], ast.Constant):
        return str(node.args[0].value)
    return None


def main() -> int:
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    ranges: dict[str, dict] = {}
    kinds: dict[str, str] = {}
    choices: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)):
            continue
        key = str(target.slice.value)
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        fname = getattr(value.func, "id", None) or getattr(value.func, "attr", None)
        if fname not in {
            "_number",
            "_optional_number",
            "_bool",
            "_text",
            "_optional_text",
            "_text_list",
        }:
            continue
        first_key = _key_from_get(value.args[0]) if value.args else None
        if first_key != key:
            continue
        if fname in {"_number", "_optional_number"}:
            minimum = _literal(value.args[2]) if len(value.args) > 2 else None
            maximum = _literal(value.args[3]) if len(value.args) > 3 else None
            entry = {}
            if isinstance(minimum, (int, float)):
                entry["min"] = float(minimum)
            if isinstance(maximum, (int, float)):
                entry["max"] = float(maximum)
            if entry:
                ranges[key] = entry
        elif fname == "_bool":
            kinds[key] = "bool"
        elif fname == "_text_list":
            kinds[key] = "list"
        elif fname in {"_text", "_optional_text"}:
            kinds[key] = "str"
        # 枚举：`x in {"a","b"}` / `x not in {...}`
        for compare in ast.walk(value):
            if not isinstance(compare, ast.Compare) or len(compare.ops) != 1:
                continue
            if not isinstance(compare.ops[0], (ast.In, ast.NotIn)):
                continue
            if not isinstance(compare.comparators[0], (ast.Set, ast.Tuple, ast.List)):
                continue
            values = [str(item.value) for item in compare.comparators[0].elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
            if len(values) >= 2:
                choices.setdefault(key, values)
    OUT.write_text(
        "# 由 scripts/gen_settings_meta.py 生成；只收录数值范围/类型/枚举，不收录默认值。\n"
        "RANGES = " + json.dumps(ranges, ensure_ascii=False, indent=2) + "\n\n"
        "KINDS = " + json.dumps(kinds, ensure_ascii=False, indent=2) + "\n\n"
        "CHOICES = " + json.dumps(choices, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT}: {len(ranges)} ranges, {len(kinds)} kinds, {len(choices)} choices")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
