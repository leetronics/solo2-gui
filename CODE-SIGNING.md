# Code Signing

This document describes the optional signing paths for release artifacts.

## macOS

`build_macos.sh` always signs the `.app` bundle:

- ad-hoc signing by default for local development
- Developer ID signing when a signing identity is configured
- optional notarization for release builds

For a Developer ID signed release build, provide a signing identity and
notarization credentials:

```bash
export MACOS_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export MACOS_NOTARIZE=1
export APPLE_NOTARY_KEY_PATH=/path/to/AuthKey_KEYID.p8
export APPLE_NOTARY_KEY_ID=KEYID
export APPLE_NOTARY_ISSUER_ID=ISSUER_UUID
./build_macos.sh
```

Signed release builds automatically register the browser native messaging host
from inside the signed `.app` bundle. Ad-hoc builds keep using a per-user copy
under `~/Library/Application Support/solokeys-gui/` to avoid local Gatekeeper
issues while developing.

The GitHub desktop artifact workflows can sign and notarize macOS builds when
these repository secrets are configured:

- `APPLE_CERTIFICATE_P12`: base64-encoded Developer ID Application `.p12`
- `APPLE_CERTIFICATE_PASSWORD`: password for that `.p12`
- `APPLE_DEVELOPER_ID_APPLICATION`: full codesign identity string
- `APPLE_NOTARY_KEY_BASE64`: base64-encoded App Store Connect API `.p8` key
- `APPLE_NOTARY_KEY_ID`: App Store Connect API key ID
- `APPLE_NOTARY_ISSUER_ID`: App Store Connect issuer UUID

## Windows Authenticode

`build_windows.bat` can Authenticode-sign Windows artifacts when certificate
environment variables are configured. Without those variables, Windows builds
remain unsigned.

The `.pfx` path is intended for existing/private/commercial certificates and as
a fallback. Modern public code signing certificates usually require hardware or
cloud-backed key storage and may not provide exportable `.pfx` files.

When configured, the build signs:

- `dist\SoloKeys GUI\SoloKeys GUI.exe`
- `dist\solokeys-secrets-host.exe`
- `dist\installer\SoloKeys-GUI-Setup-<version>.exe`

Local `.pfx` signing:

```cmd
set WINDOWS_CODESIGN_CERT_PATH=C:\path\to\codesign.pfx
set WINDOWS_CODESIGN_CERT_PASSWORD=your-pfx-password
build_windows.bat
```

Alternative local certificate store signing:

```cmd
set WINDOWS_CODESIGN_CERT_SHA1=<certificate-thumbprint>
build_windows.bat
```

Optional environment variables:

- `WINDOWS_CODESIGN_TIMESTAMP_URL`: timestamp URL, defaults to `http://timestamp.digicert.com`
- `WINDOWS_CODESIGN_DESCRIPTION`: signed file description, defaults to `SoloKeys GUI`

The GitHub desktop artifact workflows can use the `.pfx` path when these
repository secrets are configured:

- `WINDOWS_CERTIFICATE_PFX`: base64-encoded standard code signing `.pfx`
- `WINDOWS_CERTIFICATE_PASSWORD`: password for that `.pfx`

## SignPath Foundation

For public open-source Windows releases, SignPath Foundation is likely the
preferred long-term path because it avoids storing private signing keys in this
repository or in GitHub Secrets.

Readiness notes live in [`docs/signpath-readiness.md`](docs/signpath-readiness.md).

If SignPath Foundation accepts the project, the Windows release workflow should
submit the Windows build artifact to SignPath instead of using repository-stored
certificate material. Keep the `.pfx` Authenticode path as a fallback for local
or non-Foundation signing.
