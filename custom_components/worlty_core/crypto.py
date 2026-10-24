"""Cryptographic helpers for the Local Device API."""

from __future__ import annotations

import base64
import binascii
import hashlib

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
)

from .wdp.external_auth import (
    encode_local_auth_transcript,
)


def base64url_encode(value: bytes) -> str:
    """Encode bytes as unpadded Base64url."""
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def base64url_decode(value: str) -> bytes:
    """Decode unpadded Base64url with strict alphabet validation."""
    if not isinstance(value, str):
        raise ValueError("base64url value must be a string")
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as err:
        raise ValueError("invalid base64url value") from err


def generate_private_key_pem() -> str:
    """Generate a local HA P-256 client identity."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def load_private_key(private_key_pem: str) -> ec.EllipticCurvePrivateKey:
    """Load a client key and verify that it is P-256."""
    key = serialization.load_pem_private_key(
        private_key_pem.encode("ascii"), password=None
    )
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve,
        ec.SECP256R1,
    ):
        raise ValueError("Worlty client key must be P-256")
    return key


def raw_public_key_base64url(private_key_pem: str) -> str:
    """Return the SEC1 public key encoded for connection frames."""
    key = load_private_key(private_key_pem)
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return base64url_encode(raw)


def raw_public_key_sha256(private_key_pem: str) -> str:
    """Return the lowercase SHA-256 digest of the raw SEC1 binding key."""
    key = load_private_key(private_key_pem)
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return hashlib.sha256(raw).hexdigest()


def build_auth_transcript(
    *,
    protocol_version: str,
    session_id: str,
    pad_id: str,
    server_nonce: str,
    client_nonce: str,
    client_id: str,
    platform_id: int,
    platform_instance_id: str,
    requested_scope_mask: int,
    binding_generation: str,
) -> bytes:
    """Build the exact Local API transcript to sign."""
    return encode_local_auth_transcript(
        {
            "protocol_version": protocol_version,
            "pad_id": pad_id,
            "client_id": client_id,
            "platform_id": platform_id,
            "platform_instance_id": platform_instance_id,
            "server_nonce": server_nonce,
            "client_nonce": client_nonce,
            "session_id": session_id,
            "requested_scope_mask": requested_scope_mask,
            "binding_generation": binding_generation,
        }
    )


def sign_auth_transcript(private_key_pem: str, transcript: bytes) -> str:
    """Sign the transcript and return a fixed-width raw r||s signature."""
    key = load_private_key(private_key_pem)
    der_signature = key.sign(transcript, ec.ECDSA(hashes.SHA256()))
    r_value, s_value = decode_dss_signature(der_signature)
    raw_signature = r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")
    return base64url_encode(raw_signature)


def certificate_spki_sha256(certificate_der: bytes) -> str:
    """Return the canonical SubjectPublicKeyInfo SHA-256 as lowercase hex."""
    certificate = x509.load_der_x509_certificate(certificate_der)
    spki = certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(spki).hexdigest()
