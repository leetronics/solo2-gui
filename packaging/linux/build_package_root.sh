#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PACKAGE_NAME="solokeys-gui"
INSTALL_LIBDIR="/usr/lib/solokeys-gui"
BUILD_VERSION_FILE="${REPO_ROOT}/src/solo_gui/_build_version.py"
SOLO2_REPO_URL="https://github.com/leetronics/solo2-python.git"
SOLO2_REPO_REF="${SOLO2_PYTHON_REF:-main}"
GUI_APP_NAME="SoloKeys GUI"
NATIVE_HOST_NAME="solokeys-secrets-host"
LINUX_PKG_BUILD_PYTHON=""

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

linux_pkg_install_pyinstaller_build_deps() {
    local work_dir="$1"
    local solo2_root
    local requirements_file="${work_dir}/requirements-linux-package.txt"
    local venv_dir="${work_dir}/pyinstaller-venv"

    echo "Creating temporary Python build environment..."
    python3 -m venv "${venv_dir}"
    LINUX_PKG_BUILD_PYTHON="${venv_dir}/bin/python"
    solo2_root="$(linux_pkg_find_solo2_root "${work_dir}")"
    grep -v '^solo2 @' "${REPO_ROOT}/requirements.txt" > "${requirements_file}"

    echo "Installing PyInstaller build dependencies..."
    "${LINUX_PKG_BUILD_PYTHON}" -m pip install --upgrade pip
    "${LINUX_PKG_BUILD_PYTHON}" -m pip install \
        --requirement "${requirements_file}" \
        "hidapi>=0.14.0.post2" \
        "pyinstaller>=6.2.0"
    "${LINUX_PKG_BUILD_PYTHON}" -m pip install --editable "${solo2_root}"
}

linux_pkg_build_pyinstaller_payload() {
    local pkg_root="$1"
    local app_version="$2"
    local work_dir="$3"
    local gui_dir="${REPO_ROOT}/dist/${GUI_APP_NAME}"
    local gui_exe="${gui_dir}/${GUI_APP_NAME}"
    local host_exe="${REPO_ROOT}/dist/${NATIVE_HOST_NAME}"

    linux_pkg_install_pyinstaller_build_deps "${work_dir}"
    "${LINUX_PKG_BUILD_PYTHON}" "${REPO_ROOT}/scripts/app_version.py" write-build-module --version "${app_version}" >/dev/null

    echo "Cleaning previous PyInstaller build artifacts..."
    rm -rf "${REPO_ROOT}/build"
    rm -rf "${gui_dir}"
    rm -rf "${host_exe}"

    echo "Running PyInstaller for GUI..."
    (cd "${REPO_ROOT}" && SOLOKEYS_GUI_VERSION="${app_version}" "${LINUX_PKG_BUILD_PYTHON}" -m PyInstaller --clean --noconfirm solokeys_gui.spec)
    if [[ ! -d "${gui_dir}" || ! -x "${gui_exe}" ]]; then
        echo "Error: PyInstaller did not produce executable ${gui_exe}" >&2
        exit 1
    fi

    echo "Running PyInstaller for native host..."
    (cd "${REPO_ROOT}" && "${LINUX_PKG_BUILD_PYTHON}" -m PyInstaller --clean --noconfirm native_host.spec)
    if [[ ! -x "${host_exe}" ]]; then
        echo "Error: PyInstaller did not produce executable ${host_exe}" >&2
        exit 1
    fi

    # Match AppImage runtime hardening: avoid fragile host input-method modules.
    rm -f \
        "${gui_dir}/_internal/PySide6/Qt/plugins/platforminputcontexts/libibusplatforminputcontextplugin.so"
    rm -f "${gui_dir}/_internal/libxkbcommon.so.0"

    mkdir -p "${pkg_root}${INSTALL_LIBDIR}"
    cp -R "${gui_dir}/." "${pkg_root}${INSTALL_LIBDIR}/"
    mv "${pkg_root}${INSTALL_LIBDIR}/${GUI_APP_NAME}" "${pkg_root}${INSTALL_LIBDIR}/solokeys-gui-bin"
    install -m 0755 "${host_exe}" "${pkg_root}${INSTALL_LIBDIR}/${NATIVE_HOST_NAME}"
}

linux_pkg_prepare_root() {
    local pkg_root="$1"
    local app_version="$2"
    local work_dir="$3"

    linux_pkg_build_pyinstaller_payload "${pkg_root}" "${app_version}" "${work_dir}"

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

    for manifest_dir in \
        "${pkg_root}/usr/lib/mozilla/native-messaging-hosts" \
        "${pkg_root}/usr/lib64/mozilla/native-messaging-hosts"; do
        install -d "${manifest_dir}"
        install -m 0644 \
            "${REPO_ROOT}/packaging/linux/native-messaging/com.solokeys.secrets.firefox.json" \
            "${manifest_dir}/com.solokeys.secrets.json"
    done

    install -d "${pkg_root}/lib/udev/rules.d"
    install -m 0644 "${REPO_ROOT}/packaging/linux/udev/70-solokeys.rules" \
        "${pkg_root}/lib/udev/rules.d/70-solokeys.rules"
}
