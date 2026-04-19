#!/usr/bin/env bash
# build_macos.sh — Build SoloKeys GUI.app and package it as a DMG
#
# Requirements: macOS 12+, Homebrew, Python 3.10+
# Usage: ./build_macos.sh
set -euo pipefail

APP_NAME="SoloKeys GUI"
BUILD_VERSION_FILE="src/solo_gui/_build_version.py"
STAGING_DIR=""
NATIVE_HOST_MODE_MARKER=".solokeys-native-host-mode"

cleanup() {
    rm -f "${BUILD_VERSION_FILE}"
    if [[ -n "${STAGING_DIR}" && -d "${STAGING_DIR}" ]]; then
        rm -rf "${STAGING_DIR}"
    fi
}

trap cleanup EXIT

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

APP_VERSION="$(python3 scripts/app_version.py resolved)"
DMG_NAME="SoloKeys-GUI-${APP_VERSION}.dmg"
HOST_DIR="dist/solokeys-secrets-host"
HOST_EXE="${HOST_DIR}/solokeys-secrets-host"
APP_RESOURCES_DIR="dist/${APP_NAME}.app/Contents/Resources"
APP_HOST_DIR="${APP_RESOURCES_DIR}/solokeys-secrets-host"
APP_HOST_EXE="${APP_HOST_DIR}/solokeys-secrets-host"
NATIVE_HOST_MODE="${SOLOKEYS_MACOS_NATIVE_HOST_MODE:-}"
CODESIGN_IDENTITY="${MACOS_CODESIGN_IDENTITY:-${APPLE_CODESIGN_IDENTITY:--}}"
NOTARIZE="${MACOS_NOTARIZE:-0}"

if [[ -z "${NATIVE_HOST_MODE}" ]]; then
    if [[ "${CODESIGN_IDENTITY}" == "-" ]]; then
        NATIVE_HOST_MODE="copy"
    else
        NATIVE_HOST_MODE="app"
    fi
fi

if [[ "${NATIVE_HOST_MODE}" != "copy" && "${NATIVE_HOST_MODE}" != "app" ]]; then
    echo "Error: SOLOKEYS_MACOS_NATIVE_HOST_MODE must be 'copy' or 'app'." >&2
    exit 1
fi

if [[ "${NOTARIZE}" == "1" && "${CODESIGN_IDENTITY}" == "-" ]]; then
    echo "Error: MACOS_NOTARIZE=1 requires a Developer ID MACOS_CODESIGN_IDENTITY." >&2
    exit 1
fi

codesign_target() {
    local target="$1"
    if [[ "${CODESIGN_IDENTITY}" == "-" ]]; then
        codesign --force --sign "-" "${target}"
    else
        codesign --force --options runtime --timestamp \
            --sign "${CODESIGN_IDENTITY}" \
            "${target}"
    fi
}

is_macho_file() {
    local target="$1"
    file -b "${target}" | grep -q "Mach-O"
}

codesign_app_bundle() {
    local app_bundle="$1"
    local contents_dir="${app_bundle}/Contents"
    local target

    # Sign only actual code. Avoid codesign --deep here: PyInstaller onedir
    # contains metadata directories such as *.dist-info that codesign can
    # misclassify as invalid nested bundles.
    while IFS= read -r -d '' target; do
        codesign_target "${target}"
    done < <(find "${contents_dir}" -type d -name "*.framework" -print0)

    while IFS= read -r -d '' target; do
        case "${target}" in
            *.framework/*)
                continue
                ;;
        esac
        if is_macho_file "${target}"; then
            codesign_target "${target}"
        fi
    done < <(find "${contents_dir}" -type f -print0)

    codesign_target "${app_bundle}"
    codesign --verify --strict --verbose=2 "${app_bundle}"
}

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
pip3 install "Pillow>=10.0.0"
python3 scripts/app_version.py write-build-module --version "${APP_VERSION}" >/dev/null

# ---------------------------------------------------------------------------
# 4. Clean previous build artifacts
# ---------------------------------------------------------------------------
echo ""
echo "Cleaning previous build..."
rm -rf build dist

# ---------------------------------------------------------------------------
# 5. Run PyInstaller for the GUI
# ---------------------------------------------------------------------------
echo ""
echo "Running PyInstaller for GUI..."
pyinstaller --clean --noconfirm solokeys_gui.spec

if [[ ! -d "dist/${APP_NAME}.app" ]]; then
    echo "Error: PyInstaller did not produce dist/${APP_NAME}.app" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 6. Build and bundle the native messaging host helper
# ---------------------------------------------------------------------------
echo ""
echo "Running PyInstaller for native host..."
pyinstaller --clean --noconfirm native_host.spec

if [[ ! -d "${HOST_DIR}" || ! -x "${HOST_EXE}" ]]; then
    echo "Error: PyInstaller did not produce executable ${HOST_EXE}" >&2
    exit 1
fi

mkdir -p "${APP_RESOURCES_DIR}"
rm -rf "${APP_HOST_DIR}"
cp -R "${HOST_DIR}" "${APP_HOST_DIR}"
chmod 0755 "${APP_HOST_EXE}"
printf "%s\n" "${NATIVE_HOST_MODE}" \
    > "${APP_RESOURCES_DIR}/${NATIVE_HOST_MODE_MARKER}"

# ---------------------------------------------------------------------------
# 7. Codesign
# ---------------------------------------------------------------------------
echo ""
echo "Codesigning..."
if [[ "${CODESIGN_IDENTITY}" == "-" ]]; then
    echo "Note: Ad-hoc signature only."
fi
codesign_app_bundle "dist/${APP_NAME}.app"

# ---------------------------------------------------------------------------
# 8. Create DMG
# ---------------------------------------------------------------------------
echo ""
echo "Creating DMG..."

STAGING_DIR="$(mktemp -d)"

# Copy app and create Applications symlink for drag-to-install UX
cp -R "dist/${APP_NAME}.app" "${STAGING_DIR}/"
ln -s /Applications "${STAGING_DIR}/Applications"

hdiutil create \
    -volname "${APP_NAME}" \
    -srcfolder "${STAGING_DIR}" \
    -ov \
    -format UDZO \
    "dist/${DMG_NAME}"

DMG_PATH="dist/${DMG_NAME}"

if [[ "${CODESIGN_IDENTITY}" != "-" ]]; then
    echo ""
    echo "Codesigning DMG..."
    codesign --force --timestamp --sign "${CODESIGN_IDENTITY}" "${DMG_PATH}"
fi

if [[ "${NOTARIZE}" == "1" ]]; then
    echo ""
    echo "Notarizing DMG..."
    notary_args=()
    if [[ -n "${APPLE_NOTARY_KEYCHAIN_PROFILE:-}" ]]; then
        notary_args=(--keychain-profile "${APPLE_NOTARY_KEYCHAIN_PROFILE}")
    elif [[ -n "${APPLE_NOTARY_KEY_PATH:-}" && -n "${APPLE_NOTARY_KEY_ID:-}" && -n "${APPLE_NOTARY_ISSUER_ID:-}" ]]; then
        notary_args=(
            --key "${APPLE_NOTARY_KEY_PATH}"
            --key-id "${APPLE_NOTARY_KEY_ID}"
            --issuer "${APPLE_NOTARY_ISSUER_ID}"
        )
    else
        echo "Error: MACOS_NOTARIZE=1 requires APPLE_NOTARY_KEYCHAIN_PROFILE or" >&2
        echo "APPLE_NOTARY_KEY_PATH, APPLE_NOTARY_KEY_ID and APPLE_NOTARY_ISSUER_ID." >&2
        exit 1
    fi

    xcrun notarytool submit "${DMG_PATH}" "${notary_args[@]}" --wait
    xcrun stapler staple "${DMG_PATH}"
fi

# ---------------------------------------------------------------------------
# 9. Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "Build complete:"
echo "  dist/${APP_NAME}.app"
echo "  ${APP_HOST_EXE}"
echo "  dist/${DMG_NAME}"
echo "  native host mode: ${NATIVE_HOST_MODE}"
echo "  codesign identity: ${CODESIGN_IDENTITY}"
echo "  notarized: ${NOTARIZE}"
echo ""
if [[ "${CODESIGN_IDENTITY}" == "-" ]]; then
    echo "For public distribution, set MACOS_CODESIGN_IDENTITY and MACOS_NOTARIZE=1."
fi
echo "============================================================"
