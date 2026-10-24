"""Strict TLS validation for the hub Local API."""

from __future__ import annotations

import datetime as dt
import hmac
import ipaddress
import re
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID

from .crypto import certificate_spki_sha256
from .errors import (
    WorltyAuthenticationError,
    WorltyConnectionError,
    WorltyIdentityMismatchError,
    WorltyPinMismatchError,
)

PACKAGED_CA_BUNDLE = (
    Path(__file__).with_name("certificates") / "worlty_local_api_root.pem"
)
_TLS12_ECDSA_AEAD_CIPHERS = ":".join(
    (
        "ECDHE-ECDSA-AES128-GCM-SHA256",
        "ECDHE-ECDSA-AES256-GCM-SHA384",
        "ECDHE-ECDSA-CHACHA20-POLY1305",
    )
)
_MDNS_HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+local$"
)


def normalize_tls_hostname(value: str) -> str:
    """Return a canonical DNS hostname suitable for SNI and SAN validation."""
    hostname = value.strip().rstrip(".").lower()
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise WorltyAuthenticationError(
            "PAD TLS identity requires its mDNS hostname, not an IP address"
        )
    if not _MDNS_HOSTNAME_PATTERN.fullmatch(hostname):
        raise WorltyAuthenticationError("PAD TLS mDNS hostname is invalid")
    return hostname


def create_worlty_tls_context(
    ca_bundle: str | Path | None = None,
) -> ssl.SSLContext:
    """Create a fail-closed client context trusting only the Worlty Root CA."""
    bundle = Path(ca_bundle) if ca_bundle is not None else PACKAGED_CA_BUNDLE
    if not bundle.is_file():
        raise WorltyConnectionError(
            "Worlty Local API Root CA is missing from the signed package"
        )

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.hostname_checks_common_name = False
    context.set_ciphers(_TLS12_ECDSA_AEAD_CIPHERS)
    try:
        context.load_verify_locations(cafile=str(bundle))
    except (OSError, ssl.SSLError) as err:
        raise WorltyConnectionError(
            "Worlty Local API Root CA bundle is invalid"
        ) from err
    return context


def expected_device_uri(target_id: str) -> str:
    """Return the canonical device identity URI for the leaf certificate SAN."""
    if not re.fullmatch(r"[0-9A-F]{12}", target_id):
        raise WorltyAuthenticationError("expected PAD hardware target ID is invalid")
    return f"urn:worlty:device:{target_id}"


def verified_peer_identity(
    certificate_der: bytes,
    *,
    now: dt.datetime | None = None,
) -> tuple[str, str]:
    """Read identity only after TLS chain and hostname validation."""
    try:
        certificate = x509.load_der_x509_certificate(certificate_der)
    except ValueError as err:
        raise WorltyAuthenticationError("PAD TLS leaf is not valid DER") from err

    public_key = certificate.public_key()
    if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
        public_key.curve,
        ec.SECP256R1,
    ):
        raise WorltyAuthenticationError("PAD TLS leaf key must be P-256")

    signature_hash = certificate.signature_hash_algorithm
    if not isinstance(signature_hash, hashes.SHA256):
        raise WorltyAuthenticationError(
            "PAD TLS leaf must use a SHA-256 certificate signature"
        )

    try:
        basic_constraints = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
        key_usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
        extended_key_usage = certificate.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage
        ).value
        subject_alt_name = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
    except x509.ExtensionNotFound as err:
        raise WorltyAuthenticationError(
            "PAD TLS leaf is missing a required X.509 extension"
        ) from err

    if basic_constraints.ca:
        raise WorltyAuthenticationError("PAD TLS leaf must not be a CA")
    if not key_usage.digital_signature:
        raise WorltyAuthenticationError("PAD TLS leaf must permit digital signatures")
    if ExtendedKeyUsageOID.SERVER_AUTH not in extended_key_usage:
        raise WorltyAuthenticationError("PAD TLS leaf is missing the serverAuth EKU")

    uris = subject_alt_name.get_values_for_type(x509.UniformResourceIdentifier)
    if len(uris) != 1 or not re.fullmatch(r"urn:worlty:device:[0-9A-F]{12}", uris[0]):
        raise WorltyAuthenticationError(
            "PAD TLS leaf must have exactly one canonical device URI"
        )

    current_time = now or dt.datetime.now(dt.UTC)
    not_before = certificate.not_valid_before_utc
    not_after = certificate.not_valid_after_utc
    if current_time < not_before or current_time > not_after:
        raise WorltyAuthenticationError("PAD TLS leaf is outside its validity period")

    return uris[0].rsplit(":", 1)[1], certificate_spki_sha256(certificate_der)


def validate_peer_leaf(
    certificate_der: bytes,
    *,
    expected_target_id: str,
    expected_spki_sha256: str,
    now: dt.datetime | None = None,
) -> None:
    """Require identity and pin matches after chain and hostname validation."""
    target_id, actual_pin = verified_peer_identity(certificate_der, now=now)
    expected_device_uri(expected_target_id)
    if not hmac.compare_digest(target_id, expected_target_id):
        raise WorltyIdentityMismatchError("PAD TLS leaf device URI does not match")
    if not hmac.compare_digest(actual_pin, expected_spki_sha256):
        raise WorltyPinMismatchError("PAD TLS SPKI pin mismatch")
