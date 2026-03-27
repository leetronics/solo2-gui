#!/usr/bin/env bash
# build_macos.sh — Build SoloKeys GUI.app and package it as a DMG
#
# Requirements: macOS 12+, Homebrew, Python 3.10+
# Usage: ./build_macos.sh
set -euo pipefail

APP_NAME="SoloKeys GUI"
APP_VERSION="0.1.0"
DMG_NAME="SoloKeys GUI-${APP_VERSION}.dmg"

# ---------------------------------------------------------------------------
# 1. Check required tools
# ---------------------------------------------------------------------------
if ! command -v brew &>/dev/null; then
    echo "Error: Homebrew not found. Install from https://brew.sh" >&2
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found. Install with: brew install python" >&2
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
    echo "Python ${PYTHON_VERSION} OK"
else
    echo "Error: Python 3.10+ required (found ${PYTHON_VERSION})." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Detect libusb
# ---------------------------------------------------------------------------
if [[ -n "${LIBUSB_PATH:-}" && -f "${LIBUSB_PATH}" ]]; then
    echo "Using LIBUSB_PATH=${LIBUSB_PATH}"
elif [[ -f "/opt/homebrew/lib/libusb-1.0.0.dylib" ]]; then
    export LIBUSB_PATH="/opt/homebrew/lib/libusb-1.0.0.dylib"
    echo "Detected libusb (Apple Silicon): ${LIBUSB_PATH}"
elif [[ -f "/usr/local/lib/libusb-1.0.0.dylib" ]]; then
    export LIBUSB_PATH="/usr/local/lib/libusb-1.0.0.dylib"
    echo "Detected libusb (Intel): ${LIBUSB_PATH}"
else
    echo "Error: libusb not found." >&2
    echo "Install with: brew install libusb" >&2
    echo "Or set: export LIBUSB_PATH=/path/to/libusb-1.0.0.dylib" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. Install Python dependencies
# ---------------------------------------------------------------------------
echo ""
echo "Installing Python dependencies..."
pip3 install -r requirements.txt
pip3 install "pyinstaller>=6.2.0"

# ---------------------------------------------------------------------------
# 4. Clean previous build artifacts
# ---------------------------------------------------------------------------
echo ""
echo "Cleaning previous build..."
rm -rf build dist

# ---------------------------------------------------------------------------
# 5. Run PyInstaller
# ---------------------------------------------------------------------------
echo ""
echo "Running PyInstaller..."
pyinstaller --clean --noconfirm solokeys_gui.spec

if [[ ! -d "dist/${APP_NAME}.app" ]]; then
    echo "Error: PyInstaller did not produce dist/${APP_NAME}.app" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 6. Ad-hoc codesign (required to run on Apple Silicon without Gatekeeper alert)
#    For public distribution replace '-' with your Developer ID certificate.
# ---------------------------------------------------------------------------
echo ""
echo "Codesigning (ad-hoc)..."
codesign --force --deep --sign "-" "dist/${APP_NAME}.app"
echo "Note: Ad-hoc signature only. For distribution, use:"
echo "  codesign --force --deep --sign 'Developer ID Application: ...' 'dist/${APP_NAME}.app'"

# ---------------------------------------------------------------------------
# 7. Create DMG
# ---------------------------------------------------------------------------
echo ""
echo "Creating DMG..."

STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "${STAGING_DIR}"' EXIT

# Copy app and create Applications symlink for drag-to-install UX
cp -R "dist/${APP_NAME}.app" "${STAGING_DIR}/"
ln -s /Applications "${STAGING_DIR}/Applications"

hdiutil create \
    -volname "${APP_NAME}" \
    -srcfolder "${STAGING_DIR}" \
    -ov \
    -format UDZO \
    "dist/${DMG_NAME}"

# ---------------------------------------------------------------------------
# 8. Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "Build complete:"
echo "  dist/${APP_NAME}.app"
echo "  dist/${DMG_NAME}"
echo ""
echo "For public distribution you should:"
echo "  1. Codesign with a Developer ID Application certificate"
echo "  2. Notarize with Apple: xcrun notarytool submit ..."
echo "  3. Staple the ticket:   xcrun stapler staple 'dist/${DMG_NAME}'"
echo "============================================================"
