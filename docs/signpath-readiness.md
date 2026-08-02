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

## Proposed Signing Scope

The Windows release build should sign:

- `dist\SoloKeys GUI\SoloKeys GUI.exe`
- `dist\SoloKeys GUI\solokeys-secrets-host.exe`
- `dist\installer\SoloKeys-GUI-Setup-<version>.exe`

The existing `.pfx` Authenticode path in `build_windows.bat` is a fallback for
private or commercial certificates. If SignPath Foundation accepts the project,
the release workflow should submit the Windows artifact to SignPath instead of
using repository-stored certificate material.

## Open Items Before Applying

- ~~Verify byte-for-byte reproducibility of
  `src/solo_gui/resources/provisioner-minimal.bin`.~~ Done (2026-07-31) — rebuilt
  from `https://github.com/leetronics/solo2` commit
  `20421d1a8a61e6e0043bd7f0e9c9f977803801f6` per the documented command; identical
  except for 5 bytes of the builder's home directory embedded in a cargo registry
  path string. Details recorded in `THIRD_PARTY_NOTICES.md`.
- ~~Confirm that all bundled PyInstaller runtime contents are covered by
  open-source licenses or system-library exceptions.~~ Done (2026-08-02) —
  license metadata of every package in the build environment reviewed; all
  bundled components are OSI-approved open source (details and copyleft
  handling recorded in `THIRD_PARTY_NOTICES.md`).
- ~~Add a release/download page section explaining that Windows artifacts are
  signed through SignPath Foundation once this is active.~~ Done — the README
  now contains a "Code Signing Policy" section with the required wording,
  maintainer roles, and privacy policy link.
- Publish a first versioned release (tag `v<version>`) on
  `https://github.com/solokeys/solo2-python-gui` — SignPath requires the project
  to already be released in the form that should be signed; only the `ci-latest`
  pre-release exists so far.
- The signing workflow must run in `solokeys/solo2-python-gui`: SignPath
  verifies the repository URL from the request form against the CI origin for
  every build.
- ~~Decide whether SignPath should sign only the final installer or also nested
  executables through an artifact configuration.~~ Decided (2026-08-02): sign
  everything — the nested executables (`SoloKeys GUI.exe`,
  `solokeys-secrets-host.exe`) and the final installer, via a SignPath artifact
  configuration with deep signing.

## Reviewer Notes

SoloKeys GUI does not include telemetry or analytics. Network access is limited
to user-visible update checks/downloads via GitHub release metadata and release
assets. Browser integration is local native messaging plus local IPC between the
browser helper and the GUI.
