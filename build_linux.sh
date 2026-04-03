#!/usr/bin/env bash
# build_linux.sh — Build SoloKeys GUI as an AppImage
#
# Requirements: Ubuntu 22.04+, Python 3.10+, libusb-1.0-0, libpcsclite-dev
# Usage: ./build_linux.sh
set -euo pipefail

APP_NAME="SoloKeys GUI"
ARCH="$(uname -m)"
BUILD_VERSION_FILE="src/solo_gui/_build_version.py"

cleanup() {
    rm -f "${BUILD_VERSION_FILE}"
    if [[ -n "${APPIMAGE_DIR:-}" && -d "${APPIMAGE_DIR}" ]]; then
        rm -rf "${APPIMAGE_DIR}"
    fi
}

trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1. Check required tools
# ---------------------------------------------------------------------------
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found." >&2
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
    echo "Python ${PYTHON_VERSION} OK"
else
    echo "Error: Python 3.10+ required (found ${PYTHON_VERSION})." >&2
    exit 1
fi

APP_VERSION="$(python3 scripts/app_version.py resolved)"

# ---------------------------------------------------------------------------
# 2. Check PCSC development headers needed by pyscard
# ---------------------------------------------------------------------------
if [[ ! -f "/usr/include/PCSC/winscard.h" && ! -f "/usr/local/include/PCSC/winscard.h" ]]; then
    echo "Error: PCSC development headers not found." >&2
    echo "Install with: sudo apt install libpcsclite-dev" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. Install Python dependencies
# ---------------------------------------------------------------------------
echo ""
echo "Installing Python dependencies..."
pip3 install -r requirements.txt
pip3 install "pyinstaller>=6.2.0"
python3 scripts/app_version.py write-build-module --version "${APP_VERSION}" >/dev/null

# ---------------------------------------------------------------------------
# 4. Clean previous build artifacts
# ---------------------------------------------------------------------------
echo ""
echo "Cleaning previous build..."
rm -rf build dist

# ---------------------------------------------------------------------------
# 5. Run PyInstaller for main app
# ---------------------------------------------------------------------------
echo ""
echo "Running PyInstaller..."
pyinstaller --clean --noconfirm solokeys_gui.spec

if [[ ! -d "dist/${APP_NAME}" ]]; then
    echo "Error: PyInstaller did not produce dist/${APP_NAME}/" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 5. Run PyInstaller for native messaging host
# ---------------------------------------------------------------------------
echo ""
echo "Building native messaging host..."
pyinstaller --clean --noconfirm native_host.spec

if [[ ! -f "dist/solokeys-secrets-host" ]]; then
    echo "Error: Native host build failed." >&2
    exit 1
fi

# Copy native host into the app directory
cp dist/solokeys-secrets-host "dist/${APP_NAME}/"

# ---------------------------------------------------------------------------
# 6. Build AppImage
# ---------------------------------------------------------------------------
echo ""
echo "Building AppImage..."

APPIMAGE_DIR="$(mktemp -d)"

# Prepare AppDir structure
mkdir -p "${APPIMAGE_DIR}/usr/bin"
cp -r "dist/${APP_NAME}/"* "${APPIMAGE_DIR}/usr/bin/"

# Create desktop file
cat > "${APPIMAGE_DIR}/SoloKeys GUI.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SoloKeys GUI
Comment=Manage your Solo 2 device
Exec=SoloKeys GUI
Icon=solokeys-gui
Categories=Utility;
Terminal=false
EOF

# Copy icon
cp "src/solo_gui/resources/logo-square.png" "${APPIMAGE_DIR}/solokeys-gui.png"

# Create AppRun entry point
cat > "${APPIMAGE_DIR}/AppRun" <<'EOF'
#!/usr/bin/env bash
SELF="$(readlink -f "$0")"
HERE="$(dirname "$SELF")"
exec "${HERE}/usr/bin/SoloKeys GUI" "$@"
EOF
chmod +x "${APPIMAGE_DIR}/AppRun"

# Download linuxdeploy
LINUXDEPLOY="${APPIMAGE_DIR}/linuxdeploy"
if [[ "${ARCH}" == "aarch64" ]]; then
    LD_ARCH="aarch64"
else
    LD_ARCH="x86_64"
fi

curl -L -o "${LINUXDEPLOY}" \
    "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-${LD_ARCH}.AppImage"
chmod +x "${LINUXDEPLOY}"

# Build AppImage
OUTPUT="dist/SoloKeys-GUI-${APP_VERSION}-${ARCH}.AppImage"
"${LINUXDEPLOY}" --appdir "${APPIMAGE_DIR}" --output appimage

# linuxdeploy outputs to current dir, move to dist
if [[ -f "SoloKeys_GUI-${APP_VERSION}-${ARCH}.AppImage" ]]; then
    mv "SoloKeys_GUI-${APP_VERSION}-${ARCH}.AppImage" "${OUTPUT}"
elif ls SoloKeys*.AppImage 1>/dev/null 2>&1; then
    mv SoloKeys*.AppImage "${OUTPUT}"
fi

# ---------------------------------------------------------------------------
# 7. Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "Build complete:"
echo "  ${OUTPUT}"
echo "============================================================"
