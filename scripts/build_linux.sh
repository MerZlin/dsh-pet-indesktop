#!/usr/bin/env bash
# 构建 dsh-pet-standalone Linux onedir（本地与 CI 共用的唯一构建入口）。
#
# CI（.github/workflows/build-linux.yml）与本脚本必须保持一致——构建逻辑
# 只有这一份，CI 通过调用本脚本复用，不在 workflow 内联 PyInstaller 命令，
# 避免两处漂移（曾因本地/CI 两套命令不一致而难以追踪）。
#
# 用法：
#   ./scripts/build_linux.sh                            # 本地默认输出 dist
#   ./scripts/build_linux.sh --dist dist                # 指定输出目录
#   ./scripts/build_linux.sh --variants webm-chat,webm  # 只构建指定变体（CI 只发两个）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 默认用 PATH 上的 python3（源码环境）；可用 PYTHON_BIN 覆盖。
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || echo python3)}"
DIST_DIR="$ROOT/dist"
WORK_DIR="$ROOT/build/.pyinstaller/linux"
VARIANTS="webm-chat,webm,gif-chat,gif"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dist) DIST_DIR="$2"; shift 2 ;;
        --variants) VARIANTS="$2"; shift 2 ;;
        *) echo "未知参数: $1" >&2; exit 1 ;;
    esac
done

cd "$ROOT"

# 仅当要构建 gif 变体时才生成 GIF 素材（convert_to_gif 默认幂等：只转换
# 缺失/过期的，CI 只构建 webm 变体时不会触发；与 build_macos.sh 一致）。
if [[ ",$VARIANTS," == *",gif-chat,"* || ",$VARIANTS," == *",gif,"* ]]; then
    "$PYTHON_BIN" scripts/convert_to_gif.py --clean
fi

mkdir -p "$DIST_DIR" "$WORK_DIR"

IFS=',' read -ra variant_list <<< "$VARIANTS"
for variant in "${variant_list[@]}"; do
    case "$variant" in
        webm-chat)  entry="packaging/pet_entry.py";            assets="assets/characters";       excludes="" ;;
        webm)       entry="packaging/pet_entry_no_chat.py";    assets="assets/characters";       excludes="pet.chat,keyring" ;;
        gif-chat)   entry="packaging/pet_entry.py";            assets="assets/characters_gif";   excludes="" ;;
        gif)        entry="packaging/pet_entry_no_chat.py";    assets="assets/characters_gif";   excludes="pet.chat,keyring" ;;
        *) echo "未知变体: $variant" >&2; exit 1 ;;
    esac
    name="dsh-pet-standalone-$variant"
    printf "VARIANT = '%s'\n" "$variant" > packaging/build_variant.py

    args=(
        --noconfirm
        --clean
        --onedir
        --paths .
        --distpath "$DIST_DIR"
        --workpath "$WORK_DIR"
        --name "$name"
        --collect-all imageio_ffmpeg
        --collect-all certifi
        --collect-all PySide6.QtMultimedia
        --add-data "$assets:$assets"
        --add-data "assets/sounds:assets/sounds"
        --add-data "assets/chat:assets/chat"
        --add-data "assets/big_blue_fat_fish:assets/big_blue_fat_fish"
        --add-data "pet/menu_templates:pet/menu_templates"
        --add-data "integrations:integrations"
    )
    if [[ "$name" == *-chat ]]; then
        args+=(--collect-all keyring)
        args+=(--add-data "pet/chat/legacy_styles.qss:pet/chat")
        args+=(--add-data "pet/chat/modern_styles.qss:pet/chat")
    fi
    if [[ -n "$excludes" ]]; then
        IFS=',' read -ra exclude_modules <<< "$excludes"
        for module in "${exclude_modules[@]}"; do
            args+=(--exclude-module "$module")
        done
    fi

    echo "==> 构建 $name"
    "$PYTHON_BIN" -m PyInstaller "${args[@]}" "$entry"
    # 中文编码自检（issue #26）：字节码/资源/文件名被编码污染即中止。
    "$PYTHON_BIN" scripts/check_bundle_encoding.py --dir "$DIST_DIR/$name"
    # Bridge node_modules 自包含修复（issue: Cannot find package '@deepseek-ai/cosmokit'）：
    # --add-data 复制 pnpm 符号链接布局可能损坏，展开为真实目录树并在副本上冒烟。
    "$PYTHON_BIN" scripts/fix_bridge_bundle.py --app-dir "$DIST_DIR/$name"
done

echo "Linux 构建完成：$DIST_DIR"
