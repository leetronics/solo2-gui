#!/usr/bin/env bash
# build_linux_rpm.sh — Build an RPM package for SoloKeys GUI
#
# Requirements: rpmbuild/rpm-build and Python 3.10+
# Usage: ./build_linux_rpm.sh
set -euo pipefail

source "packaging/linux/build_package_root.sh"

WORK_DIR=""
PKG_ROOT=""
RPM_TOPDIR=""

cleanup() {
    rm -f "${BUILD_VERSION_FILE}"
    if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
        rm -rf "${WORK_DIR}"
    fi
}

trap cleanup EXIT

linux_pkg_check_python
linux_pkg_require_command rpmbuild "Install rpm-build or rpm."

case "$(uname -m)" in
    x86_64) RPM_ARCH="x86_64" ;;
    aarch64|arm64) RPM_ARCH="aarch64" ;;
    *)
        echo "Error: unsupported architecture $(uname -m)" >&2
        exit 1
        ;;
esac

APP_VERSION="$(linux_pkg_resolved_version)"
RPM_VERSION="$(printf '%s' "${APP_VERSION}" | sed 's/[^A-Za-z0-9._+~]/./g')"
RPM_VERSION="${RPM_VERSION#.}"
RPM_VERSION="${RPM_VERSION:-0.0.0}"
RPM_RELEASE="1"
RPM_SOURCE_NAME="${PACKAGE_NAME}-${RPM_VERSION}.tar.gz"
RPM_CHANGELOG_DATE="$(LC_ALL=C date '+%a %b %d %Y')"

echo ""
echo "Preparing RPM package payload..."

WORK_DIR="$(mktemp -d)"
PKG_ROOT="${WORK_DIR}/pkgroot"
RPM_TOPDIR="${WORK_DIR}/rpmbuild"

linux_pkg_prepare_root "${PKG_ROOT}" "${APP_VERSION}" "${WORK_DIR}"

install -d "${RPM_TOPDIR}/BUILD" "${RPM_TOPDIR}/BUILDROOT" "${RPM_TOPDIR}/RPMS" \
    "${RPM_TOPDIR}/SOURCES" "${RPM_TOPDIR}/SPECS" "${RPM_TOPDIR}/SRPMS" \
    "${WORK_DIR}/tmp" "${WORK_DIR}/rpmdb"

tar -C "${PKG_ROOT}" -czf "${RPM_TOPDIR}/SOURCES/${RPM_SOURCE_NAME}" .
sed \
    -e "s#@RPM_VERSION@#${RPM_VERSION}#g" \
    -e "s#@RPM_RELEASE@#${RPM_RELEASE}#g" \
    -e "s#@RPM_ARCH@#${RPM_ARCH}#g" \
    -e "s#@RPM_SOURCE_NAME@#${RPM_SOURCE_NAME}#g" \
    -e "s#@RPM_CHANGELOG_DATE@#${RPM_CHANGELOG_DATE}#g" \
    "packaging/linux/rpm/solokeys-gui.spec.in" > "${RPM_TOPDIR}/SPECS/solokeys-gui.spec"

rpmbuild \
    --define "_topdir ${RPM_TOPDIR}" \
    --define "_sourcedir ${RPM_TOPDIR}/SOURCES" \
    --define "_specdir ${RPM_TOPDIR}/SPECS" \
    --define "_rpmdir ${RPM_TOPDIR}/RPMS" \
    --define "_srcrpmdir ${RPM_TOPDIR}/SRPMS" \
    --define "_dbpath ${WORK_DIR}/rpmdb" \
    --define "_tmppath ${WORK_DIR}/tmp" \
    -bb "${RPM_TOPDIR}/SPECS/solokeys-gui.spec" >/dev/null

mkdir -p dist
OUTPUT="dist/${PACKAGE_NAME}-${RPM_VERSION}-${RPM_RELEASE}.${RPM_ARCH}.rpm"
cp "${RPM_TOPDIR}/RPMS/${RPM_ARCH}/solokeys-gui-${RPM_VERSION}-${RPM_RELEASE}.${RPM_ARCH}.rpm" "${OUTPUT}"

echo ""
echo "============================================================"
echo "Build complete:"
echo "  ${OUTPUT}"
echo "============================================================"
