#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PACKAGE_NAME="solokeys-gui"
INSTALL_LIBDIR="/usr/lib/solokeys-gui"
BUILD_VERSION_FILE="${REPO_ROOT}/src/solo_gui/_build_version.py"
SOLO2_REPO_URL="https://github.com/leetronics/solo2-python.git"
SOLO2_REPO_REF="${SOLO2_PYTHON_REF:-main}"

linux_pkg_require_command() {
    local cmd="$1"
    local hint="$2"
    if ! command -v "${cmd}" &>/dev/null; then
        echo "Error: ${cmd} not found. ${hint}" >&2
        exit 1
    fi
}

linux_pkg_check_python() {
    linux_pkg_require_command python3 "Install Python 3.10 or newer."
    local py_version
    py_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
        echo "Error: Python 3.10+ required (found ${py_version})." >&2
        exit 1
    fi
}

linux_pkg_resolved_version() {
    python3 "${REPO_ROOT}/scripts/app_version.py" resolved
}

linux_pkg_find_solo2_root() {
    local work_dir="$1"
    local candidate=""

    if [[ -n "${SOLO2_PYTHON_DIR:-}" ]]; then
        candidate="${SOLO2_PYTHON_DIR}"
    elif [[ -d "${REPO_ROOT}/../solo2-python/src/solo2" ]]; then
        candidate="${REPO_ROOT}/../solo2-python"
    fi

    if [[ -n "${candidate}" ]]; then
        if [[ ! -d "${candidate}/src/solo2" ]]; then
            echo "Error: SOLO2_PYTHON_DIR must contain src/solo2." >&2
            exit 1
        fi
        printf '%s\n' "${candidate}"
        return
    fi

    linux_pkg_require_command git \
        "Install git or set SOLO2_PYTHON_DIR=/path/to/solo2-python."

    candidate="${work_dir}/solo2-python"
    git clone --depth 1 --branch "${SOLO2_REPO_REF}" "${SOLO2_REPO_URL}" "${candidate}" >/dev/null
    printf '%s\n' "${candidate}"
}

linux_pkg_prepare_root() {
    local pkg_root="$1"
    local app_version="$2"
    local work_dir="$3"
    local solo2_root

    solo2_root="$(linux_pkg_find_solo2_root "${work_dir}")"

    mkdir -p "${pkg_root}${INSTALL_LIBDIR}"
    python3 "${REPO_ROOT}/scripts/app_version.py" write-build-module --version "${app_version}" >/dev/null

    cp -R "${REPO_ROOT}/src/solo_gui" "${pkg_root}${INSTALL_LIBDIR}/"
    cp -R "${solo2_root}/src/solo2" "${pkg_root}${INSTALL_LIBDIR}/"

    find "${pkg_root}${INSTALL_LIBDIR}" -type d -name "__pycache__" -prune -exec rm -rf {} +
    find "${pkg_root}${INSTALL_LIBDIR}" -type f -name "*.pyc" -delete

    rm -f "${pkg_root}${INSTALL_LIBDIR}/solo_gui/debug.txt"
    rm -f "${pkg_root}${INSTALL_LIBDIR}/solo_gui/solokeys_secrets_host.sh"

    install -d "${pkg_root}/usr/bin"
    sed \
        -e "s#@INSTALL_LIBDIR@#${INSTALL_LIBDIR}#g" \
        -e "s#@APP_VERSION@#${app_version}#g" \
        "${REPO_ROOT}/packaging/linux/bin/solokeys-gui" > "${pkg_root}/usr/bin/solokeys-gui"
    chmod 0755 "${pkg_root}/usr/bin/solokeys-gui"

    sed \
        -e "s#@INSTALL_LIBDIR@#${INSTALL_LIBDIR}#g" \
        -e "s#@APP_VERSION@#${app_version}#g" \
        "${REPO_ROOT}/packaging/linux/bin/solokeys-secrets-host" > "${pkg_root}/usr/bin/solokeys-secrets-host"
    chmod 0755 "${pkg_root}/usr/bin/solokeys-secrets-host"

    install -d "${pkg_root}/usr/share/applications"
    install -m 0644 "${REPO_ROOT}/packaging/linux/desktop/solokeys-gui.desktop" \
        "${pkg_root}/usr/share/applications/solokeys-gui.desktop"

    install -d "${pkg_root}/usr/share/icons/hicolor/256x256/apps"
    install -m 0644 "${REPO_ROOT}/src/solo_gui/resources/logo-square.png" \
        "${pkg_root}/usr/share/icons/hicolor/256x256/apps/solokeys-gui.png"

    for manifest_dir in \
        "${pkg_root}/etc/opt/chrome/native-messaging-hosts" \
        "${pkg_root}/etc/chromium/native-messaging-hosts" \
        "${pkg_root}/etc/chromium-browser/native-messaging-hosts"; do
        install -d "${manifest_dir}"
        install -m 0644 \
            "${REPO_ROOT}/packaging/linux/native-messaging/com.solokeys.secrets.json" \
            "${manifest_dir}/"
    done

    install -d "${pkg_root}/lib/udev/rules.d"
    install -m 0644 "${REPO_ROOT}/packaging/linux/udev/70-solokeys.rules" \
        "${pkg_root}/lib/udev/rules.d/70-solokeys.rules"
}
