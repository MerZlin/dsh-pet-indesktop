#!/usr/bin/env bash
# Build all Qt headers/tools from the exact wheel's Qt version. Do not mix
# Ubuntu's Qt private headers with PySide's Qt ABI (#71).
set -euo pipefail
PYTHON_BIN="$1"
OUTPUT_PLUGIN="$2"
WORK_DIR="${3:-$(dirname "$OUTPUT_PLUGIN")/build}"
mkdir -p "$WORK_DIR"
mkdir -p "$(dirname "$OUTPUT_PLUGIN")"
QT_VERSION="$($PYTHON_BIN -c 'from PySide6.QtCore import qVersion; print(qVersion())')"
[[ "$QT_VERSION" == "6.11.3" ]] || { echo 'Expected Qt 6.11.3' >&2; exit 1; }
pkg-config --exists x11 xcb xkbcommon dbus-1 || {
    echo 'Missing Fcitx build dependencies: install libx11-dev libxcb1-dev libxkbcommon-dev libdbus-1-dev.' >&2
    exit 1
}
checkout() {
    local url="$1" tag="$2" directory="$3"
    if [[ ! -e "$directory" ]]; then
        git clone --depth 1 --branch "$tag" "$url" "$directory"
    fi
    [[ "$(git -C "$directory" remote get-url origin)" == "$url" ]]
    [[ "$(git -C "$directory" rev-parse HEAD)" == "$(git -C "$directory" rev-parse "$tag^{commit}")" ]]
    [[ -z "$(git -C "$directory" status --porcelain --untracked-files=all)" ]] || {
        echo "Refusing modified cached source: $directory" >&2; exit 1;
    }
}
checkout https://code.qt.io/qt/qtbase.git "v$QT_VERSION" "$WORK_DIR/qtbase"
checkout https://github.com/fcitx/fcitx5-qt.git 5.1.14 "$WORK_DIR/fcitx5-qt"
cmake -S "$WORK_DIR/qtbase" -B "$WORK_DIR/qt-build" -GNinja \
    -DCMAKE_BUILD_TYPE=Release "-DCMAKE_INSTALL_PREFIX=$WORK_DIR/qt" \
    -DQT_BUILD_TESTS=OFF -DQT_BUILD_EXAMPLES=OFF \
    -DINPUT_opengl=no -DFEATURE_opengl=OFF -DFEATURE_xcb=OFF -DFEATURE_sql=OFF
cmake --build "$WORK_DIR/qt-build" --parallel 2
cmake --install "$WORK_DIR/qt-build"
[[ "$("$WORK_DIR/qt/bin/qmake" -query QT_VERSION)" == "$QT_VERSION" ]]
cmake -S "$WORK_DIR/fcitx5-qt" -B "$WORK_DIR/fcitx-build" -GNinja \
    "-DCMAKE_PREFIX_PATH=$WORK_DIR/qt" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_ONLY_PLUGIN=ON -DENABLE_QT4=OFF -DENABLE_QT5=OFF -DENABLE_QT6=ON \
    -DENABLE_QT6_WAYLAND_WORKAROUND=OFF
cmake --build "$WORK_DIR/fcitx-build" --target fcitx5platforminputcontextplugin-qt6 --parallel 2
cp "$WORK_DIR/fcitx-build/qt6/platforminputcontext/libfcitx5platforminputcontextplugin.so" "$OUTPUT_PLUGIN"
LICENSE_DIR="$(dirname "$OUTPUT_PLUGIN")/licenses"
mkdir -p "$LICENSE_DIR"
cp -R "$WORK_DIR/fcitx5-qt/LICENSES" "$LICENSE_DIR/fcitx5-qt"
cp -R "$WORK_DIR/qtbase/LICENSES" "$LICENSE_DIR/qtbase"
# Preserve upstream copyright notices as well as license templates.
git -C "$WORK_DIR/fcitx5-qt" archive --format=tar.gz --output="$LICENSE_DIR/fcitx5-qt-5.1.14-source.tar.gz" HEAD
# Resolve only against bundled Qt after moving to Qt/plugins/platforminputcontexts.
patchelf --set-rpath '$ORIGIN/../../lib' "$OUTPUT_PLUGIN"
"$PYTHON_BIN" - "$OUTPUT_PLUGIN" "$WORK_DIR" "$LICENSE_DIR" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from PySide6.QtCore import QPluginLoader, qVersion
from petcore.platform.bundle import fcitx_problems
plugin, work, licenses = map(Path, sys.argv[1:])
def commit(name):
    return subprocess.check_output(['git', '-C', str(work / name), 'rev-parse', 'HEAD'], text=True).strip()
with plugin.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
provenance = {'schema': 1, 'qt_version': qVersion(), 'qt_source_tag': 'v' + qVersion(),
              'qt_source_commit': commit('qtbase'), 'fcitx_source_tag': '5.1.14',
              'fcitx_source_commit': commit('fcitx5-qt'), 'plugin_sha256': digest}
errors = fcitx_problems(plugin, QPluginLoader(str(plugin)).metaData(), qVersion(), provenance)
assert not errors, errors
(licenses / 'fcitx5-qt-build.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
print('Fcitx metadata and exact build provenance match Qt', qVersion())
PY
