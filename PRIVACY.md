# Privacy Policy

SoloKeys GUI is a local desktop application for managing Solo 2 devices.

## Local Data

The application communicates with connected Solo 2 devices over local USB HID,
PC/SC smartcard interfaces, and bootloader transports. FIDO2 PINs, OpenPGP PINs,
PIV PINs, Vault secrets, TOTP secrets, and generated one-time codes are processed
locally and are not sent to SoloKeys GUI project servers.

Browser integration uses the browser native messaging protocol and a local IPC
socket or named pipe to talk to the running GUI. Browser requests are handled on
the local machine.

## Network Access

SoloKeys GUI does not include telemetry, analytics, advertising, or crash-report
uploading.

The application may make network requests only for user-visible update flows:

- The app update checker queries GitHub Releases for this project.
- Firmware update checks query GitHub Releases for Solo 2 firmware.
- Firmware update installation downloads the selected firmware asset from the
  release host.

These requests are made directly from the user's machine to GitHub or the
download URL shown by the release metadata.

## Logs

SoloKeys GUI writes diagnostic messages to the local console or local log output
of the process. Users should avoid sharing logs publicly if they contain device
paths, account names, resident credential names, or other local context.

## Contact

Report privacy or security issues through the project's GitHub repository.
