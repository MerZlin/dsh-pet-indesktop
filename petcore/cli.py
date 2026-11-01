from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, paths
from .config import Config
from .log import get_logger, setup_logging
from .performance import runtime_metrics

#: 内置「每日一句」池的最小条数：生成脚本、设置页与构建门共用这一口径。
MIN_DAILY_LINES = 1000


def _ensure_streams() -> None:
    """窗口化打包（PyInstaller --windowed）下 sys.stdout/stderr 是 None。

    CLI 子命令仍要能跑（`petcore mods list`、`--check`），否则第一次 print 就
    抛 AttributeError；这里接到 devnull，等价于「静默丢弃控制台输出」。
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is not None:
            continue
        try:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
        except OSError:
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="petcore", description="dsh-pet core")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument("--check", action="store_true", help="headless self check")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="--check 的构建门模式：菜单模板/内置配图/每日台词池/角色缺失即退出 1",
    )
    parser.add_argument("--out", type=Path, default=None, help="write --check report to this path")
    parser.add_argument("--character", default=None)
    parser.add_argument("--scale", type=float, default=None)
    parser.add_argument("--mods-dir", action="append", default=[])
    parser.add_argument("--portable", action="store_true")
    parser.add_argument("--instance", default=None, help="isolated data root, e.g. pet2")
    parser.add_argument("--frameseq", choices=("auto", "off", "only"), default=None)
    parser.add_argument("--screen", default=None)
    parser.add_argument("--allow-feature-mods", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--root", type=Path, default=None, help="data root (default: user data dir)")
    parser.add_argument("command", nargs="?", default=None)
    return parser


def _value_options(parser: argparse.ArgumentParser) -> frozenset[str]:
    options: set[str] = set()
    for action in parser._actions:  # noqa: SLF001 - argparse exposes no public enumeration
        if action.option_strings and action.nargs != 0:
            options.update(action.option_strings)
    return frozenset(options)


def _subcommand_index(argv: list[str], names: tuple[str, ...]) -> int | None:
    """Locate a subcommand token that is not the value of a preceding option."""
    value_options = _value_options(_parser())
    skip_value = False
    for index, token in enumerate(argv):
        if skip_value:
            skip_value = False
            continue
        if token in names:
            return index
        if token in value_options:
            skip_value = True
    return None


def _menu_ids(nodes) -> set[str]:
    found: set[str] = set()
    for node in nodes if isinstance(nodes, list) else ():
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        if node.get("type") in {"action", "submenu"} and node_id:
            found.add(node_id)
        found.update(_menu_ids(node.get("children", [])))
    return found


def _speech_report(root: Path, config: Config) -> dict:
    """`--check` 的语音证据：后端、缓存目录与每家 provider 的可用性。

    冻结包里 `edge_tts`/`winrt` 是惰性 import，静态分析收不全；这里用
    ``importlib.util.find_spec`` 探模块是否真的随包存在（不 import 本体）。
    """
    try:
        from .api.credentials import KeyringCredentials
        from .speech.service import SpeechService

        service = SpeechService(root, config=config, credentials=KeyringCredentials())
        return {
            "backend": service.backend(),
            "fallback": service.fallback(),
            "cache_dir": str(service.cache_dir()),
            "cached_files": service.cache.count(),
            "providers": [
                {
                    "id": item.id,
                    "online": bool(item.online),
                    "available": bool(item.available),
                    "reason": str(item.reason or ""),
                }
                for item in service.providers()
            ],
        }
    except Exception as exc:  # noqa: BLE001 - 诊断不能把 --check 拖垮
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


def _self_talk_daily_report(path: Path | None = None) -> dict:
    """`--check` 的每日一句证据：随包台词池是否真的随安装包存在。

    冻结包整棵 ``petcore/builtin`` 一起拷；池文件缺失时运行时只会静默回落
    普通自语，所以必须有构建门（``--strict``）把它变成红的。
    """
    target = path or (
        paths.builtin_features_dir() / "fun.self-talk" / "daily_lines.json"
    )
    payload: object = {}
    try:
        payload = json.loads(Path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    raw = payload.get("lines") if isinstance(payload, dict) else None
    lines = [
        str(item).strip()
        for item in (raw if isinstance(raw, list) else ())
        if isinstance(item, str) and str(item).strip()
    ]
    return {
        "path": str(target),
        "schema": payload.get("schema") if isinstance(payload, dict) else None,
        "generated": str((payload or {}).get("generated") or "")
        if isinstance(payload, dict)
        else "",
        "count": len(lines),
        "unique": len(set(lines)),
    }


def _migration_report(root: Path) -> dict:
    """`--check` 的迁移证据：完成标记与备份目录，只读，不跑迁移。"""
    from . import migration

    marker = migration.read_marker(root)
    backup_root = root / migration.BACKUP_DIRNAME
    try:
        backups = sorted(path.name for path in backup_root.iterdir() if path.is_file())
    except OSError:
        backups = []
    return {
        "marker": bool(marker),
        "completed": dict(marker.get("completed") or {}),
        "restore_entries": len(marker.get("restore") or []),
        "backup_dir": str(backup_root),
        "backups": backups,
    }


def _check(
    root: Path,
    config: Config,
    mod_dirs: tuple[Path, ...],
    out: Path | None = None,
    *,
    strict: bool = False,
) -> int:
    from .content.collect import collect_characters
    from .contracts import capabilities
    from .ui import menu_layout
    from .update import UpdateService

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    characters, result = collect_characters(
        root=root,
        mod_dirs=mod_dirs,
        frameseq_mode=str(config.get("frameseq") or "auto"),
        allow_feature_mods=bool(config.get("allow_feature_mods")),
    )
    character_report = []
    for character in characters:
        backends: dict[str, int] = {}
        for animation in character.animations:
            if animation.frameseq_dir is not None:
                kind = "frameseq"
            elif animation.path is not None:
                kind = animation.path.suffix.lower().lstrip(".") or "unknown"
            else:
                kind = "none"
            backends[kind] = backends.get(kind, 0) + 1
        character_report.append(
            {
                "id": character.id,
                "name": character.name,
                "owner": character.owner,
                "animations": len(character.animations),
                "backends": backends,
                "body_box": character.body_box,
                "head_box": character.head_box,
            }
        )
    mod_report = [
        {
            "id": mod.id,
            "kind": mod.kind,
            "name": mod.manifest.name,
            "version": mod.manifest.version,
            "enabled": mod.enabled,
            "path": str(mod.path),
        }
        for mod in result.mods
    ]
    # Headless evidence for the packaged menu: `menu_template` is "builtin" when
    # petcore/menu_templates did not ship with the build.
    menu_template, menu_template_source = menu_layout.load_default_with_source()
    menu_ids = _menu_ids(menu_template.get("nodes", []))
    menu_resolved = menu_layout.resolve(None, registered=menu_ids, available=menu_ids)
    from .media import talk_images

    configured_dir = talk_images.resolve_image_dir(config.get("self_talk_image_dir"))
    builtin_dir = talk_images.builtin_image_dir()
    report = {
        "version": __version__,
        "data_root": str(root),
        "cache_root": str(paths.cache_root() or root),
        "config": config.snapshot(),
        "config_corrupt_backup": str(getattr(config, "corrupt_backup", "") or ""),
        "active_pets": list(config.get("active_pets") or [0]),
        "pet_configs": {
            str(slot): str(paths.pet_config_path(root, slot))
            for slot in (config.get("active_pets") or [0])
        },
        "mod_dirs": [str(path) for path in mod_dirs],
        "legacy_characters": str(paths.legacy_characters_root()),
        "mods": mod_report,
        "characters": character_report,
        "diagnostics": list(result.diagnostics),
        "menu_template": menu_template_source,
        "menu_layout_diagnostics": list(menu_resolved.diagnostics[:20]),
        "self_talk_images": {
            "dir": str(configured_dir),
            "count": len(talk_images.image_files(configured_dir)),
            "readable": len(talk_images.readable_images(configured_dir)),
            "builtin_dir": str(builtin_dir),
            "builtin_count": len(talk_images.image_files(builtin_dir)),
            "builtin_readable": len(talk_images.readable_images(builtin_dir)),
        },
        "self_talk_daily": _self_talk_daily_report(),
        "speech": _speech_report(root, config),
        "update": UpdateService(root).diagnose(),
        # 运行时计数器只写不读就是死代码：`--check` 是它们的读出口。
        "performance": runtime_metrics.snapshot().as_dict(),
        "migration": _migration_report(root),
        # 能力声明是「宏大的纸面权力」时最容易腐烂的地方：至少让 `--check` 有读出口。
        "capabilities": sorted(capabilities.CAPABILITIES),
    }
    problems: list[str] = []
    if strict:
        # 构建门的唯一判据：这几项就是「包打全了没有」的证据（AGENTS 已写明
        # `self_talk_images` 是素材/Qt 插件是否正常的第一证据）。
        if menu_template_source != "json":
            problems.append(f"menu_template={menu_template_source}")
        if int(report["self_talk_images"].get("builtin_readable") or 0) <= 0:
            problems.append("builtin self-talk images unreadable")
        # 每日一句池缺失时运行时只会静默回落普通自语，必须在这里变红。
        if int(report["self_talk_daily"].get("unique") or 0) < MIN_DAILY_LINES:
            problems.append(
                "builtin self-talk daily lines missing "
                f"({report['self_talk_daily'].get('unique') or 0}"
                f" < {MIN_DAILY_LINES})"
            )
        if not report.get("characters"):
            problems.append("no characters packaged")
        problems.extend(f"diagnostic:{item}" for item in (report.get("diagnostics") or []))
    report["strict"] = {"enabled": bool(strict), "problems": problems}
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    return 1 if problems else 0


def _resolve_root(args: argparse.Namespace) -> Path:
    # `--root ""` 在 Windows 上等于 `Path(".")`，会把数据根变成当前工作目录。
    raw = getattr(args, "root", None)
    explicit = None
    if raw is not None and str(raw).strip():
        explicit = Path(str(raw).strip())
    elif getattr(args, "root", None) is not None:
        logger = get_logger("cli")
        logger.warning("--root 是空字符串，按未指定处理")
    roots = paths.resolve_roots(
        portable=bool(getattr(args, "portable", False)),
        instance=getattr(args, "instance", None),
        explicit=explicit,
    )
    paths.set_cache_root(roots.cache_root, data_root=roots.data_root)
    return roots.data_root


def main(argv: list[str] | None = None) -> int:
    _ensure_streams()
    raw = list(sys.argv[1:] if argv is None else argv)

    mods_index = None
    if raw[:1] not in (["pack-character"], ["pack-legacy"], ["autostart"], ["provision"]):
        mods_index = _subcommand_index(raw, ("mods",))
    if mods_index is not None:
        from .mods.cli import main as mods_main

        global_args, _extra_args = _parser().parse_known_args(raw[:mods_index])
        try:
            root = _resolve_root(global_args)
        except ValueError as exc:
            print(str(exc))
            return 2
        tokens: list[str] = []
        if raw[:mods_index]:
            tokens.extend(["--root", str(root)])
        for item in global_args.mods_dir:
            tokens.extend(["--mods-dir", item])
        return mods_main(tokens + raw[mods_index + 1 :])

    market_index = None
    if raw[:1] not in (["pack-character"], ["pack-legacy"], ["autostart"], ["provision"]):
        market_index = _subcommand_index(raw, ("market",))
    if market_index is not None:
        from .market.cli import main as market_main

        global_args, _extra_args = _parser().parse_known_args(raw[:market_index])
        try:
            root = _resolve_root(global_args)
        except ValueError as exc:
            print(str(exc))
            return 2
        tokens: list[str] = []
        if raw[:market_index]:
            tokens.extend(["--root", str(root)])
        tokens.extend(str(item) for item in _extra_args)
        return market_main(tokens + raw[market_index + 1 :])

    update_index = _subcommand_index(raw, ("update",))
    if update_index is not None:
        from .update import UpdateService

        global_args, _extra_args = _parser().parse_known_args(raw[:update_index])
        try:
            root = _resolve_root(global_args)
        except ValueError as exc:
            print(str(exc))
            return 2
        parser = argparse.ArgumentParser(prog="petcore update")
        parser.add_argument("action", choices=("check", "download", "status"))
        parser.add_argument("--manifest-url", default=None)
        update_args = parser.parse_args(raw[update_index + 1 :])
        service = UpdateService(root, manifest_url=update_args.manifest_url)
        if update_args.action == "status":
            payload = dict(service.diagnose())
            status = service.status()
            payload.update({"state": status.state, "message": status.message, "version": status.version})
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        result = service.check(force=update_args.action == "check")
        if update_args.action == "download":
            asset = service.select_asset(result.manifest)
            if not result.ok or asset is None:
                print(result.message or "没有可用更新资产")
                return 1
            try:
                path = service.download(asset)
            except Exception as exc:  # noqa: BLE001 - CLI boundary
                print(f"下载失败：{type(exc).__name__}")
                return 1
            print(str(path))
            return 0
        print(json.dumps({
            "ok": result.ok,
            "state": result.state,
            "message": result.message,
            "version": result.version,
            "asset_available": service.select_asset(result.manifest) is not None,
        }, ensure_ascii=False, indent=2))
        return 0 if result.ok else 1

    migrate_index = _subcommand_index(raw, ("migrate",))
    if migrate_index is not None:
        from . import migration

        global_args, _extra_args = _parser().parse_known_args(raw[:migrate_index])
        try:
            root = _resolve_root(global_args)
        except ValueError as exc:
            print(str(exc))
            return 2
        parser = argparse.ArgumentParser(prog="petcore migrate")
        parser.add_argument("--dry-run", action="store_true", help="只预演，不写文件")
        parser.add_argument("--force", action="store_true", help="忽略完成标记重跑")
        parser.add_argument("--rollback", action="store_true", help="按备份恢复并清空完成标记")
        parser.add_argument("--legacy-root", type=Path, default=None, help="指定旧壳数据目录")
        migrate_args = parser.parse_args(raw[migrate_index + 1 :])
        if migrate_args.rollback:
            report = migration.rollback(root)
        else:
            report = migration.run(
                root,
                legacy_root=migrate_args.legacy_root,
                dry_run=migrate_args.dry_run,
                force=migrate_args.force,
            )
        payload = report.as_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if report.failures else 0

    if raw[:1] == ["pack-character"]:
        parser = argparse.ArgumentParser(prog="petcore pack-character")
        parser.add_argument("source", type=Path)
        parser.add_argument("--out", required=True, type=Path)
        parser.add_argument("--id", default=None)
        parser.add_argument("--name", default=None)
        parser.add_argument("--version", default="1.0.0")
        parser.add_argument("--copy", action="store_true")
        pack_args = parser.parse_args(raw[1:])
        from .content.character_pack import build_character_pack

        ok, message = build_character_pack(
            pack_args.source,
            pack_args.out,
            character_id=pack_args.id,
            name=pack_args.name,
            version=pack_args.version,
            link=not pack_args.copy,
        )
        print(message)
        return 0 if ok else 1

    if raw[:1] == ["pack-legacy"]:
        parser = argparse.ArgumentParser(prog="petcore pack-legacy")
        parser.add_argument("character")
        parser.add_argument("--out", required=True)
        parser.add_argument("--copy", action="store_true", help="copy files instead of hardlinks")
        pack_args = parser.parse_args(raw[1:])
        from .content.pack import pack_legacy

        ok, message = pack_legacy(pack_args.character, Path(pack_args.out), link=not pack_args.copy)
        print(message)
        return 0 if ok else 1

    if raw[:1] == ["autostart"]:
        from .platform import autostart

        action = raw[1] if len(raw) > 1 else "status"
        if action == "status":
            print("on" if autostart.is_enabled() else "off")
            return 0
        if action == "on":
            ok = autostart.enable()
            print("enabled" if ok else "failed")
            return 0 if ok else 1
        if action == "off":
            ok = autostart.disable()
            print("disabled" if ok else "failed")
            return 0 if ok else 1
        print("usage: petcore autostart on|off|status")
        return 1

    if raw[:1] == ["provision"]:
        parser = argparse.ArgumentParser(prog="petcore provision")
        parser.add_argument("character", nargs="?", default="shenshen")
        parser.add_argument("--budget", type=float, default=60.0)
        parser.add_argument("--migration-clips", type=int, default=1)
        prov_args = parser.parse_args(raw[1:])
        from .content.legacy import load_character
        from .media.provision import provision_character

        character = load_character(prov_args.character, frameseq_mode="off")
        if character is None:
            print(f"unknown or empty character: {prov_args.character}")
            return 1
        report = provision_character(
            character,
            budget_seconds=prov_args.budget,
            migration_clips=prov_args.migration_clips,
        )
        if report is None:
            print("provision unavailable (no webm or frameseq pipeline)")
            return 1
        for field in (
            "converted",
            "skipped",
            "failed",
            "retired",
            "locked",
            "idle",
            "deferred_budget",
            "deferred_backoff",
            "deferred_disk",
        ):
            print(f"{field}: {getattr(report, field, 0)}")
        return 0

    args, _extra = _parser().parse_known_args(raw)
    if args.version:
        print(__version__)
        return 0
    try:
        root = _resolve_root(args)
    except ValueError as exc:
        print(str(exc))
        return 2
    if args.instance:
        # 缩略图等进程级缓存按实例隔离（`_disk_cache_dir()` 读这个变量）。
        os.environ.setdefault("PETCORE_INSTANCE", str(args.instance))
    setup_logging(paths.logs_dir(root), verbose=args.verbose)
    logger = get_logger("cli")
    # `--check` 是诊断命令，必须只读：它会算出与正常启动一致的合并结果，但不备份、
    # 不写 config.json、不迁 slot（否则一次排障就改了用户数据并绕开迁移标记）。
    config = Config.load(root, portable=args.portable, persist=not args.check)
    if args.scale is not None:
        config.set("scale", args.scale)
    if args.character:
        config.set("character", args.character)
    if args.frameseq is not None:
        config.set("frameseq", args.frameseq)
    if args.screen:
        config.set("screen_name", args.screen)
    if args.allow_feature_mods:
        config.set("allow_feature_mods", True)
    extra = tuple(Path(item) for item in args.mods_dir)
    mod_dirs = paths.mods_dirs(root, extra)
    if args.check:
        return _check(root, config, mod_dirs, args.out, strict=bool(args.strict))
    logger.info("starting petcore %s", __version__)
    from .ui.app import run

    return run(
        config,
        root=root,
        mod_dirs=mod_dirs,
        instance=args.instance,
        cache_root=paths.cache_root() or root,
    )
