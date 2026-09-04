#!/usr/bin/env bash
# 为 PySide6 自带 Qt 构建 ABI 匹配的 Fcitx5 Qt6 输入法插件。
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "用法: $0 <python-bin> <output-plugin-path>" >&2
    exit 2
fi

PYTHON_BIN="$1"
OUTPUT_PLUGIN="$2"
FCITX5_QT_TAG="5.1.14"

# 仅依赖构建环境提供的工具与 Qt6 私有开发头文件，缺失时给出可执行的错误信息。
for required in cmake git perl; do
    if ! command -v "$required" >/dev/null; then
        echo "缺少构建工具: $required" >&2
        exit 1
    fi
done
SYSTEM_INPUT_CONTEXT_HEADER="$(find /usr/include -type f -path '*/QtGui/*/QtGui/qpa/qplatforminputcontext.h' -print -quit)"
if [[ -z "$SYSTEM_INPUT_CONTEXT_HEADER" ]]; then
    echo "缺少 qt6-base-private-dev；无法构建 PySide6 Fcitx 插件。" >&2
    exit 1
fi

# 从实际 PySide6 运行库读取 Qt 精确版本，插件的私有 QPA 头文件必须与它一一对应。
PYSIDE_QT_VERSION="$($PYTHON_BIN -c 'from PySide6.QtCore import qVersion; print(qVersion())')"
PYSIDE_QT_LIB="$($PYTHON_BIN -c 'from pathlib import Path; import PySide6; print(Path(PySide6.__file__).resolve().parent / "Qt" / "lib")')"
if [[ ! "$PYSIDE_QT_VERSION" =~ ^6\.[0-9]+\.[0-9]+$ ]] || [[ ! -f "$PYSIDE_QT_LIB/libQt6Gui.so.6" ]]; then
    echo "无法识别 PySide6 Qt 运行库版本或路径。" >&2
    exit 1
fi

# 所有源码、覆盖头文件和对象文件都在临时目录；只把最终插件复制到调用方指定位置。
WORK_DIR="$(mktemp -d /tmp/dsh-pet-fcitx-plugin.XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT
QTBASE_SOURCE="$WORK_DIR/qtbase"
FCITX_SOURCE="$WORK_DIR/fcitx5-qt"
HEADER_OVERLAY="$WORK_DIR/header-overlay/qpa"
BUILD_DIR="$WORK_DIR/build"
mkdir -p "$HEADER_OVERLAY"

git clone --depth 1 --branch "v$PYSIDE_QT_VERSION" https://code.qt.io/qt/qtbase.git "$QTBASE_SOURCE"
git clone --depth 1 --branch "$FCITX5_QT_TAG" https://github.com/fcitx/fcitx5-qt.git "$FCITX_SOURCE"

# 覆盖平台输入上下文实际使用的 QPA 私有头文件，修正 Qt 6.4 开发包与 PySide6 Qt 的布局差异。
for header in qplatformcursor.h qplatforminputcontext.h qplatforminputcontextplugin_p.h qplatformnativeinterface.h qplatformscreen.h qwindowsysteminterface.h; do
    cp "$QTBASE_SOURCE/src/gui/kernel/$header" "$HEADER_OVERLAY/$header"
done

cmake -S "$FCITX_SOURCE" -B "$BUILD_DIR" \
    -DBUILD_ONLY_PLUGIN=ON \
    -DENABLE_QT4=OFF \
    -DENABLE_QT5=OFF \
    -DENABLE_QT6=ON \
    -DENABLE_QT6_WAYLAND_WORKAROUND=OFF \
    "-DCMAKE_CXX_FLAGS=-I$WORK_DIR/header-overlay"

# CMake 用系统 Qt6 头文件完成目标配置；先编译对象，最终链接改为 PySide6 自带 Qt 运行库。
set +e
cmake --build "$BUILD_DIR" --target fcitx5platforminputcontextplugin-qt6 -j2 >"$WORK_DIR/compile.log" 2>&1
set -e
LINK_FILE="$BUILD_DIR/qt6/platforminputcontext/CMakeFiles/fcitx5platforminputcontextplugin-qt6.dir/link.txt"
if [[ ! -f "$LINK_FILE" ]]; then
    cat "$WORK_DIR/compile.log" >&2
    exit 1
fi
perl -pi -e "s#/usr/lib/[^ ]*/libQt6Widgets\\.so[^ ]*#$PYSIDE_QT_LIB/libQt6Widgets.so.6#g; s#/usr/lib/[^ ]*/libQt6Gui\\.so[^ ]*#$PYSIDE_QT_LIB/libQt6Gui.so.6#g; s#/usr/lib/[^ ]*/libQt6DBus\\.so[^ ]*#$PYSIDE_QT_LIB/libQt6DBus.so.6#g; s#/usr/lib/[^ ]*/libQt6Core\\.so[^ ]*#$PYSIDE_QT_LIB/libQt6Core.so.6#g" "$LINK_FILE"
(
    cd "$BUILD_DIR/qt6/platforminputcontext"
    /bin/sh "$LINK_FILE"
)

# 构建产物必须存在且引用 PySide6 Qt 版本的扩展按键接口，避免回退到系统 Qt 6.4 ABI。
BUILT_PLUGIN="$BUILD_DIR/qt6/platforminputcontext/libfcitx5platforminputcontextplugin.so"
if [[ ! -f "$BUILT_PLUGIN" ]] || ! nm -D --demangle "$BUILT_PLUGIN" | grep -q 'handleExtendedKeyEvent'; then
    echo "Fcitx5 Qt6 插件构建不完整。" >&2
    exit 1
fi
mkdir -p "$(dirname "$OUTPUT_PLUGIN")"
cp "$BUILT_PLUGIN" "$OUTPUT_PLUGIN"
