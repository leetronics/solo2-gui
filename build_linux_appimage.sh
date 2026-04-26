#!/usr/bin/env bash
# build_linux_appimage.sh - Build a Linux AppImage for SoloKeys GUI
#
# Requirements: Linux x86_64, Python 3.10+, pip, curl or wget.
# Usage: ./build_linux_appimage.sh
set -euo pipefail

APP_NAME="SoloKeys GUI"
APP_ID="solokeys-gui"
BUILD_VERSION_FILE="src/solo_gui/_build_version.py"
APPDIR=""
WORK_DIR=""

cleanup() {
    rm -f "${BUILD_VERSION_FILE}"
    if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
        rm -rf "${WORK_DIR}"
    fi
}

trap cleanup EXIT

require_command() {
    local cmd="$1"
    local hint="$2"
    if ! command -v "${cmd}" &>/dev/null; then
        echo "Error: ${cmd} not found. ${hint}" >&2
        exit 1
    fi
}

download_file() {
    local url="$1"
    local output="$2"

    if command -v curl &>/dev/null; then
        curl -fsSL "${url}" -o "${output}"
        return
    fi
    if command -v wget &>/dev/null; then
        wget -q "${url}" -O "${output}"
        return
    fi

    echo "Error: curl or wget is required to download appimagetool." >&2
    exit 1
}

resolve_appimagetool() {
    if [[ -n "${APPIMAGETOOL:-}" ]]; then
        if [[ ! -x "${APPIMAGETOOL}" ]]; then
            echo "Error: APPIMAGETOOL is not executable: ${APPIMAGETOOL}" >&2
            exit 1
        fi
        printf '%s\n' "${APPIMAGETOOL}"
        return
    fi

    if command -v appimagetool &>/dev/null; then
        command -v appimagetool
        return
    fi

    local tool="${WORK_DIR}/appimagetool-x86_64.AppImage"
    local url="${APPIMAGETOOL_URL:-https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage}"
    echo "Downloading appimagetool..." >&2
    download_file "${url}" "${tool}"
    chmod 0755 "${tool}"
    printf '%s\n' "${tool}"
}

check_python() {
    require_command python3 "Install Python 3.10 or newer."
    local py_version
    py_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
        echo "Error: Python 3.10+ required (found ${py_version})." >&2
        exit 1
    fi
}

case "$(uname -m)" in
    x86_64)
        APPIMAGE_ARCH="x86_64"
        ;;
    *)
        echo "Error: AppImage build currently supports x86_64 only." >&2
        exit 1
        ;;
esac

check_python
require_command file "Install file."

APP_VERSION="$(python3 scripts/app_version.py resolved)"
APPIMAGE_NAME="SoloKeys-GUI-${APP_VERSION}-${APPIMAGE_ARCH}.AppImage"

WORK_DIR="$(mktemp -d)"
APPDIR="${WORK_DIR}/AppDir"

echo ""
echo "Installing Python dependencies..."
python3 -m pip install -r requirements.txt
python3 -m pip install "hidapi>=0.14.0.post2" "pyinstaller>=6.2.0"
python3 scripts/app_version.py write-build-module --version "${APP_VERSION}" >/dev/null

echo ""
echo "Cleaning previous AppImage build artifacts..."
rm -rf build
rm -rf "dist/${APP_NAME}"
rm -rf "dist/solokeys-secrets-host"
rm -f "dist/${APPIMAGE_NAME}"

echo ""
echo "Running PyInstaller for GUI..."
pyinstaller --clean --noconfirm solokeys_gui.spec

GUI_DIR="dist/${APP_NAME}"
GUI_EXE="${GUI_DIR}/${APP_NAME}"
if [[ ! -d "${GUI_DIR}" || ! -x "${GUI_EXE}" ]]; then
    echo "Error: PyInstaller did not produce executable ${GUI_EXE}" >&2
    exit 1
fi

echo ""
echo "Running PyInstaller for native host..."
pyinstaller --clean --noconfirm native_host.spec

HOST_EXE="dist/solokeys-secrets-host"
if [[ ! -x "${HOST_EXE}" ]]; then
    echo "Error: PyInstaller did not produce executable ${HOST_EXE}" >&2
    exit 1
fi

echo ""
echo "Preparing AppDir..."
install -d \
    "${APPDIR}/usr/bin" \
    "${APPDIR}/usr/lib/${APP_ID}" \
    "${APPDIR}/usr/lib/gio/modules" \
    "${APPDIR}/usr/share/applications" \
    "${APPDIR}/usr/share/icons/hicolor/256x256/apps"

cp -R "${GUI_DIR}/." "${APPDIR}/usr/lib/${APP_ID}/"
install -m 0755 "${HOST_EXE}" "${APPDIR}/usr/bin/solokeys-secrets-host"
install -m 0644 "src/solo_gui/resources/logo-square.png" \
    "${APPDIR}/${APP_ID}.png"
install -m 0644 "src/solo_gui/resources/logo-square.png" \
    "${APPDIR}/usr/share/icons/hicolor/256x256/apps/${APP_ID}.png"

sed \
    -e "s#^Exec=.*#Exec=AppRun#g" \
    -e "s#^Icon=.*#Icon=${APP_ID}#g" \
    "packaging/linux/desktop/${APP_ID}.desktop" > "${APPDIR}/${APP_ID}.desktop"
install -m 0644 "${APPDIR}/${APP_ID}.desktop" \
    "${APPDIR}/usr/share/applications/${APP_ID}.desktop"

# Qt's IBus platform input context can be activated lazily on the first key
# event and is fragile in AppImages because it bridges bundled Qt/GLib with the
# host desktop's IBus stack. Use Qt's built-in compose input context instead.
rm -f \
    "${APPDIR}/usr/lib/${APP_ID}/_internal/PySide6/Qt/plugins/platforminputcontexts/libibusplatforminputcontextplugin.so"

# libqxcb uses host libxkbcommon-x11/libxcb-xkb on most desktops. Bundling only
# libxkbcommon.so.0 mixes the XKB stack and can segfault on the first key event.
rm -f "${APPDIR}/usr/lib/${APP_ID}/_internal/libxkbcommon.so.0"

cat > "${APPDIR}/AppRun" <<'EOF'
#!/bin/sh
set -eu

APPDIR="$(dirname "$(readlink -f "$0")")"
export SOLOKEYS_PATH="${SOLOKEYS_PATH:-auto}"

# Keep bundled GLib/GIO from loading host gvfs modules. A newer host gvfs
# module can otherwise resolve against the AppImage's older bundled libgio and
# crash before the Qt event loop starts.
export GIO_MODULE_DIR="${APPDIR}/usr/lib/gio/modules"
export GIO_USE_VFS=local
export NO_AT_BRIDGE=1

# Avoid desktop input-method plugins that may crash when the first key event
# crosses from bundled Qt libraries into host IBus/Fcitx services.
export QT_IM_MODULE=compose
unset XMODIFIERS

if [ "${1:-}" = "--native-host" ]; then
    shift
    exec "${APPDIR}/usr/bin/solokeys-secrets-host" "$@"
fi

exec "${APPDIR}/usr/lib/solokeys-gui/SoloKeys GUI" "$@"
EOF
chmod 0755 "${APPDIR}/AppRun"

mkdir -p dist
APPIMAGETOOL_PATH="$(resolve_appimagetool)"

echo ""
echo "Building AppImage..."
ARCH="${APPIMAGE_ARCH}" APPIMAGE_EXTRACT_AND_RUN=1 \
    "${APPIMAGETOOL_PATH}" "${APPDIR}" "dist/${APPIMAGE_NAME}"

chmod 0755 "dist/${APPIMAGE_NAME}"

echo ""
echo "============================================================"
echo "Build complete:"
echo "  dist/${APPIMAGE_NAME}"
echo "============================================================"
