"""Constants for Worlty Core."""

from __future__ import annotations

from typing import Final

from .wdp.generated_external_platform import INTEGRATION_CONTRACT_PROFILE

DOMAIN: Final = "worlty_core"
VERSION: Final = "2.0.0"

CONF_DEVICE_ID: Final = "device_id"
CONF_PAD_NAME: Final = "pad_name"
CONF_PROTOCOL: Final = "protocol"
PROTOCOL_V1: Final = "v1"
PROTOCOL_V2: Final = "v2"
CONF_V1_MODE: Final = "v1_mode"
V1_MODE_BASIC: Final = "basic"
V1_MODE_ADVANCED: Final = "advanced"
CONF_CLIENT_ID: Final = "client_id"
CONF_HA_INSTANCE_ID: Final = "ha_instance_id"
CONF_AUTH_SCOPES: Final = "auth_scopes"
CONF_BINDING_GENERATION: Final = "binding_generation"
CONF_TLS_TARGET_ID: Final = "tls_target_id"
CONF_TLS_HOSTNAME: Final = "tls_hostname"
CONF_TLS_SPKI_SHA256: Final = "tls_spki_sha256"
DEFAULT_PORT: Final = 4433
LOCAL_SUBPROTOCOL: Final = "worlty.device-api.v1"
LOCAL_API_PATH: Final = "/local/v1/device-api"
LOCAL_PAIRING_PATH: Final = "/local/v1/pair"
LOCAL_MAX_MESSAGE_BYTES: Final = 20 * 1024

PLATFORMS: Final = (
    "binary_sensor",
    "sensor",
    "light",
    "switch",
    "event",
    "fan",
    "climate",
    "cover",
    "number",
    "select",
    "time",
    "date",
    "datetime",
    "text",
)

AUTH_SCOPES: Final = ("observe", "control")
LOCAL_API_SCOPES: Final = tuple(INTEGRATION_CONTRACT_PROFILE["localApiScopes"])
EXTERNAL_PLATFORM_ID_HOME_ASSISTANT: Final = 1
