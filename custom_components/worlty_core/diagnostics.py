"""Redacted diagnostics for Worlty Core."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PROTOCOL, PROTOCOL_V1
from .runtime import WorltyRuntime


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return connection status and counts without device identifiers."""
    if entry.data.get(CONF_PROTOCOL) == PROTOCOL_V1:
        from .v1 import async_get_config_entry_diagnostics as diagnostics_v1

        return await diagnostics_v1(hass, entry)

    runtime: WorltyRuntime = entry.runtime_data
    return {
        "connected": runtime.connected,
        "connection": runtime.connection_diagnostics(),
        "entity_count": len(runtime.model.entities),
        "device_count": len(runtime.model.devices),
    }
