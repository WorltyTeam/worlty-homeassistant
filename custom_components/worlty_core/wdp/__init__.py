"""Worlty authentication and display definitions selected from the WDP public source."""

from .external_auth import (
    encode_local_auth_transcript,
    encode_local_pairing_proposal_transcript,
    local_pairing_proposal_digest_hex,
    local_api_scope_mask,
)
from .generated_external_platform import (
    INTEGRATION_CONTRACT_PROFILE,
    MEASUREMENT_UNITS,
    LOCALIZATION_REGISTRY,
    BLE_SERVICE_NAME,
    CAPABILITY_BY_ID,
)

WDP_SOURCE_SHA = "17b1c45741211331c8e0cd94b6ea58860ca02fda"

__all__ = [
    "encode_local_auth_transcript",
    "encode_local_pairing_proposal_transcript",
    "local_pairing_proposal_digest_hex",
    "local_api_scope_mask",
    "INTEGRATION_CONTRACT_PROFILE",
    "MEASUREMENT_UNITS",
    "LOCALIZATION_REGISTRY",
    "BLE_SERVICE_NAME",
    "CAPABILITY_BY_ID",
    "WDP_SOURCE_SHA",
]
