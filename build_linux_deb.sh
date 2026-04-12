#!/usr/bin/env bash
# build_linux_deb.sh — Build a Debian package for SoloKeys GUI
#
# Requirements: Ubuntu/Debian with dpkg-deb and Python 3.10+
# Usage: ./build_linux_deb.sh
set -euo pipefail

source "packaging/linux/build_package_root.sh"

WORK_DIR=""
PKG_ROOT=""

cleanup() {
    rm -f "${BUILD_VERSION_FILE}"
    if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
        rm -rf "${WORK_DIR}"
    fi
}

trap cleanup EXIT

linux_pkg_check_python
linux_pkg_require_command dpkg-deb "Install dpkg-dev."

case "$(uname -m)" in
    x86_64) DEB_ARCH="amd64" ;;
    aarch64|arm64) DEB_ARCH="arm64" ;;
    *)
        echo "Error: unsupported architecture $(uname -m)" >&2
        exit 1
        ;;
esac

APP_VERSION="$(linux_pkg_resolved_version)"
DEB_VERSION="${APP_VERSION//[^A-Za-z0-9.+:~-]/-}"
if [[ ! "${DEB_VERSION}" =~ ^[0-9] ]]; then
    DEB_VERSION="0~git${DEB_VERSION}"
fi

echo ""
echo "Preparing Debian package payload..."

WORK_DIR="$(mktemp -d)"
PKG_ROOT="${WORK_DIR}/pkgroot"

linux_pkg_prepare_root "${PKG_ROOT}" "${APP_VERSION}" "${WORK_DIR}"

install -d "${PKG_ROOT}/DEBIAN"
install -m 0755 "packaging/linux/debian/postinst" "${PKG_ROOT}/DEBIAN/postinst"
install -m 0755 "packaging/linux/debian/postrm" "${PKG_ROOT}/DEBIAN/postrm"
install -m 0644 "packaging/linux/debian/triggers" "${PKG_ROOT}/DEBIAN/triggers"

INSTALLED_SIZE="$(du -sk "${PKG_ROOT}" | cut -f1)"
cat > "${PKG_ROOT}/DEBIAN/control" <<EOF
Package: ${PACKAGE_NAME}
Version: ${DEB_VERSION}
Section: utils
Priority: optional
Architecture: ${DEB_ARCH}
Maintainer: SoloKeys GUI Team
Installed-Size: ${INSTALLED_SIZE}
Depends: python3 (>= 3.10), python3-pip, libusb-1.0-0, python3-pyside6.qtcore, python3-pyside6.qtgui, python3-pyside6.qtwidgets
Recommends: pcscd
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
