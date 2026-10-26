"""Consistent display names for multiple hubs in one HA installation."""

from __future__ import annotations

import re
from collections.abc import Mapping

from .const import CONF_PAD_NAME, CONF_TLS_TARGET_ID
from .wdp.generated_external_platform import BLE_SERVICE_NAME, LOCALIZATION_REGISTRY

_LOCALIZED_KEYS = {
    str(item["key"]): item["labels"]
    for item in LOCALIZATION_REGISTRY.get("entries", ())
}


def device_display_name(
    metadata: Mapping[str, object], device_id: str, locale: str
) -> str:
    """Preserve an alias or append the literal suffix to the translated name."""
    name = metadata.get("name")
    if isinstance(name, str) and name:
        return name
    labels = _LOCALIZED_KEYS.get(str(metadata.get("name_key", "")), {})
    name = str(labels.get(locale) or labels.get("en") or device_id)
    suffix = metadata.get("name_suffix")
    return name + (suffix if isinstance(suffix, str) else "")


def default_pad_name(target_id: str) -> str:
    """Apply the same WDP BLE default naming rule as the hub SYS component."""
    if not re.fullmatch(r"[0-9A-F]{12}", target_id):
        raise ValueError(
            "verified hardware target ID required for the PAD default name"
        )
    policy = BLE_SERVICE_NAME
    return policy["prefix"] + target_id[-2 * policy["suffixBytes"] :].lower()


def config_entry_title(pad_name: str, pad_id: str) -> str:
    """Return the trimmed hub name without appending identifiers."""
    name = pad_name.strip()
    return name


def configured_pad_name(
    data: Mapping[str, object],
    *,
    title: str,
    pad_id: str,
) -> str:
    """Use the configured display name without appending identifiers."""
    if title.strip():
        return title.strip()
    configured = data.get(CONF_PAD_NAME)
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return default_pad_name(str(data.get(CONF_TLS_TARGET_ID, "")))


def parent_device_name(pad_name: str, pad_id: str) -> str:
    """Return the parent hub's display name."""
    name = pad_name.strip()
    return name
