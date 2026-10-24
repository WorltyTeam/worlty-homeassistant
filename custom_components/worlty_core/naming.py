"""Consistent display names for multiple hubs in one HA installation."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping

from .const import CONF_PAD_NAME, CONF_TLS_TARGET_ID
from .wdp.generated_external_platform import BLE_SERVICE_NAME


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


def child_device_name(
    pad_name: str,
    resource_name: str,
    pad_id: str,
    *,
    existing_names: Collection[str] = (),
) -> str:
    """Build a name without UUID suffixes; registry IDs preserve identity."""
    name = pad_name.strip()
    resource = resource_name.strip() or "Device"
    base = f"{name} · {resource}"
    return base
