"""`petcore settings`：给用户/外部 AI 的只读 schema 与受控写入。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import paths
from .config import Config
from .settings_schema import SETTINGS, as_dicts, spec_for
from .storage.config_lock import config_write_lock


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", default=argparse.SUPPRESS)
    parser.add_argument("--portable", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--instance", default=argparse.SUPPRESS)
    parser.add_argument("--out", default=argparse.SUPPRESS)
    return parser


def _parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="petcore settings",
        description="列出/读取/修改桌宠设置；所有键以 settings list 为准。",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="列出全部设置元数据", parents=[common])
    list_parser.add_argument("--scope", choices=("global", "pet"), default=None)
    list_parser.add_argument("--query", default="")
    list_parser.add_argument("--writable", action="store_true")
    # 输出本来就是 JSON；保留 --json 让提示词/脚本不用分两套。
    list_parser.add_argument("--json", action="store_true")

    describe = sub.add_parser("describe", help="查看一个键的元数据", parents=[common])
    describe.add_argument("key")

    get_parser = sub.add_parser("get", help="读取一个键", parents=[common])
    get_parser.add_argument("key")
    get_parser.add_argument("--slot", type=int, default=0)

    set_parser = sub.add_parser("set", help="修改一个键", parents=[common])
    set_parser.add_argument("key")
    set_parser.add_argument("value")
    set_parser.add_argument("--slot", type=int, default=0)
    set_parser.add_argument("--force", action="store_true")

    export = sub.add_parser("export", help="导出 schema JSON", parents=[common])
    export.add_argument("path", nargs="?")

    sub.add_parser("path", help="打印数据根与配置文件路径", parents=[common])
    return parser


def _resolve_root(args: argparse.Namespace) -> Path:
    explicit = None
    raw = getattr(args, "root", None)
    if raw is not None and str(raw).strip():
        explicit = Path(str(raw).strip())
    roots = paths.resolve_roots(
        portable=bool(getattr(args, "portable", False)),
        instance=getattr(args, "instance", None),
        explicit=explicit,
    )
    return roots.data_root


def _slot(args: argparse.Namespace) -> int:
    try:
        return max(0, int(getattr(args, "slot", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _config_file(root: Path, slot: int) -> Path:
    return Path(root) / ("config.json" if slot <= 0 else f"config-pet-{slot}.json")


def _load(root: Path, slot: int, *, persist: bool = True) -> Config:
    if slot <= 0:
        return Config.load(root, persist=persist)
    return Config.load(
        root, filename=f"config-pet-{slot}.json", persist=persist
    )


def _emit(payload: Any, args: argparse.Namespace) -> int:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    out = getattr(args, "out", None)
    if out and str(out) != "-":
        path = Path(str(out))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        print(str(path))
    else:
        print(text)
    return 0


def _parse_value(text: str, spec) -> tuple[bool, Any, str]:
    raw = str(text)
    kind = spec.type
    try:
        if kind == "bool":
            lowered = raw.strip().lower()
            if lowered in {"1", "true", "yes", "on", "是", "开"}:
                return True, True, ""
            if lowered in {"0", "false", "no", "off", "否", "关"}:
                return True, False, ""
            return False, None, "需要 true/false"
        if kind == "int":
            return True, int(raw.strip()), ""
        if kind == "float":
            return True, float(raw.strip()), ""
        if kind == "list":
            value = json.loads(raw)
            if not isinstance(value, list):
                return False, None, "需要 JSON 数组"
            return True, value, ""
        if kind == "dict":
            value = json.loads(raw)
            if not isinstance(value, dict):
                return False, None, "需要 JSON 对象"
            return True, value, ""
        return True, raw, ""
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return False, None, f"值无法解析：{type(exc).__name__}"


def _check_value(spec, value: Any, *, force: bool) -> tuple[bool, str]:
    if spec.kind != "preference":
        return False, "这是状态/内部键，只读；请改对应的用户动作"
    if spec.choices and value not in spec.choices:
        return False, f"可选值：{', '.join(spec.choices)}"
    if spec.type in {"int", "float"}:
        number = float(value)
        if spec.min is not None and number < spec.min:
            if not force:
                return False, f"低于最小值 {spec.min}"
        if spec.max is not None and number > spec.max:
            if not force:
                return False, f"高于最大值 {spec.max}"
    return True, ""


def _cmd_list(args: argparse.Namespace, root: Path) -> int:
    query = str(getattr(args, "query", "") or "").strip().lower()
    items = as_dicts(writable_only=bool(getattr(args, "writable", False)))
    scope = getattr(args, "scope", None)
    result = []
    for item in items:
        if scope and item["scope"] != scope:
            continue
        if query:
            haystack = " ".join(
                str(item.get(key) or "")
                for key in ("key", "label_zh", "description", "group")
            ).lower()
            if query not in haystack:
                continue
        result.append(item)
    return _emit({"schema": 1, "count": len(result), "settings": result}, args)


def _cmd_describe(args: argparse.Namespace, root: Path) -> int:
    spec = spec_for(args.key)
    if spec is None:
        return _emit({"ok": False, "error": f"未知键：{args.key}"}, args) or 1
    return _emit({"schema": 1, "setting": spec.as_dict()}, args)


def _cmd_get(args: argparse.Namespace, root: Path) -> int:
    spec = spec_for(args.key)
    if spec is None:
        return _emit({"ok": False, "error": f"未知键：{args.key}"}, args) or 1
    slot = _slot(args)
    config = _load(root, slot, persist=False)
    return _emit(
        {
            "ok": True,
            "key": spec.key,
            "value": config.get(spec.key, spec.default),
            "scope": spec.scope,
            "slot": slot,
            "file": str(_config_file(root, slot if spec.scope == "pet" else 0)),
        },
        args,
    )


def _cmd_set(args: argparse.Namespace, root: Path) -> int:
    spec = spec_for(args.key)
    if spec is None:
        return _emit({"ok": False, "error": f"未知键：{args.key}"}, args) or 1
    ok, value, error = _parse_value(args.value, spec)
    if not ok:
        return _emit({"ok": False, "key": spec.key, "error": error}, args) or 1
    ok, error = _check_value(spec, value, force=bool(args.force))
    if not ok:
        return _emit({"ok": False, "key": spec.key, "error": error}, args) or 1
    slot = _slot(args)
    target_slot = 0 if spec.scope == "global" else slot
    target = _config_file(root, target_slot)
    try:
        with config_write_lock(root):
            config = _load(root, target_slot)
            config.set(spec.key, value)
            config.save()
            applied = config.get(spec.key, spec.default)
    except (OSError, TimeoutError) as exc:
        return _emit(
            {"ok": False, "key": spec.key, "error": type(exc).__name__},
            args,
        ) or 1
    return _emit(
        {
            "ok": True,
            "key": spec.key,
            "requested": value,
            "applied": applied,
            "clamped": applied != value,
            "scope": spec.scope,
            "slot": target_slot,
            "file": str(target),
            "restart_required": spec.restart_required,
        },
        args,
    )


def _cmd_export(args: argparse.Namespace, root: Path) -> int:
    payload = {
        "schema": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "count": len(SETTINGS),
        "settings": as_dicts(),
    }
    path = Path(args.path) if args.path else Path(root) / "settings-schema.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return _emit({"ok": True, "path": str(path), "count": len(SETTINGS)}, args)


def _cmd_path(args: argparse.Namespace, root: Path) -> int:
    return _emit(
        {
            "data_root": str(root),
            "config": str(root / "config.json"),
            "pet_configs": str(root / "config-pet-N.json"),
            "schema": str(root / "settings-schema.json"),
        },
        args,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return int(exc.code or 0)
    root = _resolve_root(args)
    commands = {
        "list": _cmd_list,
        "describe": _cmd_describe,
        "get": _cmd_get,
        "set": _cmd_set,
        "export": _cmd_export,
        "path": _cmd_path,
    }
    handler = commands.get(str(args.command))
    if handler is None:
        return 2
    return handler(args, root)


if __name__ == "__main__":
    raise SystemExit(main())
