"""Generate FIDO2 self-attestation key material for Solo 2 Hacker devices."""

from __future__ import annotations

import struct
import datetime

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography import x509
from cryptography.x509.oid import NameOID


def generate_fido_attestation() -> tuple[bytes, bytes]:
    """Generate a P-256 attestation key and self-signed X.509 certificate.

    Returns:
        (trussed_key_blob, der_certificate)

        trussed_key_blob: 36 bytes — 0x0002 (flags: SENSITIVE) + 0x0005 (kind: P256)
            + 32-byte private key scalar.  Ready to write to /fido/sec/00.
        der_certificate: DER-encoded self-signed X.509 cert (typically ~250 bytes).
            Ready to write to /fido/x5c/00.
    """
    # Generate P-256 key pair
    private_key = ec.generate_private_key(ec.SECP256R1())

    # Extract 32-byte scalar
    private_numbers = private_key.private_numbers()
    scalar = private_numbers.private_value.to_bytes(32, byteorder="big")

    # Build Trussed-format key blob: flags(u16 BE) + kind(u16 BE) + key_material
    # flags = 0x0002 (SENSITIVE), kind = 0x0005 (P256)
    trussed_key_blob = struct.pack(">HH", 0x0002, 0x0005) + scalar

    # Build self-signed X.509 certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Solo2 Hacker Self-Attestation"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=36500))  # ~100 years
        .sign(private_key, hashes.SHA256())
    )

    der_certificate = cert.public_bytes(serialization.Encoding.DER)

    return trussed_key_blob, der_certificate
