# HA release: allow only observe/control and preserve source authentication serialization.
# Generated from the canonical Local authentication helpers. Do not edit.
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any
from .generated_external_platform import INTEGRATION_CONTRACT_PROFILE

_SHA256_HEX_LENGTH = 64

def _validate_local_h1_transcript_admission(fields: Mapping[str, Any], label: str) -> None:
    active_platform_ids = {
        platform["platform_id"]
        for platform in INTEGRATION_CONTRACT_PROFILE["externalPlatforms"]
        if platform["status"] == "active"
    }
    observe_index = INTEGRATION_CONTRACT_PROFILE["localApiScopes"].index("observe")
    h1_scope_mask = 1 << observe_index
    if fields.get("platform_id") not in active_platform_ids:
        raise ValueError(f"{label} platform id is not active")
    control_mask = 1 << INTEGRATION_CONTRACT_PROFILE["localApiScopes"].index("control")
    allowed = {h1_scope_mask, h1_scope_mask | control_mask}
    if fields.get("requested_scope_mask") not in allowed:
        raise ValueError(f"{label} scope mask is not the H1 grant")


def local_api_scope_mask(scopes: Sequence[str]) -> int:
    """Encode release observe/control scopes as a u32 mask in registry order."""

    if not scopes:
        raise ValueError("at least one Local API scope is required")
    canonical = INTEGRATION_CONTRACT_PROFILE["localApiScopes"]
    seen: set[str] = set()
    mask = 0
    for scope in scopes:
        if scope not in canonical or scope in seen:
            raise ValueError(f"invalid Local API scope: {scope}")
        seen.add(scope)
        mask |= 1 << canonical.index(scope)
    return mask


def _u32be(value: int) -> bytes:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("u32 value is out of range")
    return value.to_bytes(4, "big")


def _u16be(value: int) -> bytes:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 0xFFFF:
        raise ValueError("u16 value is out of range")
    return value.to_bytes(2, "big")


def _sha256_hex_bytes(value: str) -> bytes:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_HEX_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("digest must be lowercase SHA-256 hex")
    return bytes.fromhex(value)


def _u64_hex_bytes(value: str) -> bytes:
    if (
        not isinstance(value, str)
        or len(value) != 16
        or value == "0000000000000000"
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("binding generation must be nonzero lowercase u64 hex")
    return bytes.fromhex(value)


def encode_local_auth_transcript(fields: Mapping[str, Any]) -> bytes:
    """Encode the domain-separated, length-prefixed Local API v1 transcript."""

    profile = INTEGRATION_CONTRACT_PROFILE["security"]["challengeTranscript"]
    _validate_local_h1_transcript_admission(fields, "Local API auth transcript")
    identity_names = (
        "protocol_version",
        "pad_id",
        "client_id",
    )
    session_names = (
        "platform_instance_id",
        "server_nonce",
        "client_nonce",
        "session_id",
    )
    parts = [
        profile["domainPrefix"].encode("ascii"),
        bytes.fromhex(profile["domainTerminatorHex"]),
    ]
    for name in identity_names:
        value = fields.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError(f"{name} is not valid Unicode scalar text") from exc
        parts.extend((_u32be(len(encoded)), encoded))
    parts.append(_u16be(fields.get("platform_id")))
    for name in session_names:
        value = fields.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError(f"{name} is not valid Unicode scalar text") from exc
        parts.extend((_u32be(len(encoded)), encoded))
    parts.extend(
        (
            _u32be(fields.get("requested_scope_mask")),
            _u64_hex_bytes(fields.get("binding_generation")),
        )
    )
    return b"".join(parts)


def encode_local_pairing_proposal_transcript(fields: Mapping[str, Any]) -> bytes:
    """Encode the exact Local API v1 pairing proposal PoP transcript."""

    profile = INTEGRATION_CONTRACT_PROFILE["security"]["pairingProposalTranscript"]
    expected_fields = [
        "protocol_version",
        "pad_id",
        "pairing_window_id",
        "client_id",
        "platform_id",
        "platform_instance_id",
        "server_nonce",
        "client_nonce",
        "session_id",
        "requested_scope_mask",
        "client_public_key_sha256",
    ]
    if (
        profile["version"] != 1
        or profile["domainPrefix"] != "WORLTY-LOCAL-PAIR-V1"
        or profile["domainTerminatorHex"] != "00"
        or profile["textEncoding"] != "UTF-8"
        or profile["textLengthEncoding"] != "u32-big-endian"
        or profile["platformIdEncoding"] != "u16-big-endian"
        or profile["scopeMaskEncoding"] != "u32-big-endian"
        or profile["publicKeyHashEncoding"] != "raw-32-byte-SHA-256"
        or profile["digest"] != "SHA-256-lowercase-hex-of-exact-transcript-bytes"
        or profile["signatureInput"] != "raw-32-byte-transcript-digest"
        or profile["fields"] != expected_fields
    ):
        raise ValueError("unsupported Local API pairing proposal transcript profile")
    _validate_local_h1_transcript_admission(fields, "Local API pairing proposal transcript")
    scope_mask = fields.get("requested_scope_mask")
    parts = [
        profile["domainPrefix"].encode("ascii"),
        bytes.fromhex(profile["domainTerminatorHex"]),
    ]
    for name in ("protocol_version", "pad_id", "pairing_window_id", "client_id"):
        value = fields.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError(f"{name} is not valid Unicode scalar text") from exc
        parts.extend((_u32be(len(encoded)), encoded))
    parts.append(_u16be(fields.get("platform_id")))
    for name in ("platform_instance_id", "server_nonce", "client_nonce", "session_id"):
        value = fields.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError(f"{name} is not valid Unicode scalar text") from exc
        parts.extend((_u32be(len(encoded)), encoded))
    parts.extend(
        (
            _u32be(scope_mask),
            _sha256_hex_bytes(fields.get("client_public_key_sha256")),
        )
    )
    return b"".join(parts)


def local_pairing_proposal_digest_hex(fields: Mapping[str, Any]) -> str:
    """Return the Cloud/PAD correlation digest of exact proposal transcript bytes."""

    return hashlib.sha256(encode_local_pairing_proposal_transcript(fields)).hexdigest()
