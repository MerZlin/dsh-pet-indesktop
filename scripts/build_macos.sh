#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 默认用 PATH 上的 python3（源码环境）；可用 PYTHON_BIN 覆盖。
# 注：GitHub Actions 的 macOS 构建在 build-macos.yml 内联完成（输出 dist/），
# 本脚本面向本机构建（输出 build/macos/），两处逻辑可能漂移，改动需同步。
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || echo python3)}"
BUILD_DEPS="$ROOT/build/.build-deps"
DIST_DIR="$ROOT/build/macos"
WORK_DIR="$ROOT/build/.pyinstaller/macos"

cd "$ROOT"
export PYTHONPATH="$BUILD_DEPS${PYTHONPATH:+:$PYTHONPATH}"
export PYINSTALLER_CONFIG_DIR="$ROOT/build/.pyinstaller/config"

if [[ ! -d "$BUILD_DEPS/PyInstaller" ]]; then
    echo "缺少 $BUILD_DEPS/PyInstaller，请先安装本地构建依赖。" >&2
    exit 1
fi

"$PYTHON_BIN" scripts/make_icon.py --icns
"$PYTHON_BIN" scripts/convert_to_gif.py --clean

mkdir -p "$DIST_DIR" "$WORK_DIR"

variants=(
    "webm-chat|packaging/pet_entry.py|assets/characters|"
    "webm|packaging/pet_entry_no_chat.py|assets/characters|pet.chat,keyring"
    "gif-chat|packaging/pet_entry.py|assets/characters_gif|"
    "gif|packaging/pet_entry_no_chat.py|assets/characters_gif|pet.chat,keyring"
)

for spec in "${variants[@]}"; do
    IFS='|' read -r variant entry assets excludes <<< "$spec"
    name="dsh-pet-standalone-$variant"
    printf "VARIANT = '%s'\n" "$variant" > packaging/build_variant.py

    args=(
        --noconfirm
        --clean
        --onedir
        --windowed
        --paths .
        --distpath "$DIST_DIR"
        --workpath "$WORK_DIR"
        --name "$name"
        --icon assets/icon.icns
        --collect-all imageio_ffmpeg
        --collect-all certifi
        --add-data "$assets:$assets"
        --add-data "assets/big_blue_fat_fish:assets/big_blue_fat_fish"
        --add-data "assets/chat:assets/chat"
        --add-data "assets/sounds:assets/sounds"
        --add-data "pet/menu_templates:pet/menu_templates"
        --add-data "integrations:integrations"
    )
    if [[ "$name" == *-chat ]]; then
        args+=(--add-data "pet/chat/legacy_styles.qss:pet/chat")
        args+=(--add-data "pet/chat/modern_styles.qss:pet/chat")
        args+=(--add-data "pet/chat/styles.qss:pet/chat")
    fi
    if [[ -n "$excludes" ]]; then
        IFS=',' read -ra exclude_modules <<< "$excludes"
        for module in "${exclude_modules[@]}"; do
            args+=(--exclude-module "$module")
        done
    fi

    echo "==> 构建 $name.app"
    "$PYTHON_BIN" -m PyInstaller "${args[@]}" "$entry"
    codesign --force --deep --sign - "$DIST_DIR/$name.app"
done

echo "macOS 构建完成：$DIST_DIR"
