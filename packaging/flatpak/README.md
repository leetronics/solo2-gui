# Flatpak packaging

This is the first-stage Flatpak packaging for local and CI validation. It is
not a Flathub submission branch yet.

## App ID

The staging app ID is `io.github.leetronics.solo2-gui`, matching the current
GitHub repository location. If the app is submitted from a different upstream
repository or domain, rename the manifest, desktop file, metainfo file, icon,
and app ID together.

## Build locally

Install Flatpak tooling and the PySide base app:

```bash
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install flathub org.kde.Platform//6.11 org.kde.Sdk//6.11 io.qt.PySide.BaseApp//6.11
```

Generate the Python dependency submanifest:

```bash
cd packaging/flatpak
python3 -m flatpak_pip_generator \
  --runtime=org.kde.Sdk//6.11 \
  --requirements-file=requirements-flatpak.txt \
  --output=python3-requirements \
  --prefer-wheels=cryptography,cffi,swig
```

Build and run:

```bash
flatpak-builder --force-clean --user --install build-dir io.github.leetronics.solo2-gui.yml
flatpak run io.github.leetronics.solo2-gui
```

## Current limitations

- Browser native messaging registration is disabled in the Flatpak build. The
  host browser needs a host-side wrapper that launches the Flatpak native-host
  command; this is a follow-up task.
- The manifest uses broad `--device=all` access because Solo 2 uses HID raw
  devices for FIDO2 and raw USB access for bootloader flashing. This should be
  tested against newer USB portal support before Flathub submission.
- The `solo2-python` source is currently tracked from `main`, matching this
  repository's existing Python dependency. Pin it to a release tag or commit
  before a Flathub submission.
- The generated `python3-requirements.json` is intentionally not committed yet;
  generate it with the target Flatpak runtime so Python ABI and architecture
  selection match the builder.
