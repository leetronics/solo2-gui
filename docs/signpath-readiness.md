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
| Source repository | `https://github.com/leetronics/solo2-gui` |
| License | `MIT OR Apache-2.0` |
| License files | `LICENSE`, `LICENSE-MIT`, `LICENSE-APACHE` |
| Privacy policy | `PRIVACY.md` |
| Third-party notices | `THIRD_PARTY_NOTICES.md` |
| Release artifacts | GitHub Releases |
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

- Document the provenance and license of
  `src/solo_gui/resources/provisioner-minimal.bin`.
  Current local metadata: size `227324` bytes, SHA-256
  `51bbd12700cc1c0b577ca39749907c49130bba8bd2bf78d7e7e22d1d2efd41cf`,
  introduced by commit `1e1f26f76cc25e5674a3d8a50e4478c1ab3978ab`.
- Confirm that all bundled PyInstaller runtime contents are covered by
  open-source licenses or system-library exceptions.
- Add a release/download page section explaining that Windows artifacts are
  signed through SignPath Foundation once this is active.
- Decide whether SignPath should sign only the final installer or also nested
  executables through an artifact configuration. Signing both nested executables
  and the final installer is preferable.

## Reviewer Notes

SoloKeys GUI does not include telemetry or analytics. Network access is limited
to user-visible update checks/downloads via GitHub release metadata and release
assets. Browser integration is local native messaging plus local IPC between the
browser helper and the GUI.
