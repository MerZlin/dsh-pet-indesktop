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
"$PYTHON_BIN" scripts/verify_bundle_tts.py --environment

# 仅当要构建 gif 变体时才生成 GIF 素材（convert_to_gif 默认幂等：只转换
# 缺失/过期的，CI 只构建 webm 变体时不会触发；与 build_macos.sh 一致）。
if [[ ",$VARIANTS," == *",gif-chat,"* || ",$VARIANTS," == *",gif,"* ]]; then
    "$PYTHON_BIN" scripts/convert_to_gif.py --clean
fi

mkdir -p "$DIST_DIR" "$WORK_DIR"

# 按当前 PySide6 Qt 精确版本构建 Fcitx 插件，避免系统 Qt6 插件的私有 ABI 不匹配。
FCITX5_PLUGIN_DIR="$(mktemp -d /tmp/dsh-pet-fcitx-package.XXXXXX)"
trap 'rm -rf "$FCITX5_PLUGIN_DIR"' EXIT
FCITX5_PLUGIN="$FCITX5_PLUGIN_DIR/libfcitx5platforminputcontextplugin.so"
"$ROOT/scripts/build_pyside6_fcitx_plugin.sh" "$PYTHON_BIN" "$FCITX5_PLUGIN"

IFS=',' read -ra variant_list <<< "$VARIANTS"

# 素材硬门（2026-10-11，与 Windows 的 build_onedir.ps1 同口径）：帧序列产物
# assets/characters/<角色>/frameseq/ 是刻意不入库的派生产物（.gitignore），而
# --add-data 对非 GIF 变体整树拷贝 assets/characters——素材在就进包、缺了也不
# 报错，于是缺帧序列的包会被静默打出来（用户长期看到未转换素材）。这里在
# PyInstaller 起跑前验证每个 clip 都有当前世代的帧序列产物，非零即中止。
# GIF 变体跳过本门：它打包 assets/characters_gif（convert_to_gif.py 直出），
# 素材树里没有也没有 frameseq 这一层，本门不适用。
needs_frameseq=0
for requested in "${variant_list[@]}"; do
    case "$requested" in
        gif|gif-chat) ;;
        *) needs_frameseq=1 ;;
    esac
done
if [[ "$needs_frameseq" == "1" ]]; then
    if ! "$PYTHON_BIN" scripts/check_frameseq_bundle.py; then
        echo "[frameseq] 帧序列素材不完整，拒绝出包（补法见上面的 stderr）" >&2
        exit 1
    fi
fi

# 本地 OCR 模型硬门（2026-10-13，与 Windows 的 build_onedir.ps1 同口径）：
# 「截图提取文字」走随包的三个 ONNX 模型（resources/rapidocr_models/，30.3 MiB），
# 缺件或换版时产品不崩——只会让识别静默变差或直接报"请重新安装"。这里在
# PyInstaller 起跑前按"文件名 + sha256"挡住（判据来自 pet/screen_capture/ocr.py，
# 三端共用同一个脚本）。
if ! "$PYTHON_BIN" scripts/verify_rapidocr_models.py; then
    echo "[rapidocr] 本地 OCR 模型不完整，拒绝出包" >&2
    exit 1
fi

# rapidocr 自己的两份配置数据（config.yaml / default_models.yaml）也必须进包：
# 只带模型的包在冻结环境里连引擎都构造不出来（2026-10-13 实测，v1 变体）。为什么
# 不用 --collect-data rapidocr：那会把它 wheel 自带的同一批 30.3 MiB 模型再收一份
# （v2 变体实测），而模型我们只从 resources/ 拿一份；所以只显式补这两份 yaml。
RAPIDOCR_DIR="$("$PYTHON_BIN" -c "import os, rapidocr; print(os.path.dirname(rapidocr.__file__))")"
if [[ -z "$RAPIDOCR_DIR" ]]; then
    echo "[rapidocr] 解析不出 rapidocr 包目录" >&2
    exit 1
fi
for cfg in config.yaml default_models.yaml; do
    if [[ ! -f "$RAPIDOCR_DIR/$cfg" ]]; then
        echo "[rapidocr] 包数据缺失：$RAPIDOCR_DIR/$cfg" >&2
        exit 1
    fi
done

# 两个模块都要显式 --hidden-import（2026-10-13 交叉审查 B1；切片 5 加了第二个）：
#
# 1. 宿主侧引擎 pet.screen_capture.ocr —— 截图动作走的是**动态** seam：
#    pet/screen_capture/actions.py 用 importlib 按字符串（OCR_MODULE = "ocr"）解析它，
#    全仓没有一处静态 import 指向这个模块。PyInstaller 从入口出发的静态分析摸不到它，
#    于是模型和配置都在包里、引擎模块却不在：冻结包里解析不出引擎，右键「截图 →
#    提取文字」永远置灰。它的依赖（rapidocr / cv2 / numpy / onnxruntime）随这条
#    hidden-import 被静态收集。
# 2. 识别子进程 pet.screen_capture.ocr_worker（切片 5）—— 模块名只出现在命令行
#    字符串里（<exe> --ocr-worker），静态分析同样摸不到。缺它不会崩，只是每次识屏
#    报"起不了工作进程"。
RAPIDOCR_HIDDEN=(--hidden-import pet.screen_capture.ocr --hidden-import pet.screen_capture.ocr_worker)

for variant in "${variant_list[@]}"; do
    case "$variant" in
        webm-chat)  entry="packaging/pet_entry.py";            assets="assets/characters";       excludes="" ;;
        webm)       entry="packaging/pet_entry_no_chat.py";    assets="assets/characters";       excludes="pet.chat" ;;
        gif-chat)   entry="packaging/pet_entry.py";            assets="assets/characters_gif";   excludes="" ;;
        gif)        entry="packaging/pet_entry_no_chat.py";    assets="assets/characters_gif";   excludes="pet.chat" ;;
        *) echo "未知变体: $variant" >&2; exit 1 ;;
    esac
    name="dsh-pet-standalone-$variant"
    printf "VARIANT = '%s'\n" "$variant" > packaging/build_variant.py

    # 运行期图标（窗口/标题栏）也要进包：Linux 上 PyInstaller 忽略 --icon，图标只能
    # 靠这一份——pet/application_icon.py 从 <_MEIPASS>/assets/icon.ico 读。漏掉这一行
    # 不会有任何报错（只有源文件不存在时 PyInstaller 才会挡下），因此由
    # tests/test_application_icon.py 的打包契约测试守着。
    #
    # keyring（2026-10-17）：四个变体都收集。no-chat 变体排掉了 pet.chat，静态分析因此
    # 摸不到 keyring（chat 的 SecretStore 是它原来的唯一入口），而语音报时的收费后端
    # （pet/tts/mimo、pet/tts/doubao）照旧收 API Key——少了它，凭据只能留在写它的进程
    # 内存里，设置页（独立进程）报「已保存」而主程序永远读不到。
    #
    # LICENSE / THIRD_PARTY_NOTICES.md：assets/sounds 下的内置点击音是 MeteorNOX 的
    # MIT 素材，许可正文写在仓根 THIRD_PARTY_NOTICES.md 第 1 节。只打素材不打声明，
    # 等于分发复制件却不带许可正文。两份落在产物根（Linux 的 _MEIPASS 就是产物根）。
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
        --collect-all edge_tts
        --collect-all psutil
        --collect-all keyring
        --add-binary "$FCITX5_PLUGIN:PySide6/Qt/plugins/platforminputcontexts"
        --add-data "$assets:$assets"
        --add-data "assets/sounds:assets/sounds"
        --add-data "assets/chat:assets/chat"
        --add-data "assets/icon.ico:assets"
        --add-data "assets/big_blue_fat_fish:assets/big_blue_fat_fish"
        --add-data "pet/menu_templates:pet/menu_templates"
        --add-data "pet/persona_presets:pet/persona_presets"
        --add-data "integrations:integrations"
        --add-data "LICENSE:."
        --add-data "THIRD_PARTY_NOTICES.md:."
        --add-data "resources/rapidocr_models:resources/rapidocr_models"
        --add-data "$RAPIDOCR_DIR/config.yaml:rapidocr"
        --add-data "$RAPIDOCR_DIR/default_models.yaml:rapidocr"
        "${RAPIDOCR_HIDDEN[@]}"
    )
    # 设置页样式表：已在 modern_settings_dialog.py 内联（_settings_stylesheet）
    if [[ "$name" == *-chat ]]; then
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
    # 本地 OCR 随包校验（与 Windows 的 build_onedir.ps1 同口径）：上面那条门只证明
    # 源目录里的模型是对的，产物里还缺两件同样致命的东西——**引擎模块**
    # （pet.screen_capture.ocr，动态 seam 的目标，只有 --hidden-import 能把它拉进包）
    # 与 rapidocr 的两份配置数据。三者缺一「截图提取文字」都不可用，所以一并判。
    "$PYTHON_BIN" scripts/verify_rapidocr_models.py --app-dir "$DIST_DIR/$name"
    "$PYTHON_BIN" scripts/verify_bundle_tts.py --app-dir "$DIST_DIR/$name"
    # 中文编码自检（issue #26）：字节码/资源/文件名被编码污染即中止。
    "$PYTHON_BIN" scripts/check_bundle_encoding.py --dir "$DIST_DIR/$name"
    # ELF 符号版本地板（issue #432）：产物里任何 ELF 引用的 GLIBC/GLIBCXX 不得超
    # 22.04 基线（2.35 / 3.4.30）。PyInstaller 会把构建机的系统库整份拷进包——
    # 构建基线一旦上漂，这道门当场红，而不是等老发行版用户报 GLIBC not found。
    "$PYTHON_BIN" scripts/check_elf_symbol_floor.py --app-dir "$DIST_DIR/$name"
    # Bridge 零依赖防线（2026-09 事故：缺 @deepseek-ai/dsh-llm 导致用户整个
    # dsh 插件树加载失败）：剥掉 --add-data 可能带入的 node_modules 残留，
    # 校验 dist 副本清单零依赖并跑 hermetic 冒烟（见 fix_bridge_bundle.py）。
    "$PYTHON_BIN" scripts/fix_bridge_bundle.py --app-dir "$DIST_DIR/$name"
done

echo "Linux 构建完成：$DIST_DIR"
