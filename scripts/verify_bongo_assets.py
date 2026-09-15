# -*- coding: utf-8 -*-
"""键鼠跟随模式的 BongoCat 模型素材校验器。

用途有三处：

1. 用户上传/替换素材后自查（尺寸、命名、透明通道、缺件）；
2. 开发时校验 `<数据目录>/bongocat/models/<model>/` 覆盖层；
3. CI 校验随包内置的 `external/bongocat/assets/models/**`。

素材契约（与原版 BongoCat 一致）：

* 模型目录含一个 `*.model3.json`，其 `FileReferences` 引用的 moc3 / 贴图 /
  表情 / 动作文件都必须存在；
* 贴图放在 `<name>.<宽>/texture_XX.png`：目录名后缀是**贴图宽度**（原版三套模型
  都是 `.1024` = 宽 1024、高 512），同一模型的贴图必须同尺寸；
* `resources/background.png`、`resources/cover.png` 为可选的窗口背景/封面；
* `resources/left-keys|right-keys/<Key>.png` 是按键覆盖图：同一目录内必须
  同尺寸；缺目录/缺键只警告（BongoCat 对缺键就是不显示覆盖图，且 `standard`
  模型本身就没有 `right-keys`），`--strict` 时升级为错误。

用法::

    python scripts/verify_bongo_assets.py --dir <模型目录>
    python scripts/verify_bongo_assets.py --runtime <运行时目录>
    python scripts/verify_bongo_assets.py --runtime <运行时目录> --strict --json

退出码：0 = 无 error（strict 下无 warning 视作失败）；1 = 有 error。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:  # Pillow 是运行期依赖；缺失时只跳过尺寸/透明通道检查
    from PIL import Image
except Exception:  # pragma: no cover - 仅在极端环境触发
    Image = None


MODEL_NAMES = ("standard", "keyboard", "gamepad")
TEXTURE_SIZE_DIR_PREFIX = "."

# 原版预置模型的键覆盖图清单（缺件按警告处理）。键名来源：上游 keyboard 预置模型；
# gamepad 模型用的是手柄键名，未知模型不做键名覆盖检查。
KEYBOARD_LEFT_KEYS = (
    "Alt", "AltGr", "BackQuote", "Backspace", "CapsLock", "Control", "ControlLeft",
    "ControlRight", "Delete", "Escape", "Fn",
    *[f"Key{letter}" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"],
    "Meta", *[f"Num{digit}" for digit in "0123456789"],
    "Return", "Shift", "ShiftLeft", "ShiftRight", "Slash", "Space", "Tab",
)
KEYBOARD_RIGHT_KEYS = ("DownArrow", "LeftArrow", "RightArrow", "UpArrow")
GAMEPAD_LEFT_KEYS = ("DPadDown", "DPadLeft", "DPadRight", "DPadUp", "LeftTrigger", "LeftTrigger2")
GAMEPAD_RIGHT_KEYS = ("East", "North", "RightTrigger", "RightTrigger2", "South", "West")

# model 目录名 → (left-keys 期望, right-keys 期望)；None = 不做键名覆盖检查。
EXPECTED_KEYS_BY_MODEL: dict[str, tuple[tuple[str, ...] | None, tuple[str, ...] | None]] = {
    "standard": (KEYBOARD_LEFT_KEYS, None),
    "keyboard": (KEYBOARD_LEFT_KEYS, KEYBOARD_RIGHT_KEYS),
    "gamepad": (GAMEPAD_LEFT_KEYS, GAMEPAD_RIGHT_KEYS),
}

ERROR = "error"
WARN = "warn"


def _ensure_utf8_console() -> None:
    """Windows 控制台可能是 cp1252/GBK：中文报告会 UnicodeEncodeError。

    CI/打包脚本靠 PYTHONUTF8 掩盖了这个问题，用户直接手敲命令时会炸，
    所以在脚本里自己把标准输出改成 UTF-8（与仓库 issue #26 的编码纪律一致）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


@dataclass
class Finding:
    level: str
    code: str
    message: str
    path: str = ""


@dataclass
class Report:
    models: dict = field(default_factory=dict)

    def add(self, model: str, level: str, code: str, message: str, path="") -> None:
        self.models.setdefault(model, []).append(
            Finding(level, code, message, str(path) if path else "")
        )

    def findings(self, model: str | None = None) -> list[Finding]:
        if model is not None:
            return list(self.models.get(model, ()))
        return [item for items in self.models.values() for item in items]

    @property
    def errors(self) -> list[Finding]:
        return [item for item in self.findings() if item.level == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [item for item in self.findings() if item.level == WARN]


def _image_size(path: Path) -> tuple[int, int] | None:
    if Image is None:
        return None
    try:
        with Image.open(path) as image:
            return tuple(image.size)
    except Exception:
        return None


def _has_alpha(path: Path) -> bool:
    if Image is None:
        return True
    try:
        with Image.open(path) as image:
            return image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
    except Exception:
        return True


def _declared_texture_width(directory_name: str) -> int | None:
    """从贴图目录名（如 ``demomodel.1024``）解析约定的贴图宽度。"""
    _, dot, suffix = directory_name.rpartition(TEXTURE_SIZE_DIR_PREFIX)
    if not dot or not suffix.isdigit():
        return None
    width = int(suffix)
    return width if 64 <= width <= 8192 else None


def discover_model_dirs(root: Path) -> list[Path]:
    """`root` 自身是模型目录则返回它，否则返回其下的模型子目录。

    两者都不是时仍返回 `root` 本身：这样「模型目录缺 *.model3.json」能被
    报成明确错误，而不是静默变成「没找到模型」。
    """
    root = Path(root)
    if any(root.glob("*.model3.json")):
        return [root]
    found = [
        child for child in sorted(root.iterdir())
        if child.is_dir() and any(child.glob("*.model3.json"))
    ] if root.is_dir() else []
    if found:
        return found
    return [root] if root.is_dir() else []


def verify_model_dir(model_dir: Path, report: Report, *, model_name: str | None = None) -> None:
    model_dir = Path(model_dir)
    model = model_name or model_dir.name
    model_files = sorted(model_dir.glob("*.model3.json"))
    if not model_files:
        report.add(model, ERROR, "missing-model3-json", "模型目录缺少 *.model3.json", model_dir)
        return
    model_json = model_files[0]
    try:
        data = json.loads(model_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.add(model, ERROR, "invalid-model3-json", f"model3.json 无法解析：{exc}", model_json)
        return
    references = data.get("FileReferences") if isinstance(data, dict) else None
    if not isinstance(references, dict):
        report.add(model, ERROR, "invalid-model3-json", "model3.json 缺 FileReferences", model_json)
        return

    _check_references(model, model_dir, references, report)
    _check_textures(model, model_dir, references, report)
    _check_resources(model, model_dir, report)


def _check_references(model: str, model_dir: Path, references: dict, report: Report) -> None:
    files: list[str] = []
    moc = references.get("Moc")
    if isinstance(moc, str) and moc:
        files.append(moc)
    textures = references.get("Textures")
    if isinstance(textures, list):
        files.extend(str(item) for item in textures if isinstance(item, str))
    for expression in references.get("Expressions") or ():
        if isinstance(expression, dict) and isinstance(expression.get("File"), str):
            files.append(expression["File"])
    motions = references.get("Motions")
    if isinstance(motions, dict):
        for entries in motions.values():
            for entry in entries or ():
                if not isinstance(entry, dict):
                    continue
                if isinstance(entry.get("File"), str):
                    files.append(entry["File"])
                if isinstance(entry.get("Sound"), str):
                    files.append(entry["Sound"])
    if not files:
        report.add(model, ERROR, "empty-model3-json", "model3.json 未引用任何文件", model_dir)
        return
    for relative in files:
        if not (model_dir / relative).is_file():
            report.add(
                model, ERROR, "missing-reference",
                f"model3.json 引用的文件不存在：{relative}", model_dir / relative,
            )


def _check_textures(model: str, model_dir: Path, references: dict, report: Report) -> None:
    textures = references.get("Textures")
    if not isinstance(textures, list) or not textures:
        report.add(model, ERROR, "no-textures", "model3.json 未声明贴图", model_dir)
        return
    sizes: set[tuple[int, int]] = set()
    for item in textures:
        if not isinstance(item, str):
            continue
        path = model_dir / item
        if not path.is_file():
            continue  # missing-reference 已报告
        size = _image_size(path)
        expected_width = _declared_texture_width(path.parent.name)
        if size is None:
            report.add(model, WARN, "unreadable-texture", f"贴图无法读取：{item}", path)
            continue
        sizes.add(size)
        if expected_width is not None and size[0] != expected_width:
            report.add(
                model, ERROR, "texture-size-mismatch",
                f"贴图 {item} 宽 {size[0]}，目录名约定宽度 {expected_width}",
                path,
            )
        if not _has_alpha(path):
            report.add(model, WARN, "texture-no-alpha", f"贴图 {item} 无透明通道", path)
    if len(sizes) > 1:
        report.add(
            model, WARN, "texture-size-inconsistent",
            f"同一模型的贴图尺寸不一致：{sorted(sizes)}（重绘替换时容易错位）",
            model_dir,
        )


def _check_resources(model: str, model_dir: Path, report: Report) -> None:
    resources = model_dir / "resources"
    for name in ("background.png", "cover.png"):
        path = resources / name
        if not path.is_file():
            report.add(model, WARN, "missing-resource", f"缺少 {resources.name}/{name}", path)
            continue
        if not _has_alpha(path):
            report.add(model, WARN, "resource-no-alpha", f"{name} 无透明通道", path)
    expected_left, expected_right = EXPECTED_KEYS_BY_MODEL.get(model, (None, None))
    for group, expected in (("left-keys", expected_left), ("right-keys", expected_right)):
        _check_key_group(model, resources / group, group, expected, report)


def _check_key_group(
    model: str, group_dir: Path, group: str, expected: tuple[str, ...] | None, report: Report
) -> None:
    if not group_dir.is_dir():
        # `standard` 预置模型本身就没有 right-keys：缺目录只作提示，不算问题。
        report.add(
            model, WARN, "missing-key-group",
            f"缺少按键覆盖图目录 resources/{group}", group_dir,
        )
        return
    images = sorted(path for path in group_dir.iterdir() if path.is_file())
    if not images:
        report.add(
            model, WARN, "missing-key-overlay",
            f"resources/{group} 为空；缺少整套按键覆盖图", group_dir,
        )
        return
    sizes: set[tuple[int, int]] = set()
    names: set[str] = set()
    for path in images:
        if path.suffix.lower() != ".png":
            report.add(
                model, ERROR, "key-overlay-not-png",
                f"按键覆盖图必须为 PNG：resources/{group}/{path.name}", path,
            )
            continue
        names.add(path.stem)
        size = _image_size(path)
        if size is None:
            report.add(
                model, WARN, "unreadable-key-overlay",
                f"按键覆盖图无法读取：resources/{group}/{path.name}", path,
            )
            continue
        sizes.add(size)
        if not _has_alpha(path):
            report.add(
                model, WARN, "key-overlay-no-alpha",
                f"按键覆盖图无透明通道：resources/{group}/{path.name}", path,
            )
    if len(sizes) > 1:
        report.add(
            model, ERROR, "key-overlay-size-inconsistent",
            f"resources/{group} 内覆盖图尺寸不一致：{sorted(sizes)}", group_dir,
        )
    if expected is None:
        return
    missing = [key for key in expected if key not in names]
    if missing:
        report.add(
            model, WARN, "missing-key-overlay",
            f"resources/{group} 缺少 {len(missing)} 个键覆盖图：{'、'.join(missing[:8])}"
            + ("…" if len(missing) > 8 else ""),
            group_dir,
        )


def verify(root: Path, *, model: str | None = None) -> tuple[Report, list[Path]]:
    report = Report()
    dirs = discover_model_dirs(Path(root))
    if model:
        wanted = str(model)
        selected = [item for item in dirs if item.name == wanted or f"{wanted}.model3.json" in {
            entry.name for entry in item.glob("*.model3.json")
        }]
        dirs = selected or dirs
    for model_dir in dirs:
        verify_model_dir(model_dir, report, model_name=model_dir.name)
    return report, dirs


def format_report(report: Report, dirs: list[Path], *, strict: bool = False) -> list[str]:
    lines = [f"模型目录：{len(dirs)} 个"]
    for model in report.models:
        lines.append(f"[{model}]")
        for finding in report.models[model]:
            mark = "ERROR" if finding.level == ERROR else "WARN "
            location = f"  ({finding.path})" if finding.path else ""
            lines.append(f"  {mark} {finding.code}: {finding.message}{location}")
        if not report.models[model]:
            lines.append("  OK 无问题")
    lines.append(
        f"合计：{len(report.errors)} 个错误，{len(report.warnings)} 个警告"
        + ("（--strict：警告按错误处理）" if strict else "")
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_console()
    parser = argparse.ArgumentParser(description="BongoCat 模型素材校验（键鼠跟随模式）")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--dir", help="模型目录（含 *.model3.json）")
    target.add_argument("--runtime", help="运行时目录（校验其 assets/models/*）")
    parser.add_argument("--model", help="只校验指定模型名")
    parser.add_argument("--strict", action="store_true", help="警告也视为失败")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    args = parser.parse_args(argv)

    root = Path(args.dir) if args.dir else Path(args.runtime) / "assets" / "models"
    if not root.exists():
        print(f"目录不存在：{root}")
        return 1
    report, dirs = verify(root, model=args.model)
    if not dirs:
        print(f"未找到任何模型目录（含 *.model3.json）：{root}")
        return 1
    if args.json:
        payload = {
            "root": str(root),
            "models": {
                model: [
                    {"level": item.level, "code": item.code, "message": item.message, "path": item.path}
                    for item in items
                ]
                for model, items in report.models.items()
            },
            "errors": len(report.errors),
            "warnings": len(report.warnings),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("\n".join(format_report(report, dirs, strict=args.strict)))
    if report.errors:
        return 1
    if args.strict and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
