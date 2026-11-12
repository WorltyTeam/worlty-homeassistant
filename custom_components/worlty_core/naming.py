"""Consistent display names for multiple hubs in one HA installation."""

from __future__ import annotations

import re
from collections.abc import Mapping

from .const import CONF_PAD_NAME, CONF_TLS_TARGET_ID
from .display_name import contentful_name, default_name_with_suffix
from .wdp.generated_external_platform import BLE_SERVICE_NAME, LOCALIZATION_REGISTRY

_LOCALIZED_KEYS = {
    str(item["key"]): item["labels"]
    for item in LOCALIZATION_REGISTRY.get("entries", ())
}
INTERPHONE_TIME_KEYS = frozenset(
    {
        "entity.setting.interphone.open_wait",
        "entity.setting.interphone.auto_open_duration",
        "entity.setting.interphone.reject_window",
    }
)
_INTERPHONE_ENTRANCES = {
    "home": {"ko": "세대현관", "en": "Home entrance"},
    "apartment": {"ko": "공동현관", "en": "Building entrance"},
}


def interphone_time_name(metadata: Mapping[str, object], name: str) -> str:
    """Qualify generic time labels while preserving aliases and complete names."""
    key = metadata.get("name_key")
    entrance = metadata.get("interphone_entrance")
    if (
        not isinstance(key, str)
        or key not in INTERPHONE_TIME_KEYS
        or not isinstance(entrance, str)
        or entrance not in _INTERPHONE_ENTRANCES
        or contentful_name(metadata.get("name")) is not None
        or metadata.get("name_suffix")
    ):
        return name
    defaults = _LOCALIZED_KEYS.get(key, {})
    for language, prefix in _INTERPHONE_ENTRANCES[entrance].items():
        if name == defaults.get(language):
            return f"{prefix} {name}"
    return name


def device_display_name(
    metadata: Mapping[str, object], device_id: str, locale: str
) -> str:
    """Preserve an alias or append the literal suffix to the translated name."""
    name = contentful_name(metadata.get("name"))
    if name is not None:
        return name
    labels = _LOCALIZED_KEYS.get(str(metadata.get("name_key", "")), {})
    name = str(labels.get(locale) or labels.get("en") or device_id)
    return default_name_with_suffix(name, metadata.get("name_suffix"))


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
