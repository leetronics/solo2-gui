# SignPath Foundation Readiness

This repository is intended to qualify for open-source Windows code signing via
SignPath Foundation, but the application should be reviewed against the current
SignPath Foundation conditions before applying.

References:

- https://signpath.org/
- https://signpath.org/terms.html
- https://docs.signpath.io/projects
- https://docs.signpath.io/signing-code

## Project Facts

| Item | Status |
|------|--------|
| Source repository | `https://github.com/solokeys/solo2-python-gui` |
| License | `MIT OR Apache-2.0` |
| License files | `LICENSE`, `LICENSE-MIT`, `LICENSE-APACHE` |
| Privacy policy | `PRIVACY.md` |
| Third-party notices | `THIRD_PARTY_NOTICES.md` |
| Release artifacts | GitHub Releases (`https://github.com/solokeys/solo2-python-gui/releases`) |
| Windows artifact | Inno Setup installer `SoloKeys-GUI-Setup-<version>.exe` |
| Native host artifact | Bundled `solokeys-secrets-host.exe` |

## Signing Scope

SignPath signs the nested executables and the final installer via an artifact
configuration with deep signing:

- `dist\SoloKeys GUI\SoloKeys GUI.exe`
- `dist\SoloKeys GUI\solokeys-secrets-host.exe`
- `dist\installer\SoloKeys-GUI-Setup-<version>.exe`

The existing `.pfx` Authenticode path in `build_windows.bat` is a fallback for
private or commercial certificates. If SignPath Foundation accepts the project,
the release workflow should submit the Windows artifact to SignPath instead of
using repository-stored certificate material.

## Verified Readiness Facts

- The README contains the required "Code Signing Policy" section (SignPath
  wording, maintainer roles, privacy policy link).
- `src/solo_gui/resources/provisioner-minimal.bin` is byte-for-byte
  reproducible from its documented source revision; provenance, build command,
  and bundling rationale are recorded in `THIRD_PARTY_NOTICES.md`.
- All components bundled into the PyInstaller payloads are OSI-approved open
  source; copyleft handling is recorded in `THIRD_PARTY_NOTICES.md`.

## Open Items Before Applying

- Publish a first versioned release (tag `v<version>`) on
  `https://github.com/solokeys/solo2-python-gui` — SignPath requires the project
  to already be released in the form that should be signed; only the `ci-latest`
  pre-release exists so far.

## Reviewer Notes

SoloKeys GUI does not include telemetry or analytics. Network access is limited
to user-visible update checks/downloads via GitHub release metadata and release
assets. Browser integration is local native messaging plus local IPC between the
browser helper and the GUI.
