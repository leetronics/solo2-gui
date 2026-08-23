#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FLATPAK_DIR="${ROOT_DIR}/packaging/flatpak"
DIST_DIR="${DIST_DIR:-${ROOT_DIR}/dist}"
TOOLS_VENV="${FLATPAK_TOOLS_VENV:-${RUNNER_TEMP:-/tmp}/solokeys-flatpak-tools}"
APP_ID="io.github.leetronics.solo2-gui"
RUNTIME_BRANCH="6.11"
MANIFEST="${APP_ID}.yml"
VERSION="${SOLOKEYS_GUI_VERSION:-flatpak}"
BUNDLE_PATH="${DIST_DIR}/SoloKeys-GUI-${VERSION}.flatpak"

mkdir -p "${DIST_DIR}"

flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --user -y flathub \
  "org.kde.Platform//${RUNTIME_BRANCH}" \
  "org.kde.Sdk//${RUNTIME_BRANCH}" \
  "io.qt.PySide.BaseApp//${RUNTIME_BRANCH}"

python3 -m venv "${TOOLS_VENV}"
"${TOOLS_VENV}/bin/python" -m pip install --upgrade pip
"${TOOLS_VENV}/bin/python" -m pip install flatpak-pip-generator

cd "${FLATPAK_DIR}"
"${TOOLS_VENV}/bin/python" -m flatpak_pip_generator \
  --runtime="org.kde.Sdk//${RUNTIME_BRANCH}" \
  --requirements-file=requirements-flatpak.txt \
  --output=python3-requirements \
  --prefer-wheels=cryptography,cffi,swig

flatpak-builder --force-clean --user --repo=repo build-dir "${MANIFEST}"

flatpak-builder --run build-dir "${MANIFEST}" python3 -c \
  'import solo_gui, solo2; from PySide6.QtWidgets import QApplication; print("imports ok")'
flatpak-builder --run build-dir "${MANIFEST}" python3 -c \
  'from solo_gui import native_host_installer as n; assert n.registration_scope() == "unsupported"; assert n.find_native_host_exe() is None; ok, _ = n.install(); assert ok is False; print("native host guard ok")'

flatpak build-bundle repo "${BUNDLE_PATH}" "${APP_ID}" master
echo "Built ${BUNDLE_PATH}"
