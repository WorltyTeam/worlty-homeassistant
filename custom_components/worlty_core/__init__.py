"""Local push integration for classic pads and Worlty hubs."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HassJob, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_DEVICE_ID,
    CONF_PROTOCOL,
    DOMAIN,
    PLATFORMS,
    PROTOCOL_V1,
    PROTOCOL_V2,
)
from .coordinator import async_get_coordinator
from .errors import WorltyAuthenticationError
from .naming import configured_pad_name, parent_device_name
from .runtime import WorltyRuntime

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Validate existing shared storage and initialize the integration."""
    await async_get_coordinator(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start the device protocol without sharing authentication state."""
    if entry.data.get(CONF_PROTOCOL) == PROTOCOL_V1:
        from .v1 import async_setup_entry as setup_v1

        return await setup_v1(hass, entry)

    coordinator = await async_get_coordinator(hass)
    runtime = WorltyRuntime(hass, entry)
    entry.runtime_data = runtime
    coordinator.register_runtime(runtime)
    started = False
    forwarding = False
    try:
        await runtime.async_start()
        started = True
        pad_id = str(entry.data[CONF_DEVICE_ID])
        pad_name = configured_pad_name(
            entry.data,
            title=entry.title,
            pad_id=pad_id,
        )
        dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, pad_id)},
            manufacturer="Worlty",
            model="월티 허브",
            name=parent_device_name(pad_name, pad_id),
        )
        forwarding = True
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException as err:
        try:
            try:
                if forwarding:
                    try:
                        await hass.config_entries.async_unload_platforms(
                            entry, PLATFORMS
                        )
                    except Exception:
                        _LOGGER.warning(
                            "일부 설정된 월티 허브 플랫폼을 해제하지 못했습니다"
                        )
            finally:
                await runtime.async_stop()
        finally:
            _remove_runtime_registration(hass, entry)
        if not isinstance(err, Exception):
            raise
        if isinstance(err, WorltyAuthenticationError):
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="hub_authentication_failed"
            ) from err
        if not started:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="hub_connection_failed"
            ) from err
        raise

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    entry.async_on_unload(hass.async_add_shutdown_job(HassJob(runtime.async_stop)))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload entity platforms before closing their shared transport."""
    if entry.data.get(CONF_PROTOCOL) == PROTOCOL_V1:
        from .v1 import async_unload_entry as unload_v1

        return await unload_v1(hass, entry)

    runtime: WorltyRuntime = entry.runtime_data
    # HA does not run on_unload callbacks when unloading fails.
    # Keep runtime and registration ownership until retry or shutdown completes cleanup.
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await runtime.async_stop()
    _remove_runtime_registration(hass, entry)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Mark the hub protocol while preserving existing trust data and IDs."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        if entry.data.get(CONF_PROTOCOL, PROTOCOL_V2) != PROTOCOL_V2:
            return False
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_PROTOCOL: PROTOCOL_V2}, version=2
        )
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_runtime_registration(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Release only the registration of a fully cleaned-up runtime."""
    domain_data = hass.data.get(DOMAIN)
    coordinator = (
        domain_data.get("coordinator") if isinstance(domain_data, dict) else None
    )
    runtime = getattr(entry, "runtime_data", None)
    if isinstance(runtime, WorltyRuntime) and coordinator is not None:
        coordinator.unregister_runtime(runtime)
    entry.runtime_data = None
