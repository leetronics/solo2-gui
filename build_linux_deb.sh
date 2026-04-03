#!/usr/bin/env bash
# build_linux_deb.sh — Build a Debian package for SoloKeys GUI
#
# Requirements: Ubuntu/Debian with dpkg-deb, Python 3.10+, libusb-1.0-0-dev,
#               libpcsclite-dev
# Usage: ./build_linux_deb.sh
set -euo pipefail

PACKAGE_NAME="solokeys-gui"
APP_NAME="SoloKeys GUI"
INSTALL_PREFIX="/opt/solokeys-gui"
INSTALL_LIBDIR="${INSTALL_PREFIX}/lib"
BUILD_VERSION_FILE="src/solo_gui/_build_version.py"
WORK_DIR=""
PKG_ROOT=""

cleanup() {
    rm -f "${BUILD_VERSION_FILE}"
    if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
        rm -rf "${WORK_DIR}"
    fi
}

trap cleanup EXIT

if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found." >&2
    exit 1
fi

if ! command -v dpkg-deb &>/dev/null; then
    echo "Error: dpkg-deb not found. Install dpkg-dev." >&2
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
    echo "Error: Python 3.10+ required (found ${PYTHON_VERSION})." >&2
    exit 1
fi

case "$(uname -m)" in
    x86_64) DEB_ARCH="amd64" ;;
    aarch64|arm64) DEB_ARCH="arm64" ;;
    *)
        echo "Error: unsupported architecture $(uname -m)" >&2
        exit 1
        ;;
esac

APP_VERSION="$(python3 scripts/app_version.py resolved)"
DEB_VERSION="${APP_VERSION//[^A-Za-z0-9.+:~-]/-}"
if [[ ! "${DEB_VERSION}" =~ ^[0-9] ]]; then
    DEB_VERSION="0~git${DEB_VERSION}"
fi

echo ""
echo "Installing Python dependencies for Debian package..."

WORK_DIR="$(mktemp -d)"
PKG_ROOT="${WORK_DIR}/pkgroot"

mkdir -p "${PKG_ROOT}${INSTALL_LIBDIR}"
python3 scripts/app_version.py write-build-module --version "${APP_VERSION}" >/dev/null
python3 -m pip install --upgrade --target "${PKG_ROOT}${INSTALL_LIBDIR}" -r requirements.txt
cp -R "src/solo_gui" "${PKG_ROOT}${INSTALL_LIBDIR}/"
find "${PKG_ROOT}${INSTALL_LIBDIR}/solo_gui" -type d -name "__pycache__" -prune -exec rm -rf {} +
rm -f "${PKG_ROOT}${INSTALL_LIBDIR}/solo_gui/debug.txt"
rm -f "${PKG_ROOT}${INSTALL_LIBDIR}/solo_gui/solokeys_secrets_host.sh"

install -d "${PKG_ROOT}/usr/bin"
sed \
    -e "s#@INSTALL_LIBDIR@#${INSTALL_LIBDIR}#g" \
    -e "s#@APP_VERSION@#${APP_VERSION}#g" \
    "packaging/linux/bin/solokeys-gui" > "${PKG_ROOT}/usr/bin/solokeys-gui"
chmod 0755 "${PKG_ROOT}/usr/bin/solokeys-gui"

sed \
    -e "s#@INSTALL_LIBDIR@#${INSTALL_LIBDIR}#g" \
    -e "s#@APP_VERSION@#${APP_VERSION}#g" \
    "packaging/linux/bin/solokeys-secrets-host" > "${PKG_ROOT}/usr/bin/solokeys-secrets-host"
chmod 0755 "${PKG_ROOT}/usr/bin/solokeys-secrets-host"

install -d "${PKG_ROOT}/usr/share/applications"
install -m 0644 "packaging/linux/desktop/solokeys-gui.desktop" \
    "${PKG_ROOT}/usr/share/applications/solokeys-gui.desktop"

install -d "${PKG_ROOT}/usr/share/icons/hicolor/256x256/apps"
install -m 0644 "src/solo_gui/resources/logo-square.png" \
    "${PKG_ROOT}/usr/share/icons/hicolor/256x256/apps/solokeys-gui.png"

for manifest_dir in \
    "${PKG_ROOT}/etc/opt/chrome/native-messaging-hosts" \
    "${PKG_ROOT}/etc/chromium/native-messaging-hosts" \
    "${PKG_ROOT}/etc/chromium-browser/native-messaging-hosts"; do
    install -d "${manifest_dir}"
    install -m 0644 "packaging/linux/native-messaging/com.solokeys.secrets.json" "${manifest_dir}/"
done

install -d "${PKG_ROOT}/lib/udev/rules.d"
install -m 0644 "packaging/linux/udev/70-solokeys.rules" \
    "${PKG_ROOT}/lib/udev/rules.d/70-solokeys.rules"

install -d "${PKG_ROOT}/DEBIAN"
install -m 0755 "packaging/linux/debian/postinst" "${PKG_ROOT}/DEBIAN/postinst"
install -m 0755 "packaging/linux/debian/postrm" "${PKG_ROOT}/DEBIAN/postrm"

INSTALLED_SIZE="$(du -sk "${PKG_ROOT}" | cut -f1)"
cat > "${PKG_ROOT}/DEBIAN/control" <<EOF
Package: ${PACKAGE_NAME}
Version: ${DEB_VERSION}
Section: utils
Priority: optional
Architecture: ${DEB_ARCH}
Maintainer: SoloKeys GUI Team
Installed-Size: ${INSTALLED_SIZE}
Depends: python3 (>= 3.10), libusb-1.0-0, libglib2.0-0, libdbus-1-3, libfontconfig1, libfreetype6, zlib1g, libegl1, libgl1, libopengl0, libx11-6, libx11-xcb1, libxcb1, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-render0, libxcb-shape0, libxcb-shm0, libxcb-sync1, libxcb-util1, libxcb-xfixes0, libxcb-xkb1, libxkbcommon0, libxkbcommon-x11-0
Recommends: pcscd, libpcsclite1
Description: Desktop GUI for managing Solo 2 devices
 Manage your Solo 2 device, FIDO2 resident credentials, Secrets/TOTP entries,
 firmware updates, admin features and browser integration from a desktop GUI.
 .
 This package installs the Chrome/Chromium native messaging host system-wide,
 adds a desktop launcher and ships udev rules for SoloKeys HID access.
EOF

mkdir -p dist
OUTPUT="dist/${PACKAGE_NAME}_${DEB_VERSION}_${DEB_ARCH}.deb"
dpkg-deb --build --root-owner-group "${PKG_ROOT}" "${OUTPUT}" >/dev/null

echo ""
echo "============================================================"
echo "Build complete:"
echo "  ${OUTPUT}"
echo "============================================================"
