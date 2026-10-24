"""Run classic pads within the unified Worlty integration.

Platform mappings and entity implementations originate from
homeassistant_legacy ea176a02e23cdfdb8750a2e11e10a76855f0cf34.
"""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HassJob, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from ..const import CONF_V1_MODE, DOMAIN, V1_MODE_ADVANCED, V1_MODE_BASIC
from .const import LOGGER, is_advanced_entity, is_app_managed_number
from .coordinator import WorltyDataCoordinator
from .transport import (
    CONNECT_TIMEOUT,
    MAX_MESSAGE_BYTES,
    SETUP_TIMEOUT,
    WorltyV1AuthenticationError,
    WorltyV1ConnectionError,
    async_authenticate,
    async_close_writer,
)

V1_PLATFORMS = (
    "binary_sensor",
    "sensor",
    "light",
    "switch",
    "fan",
    "climate",
    "water_heater",
    "number",
    "date",
    "time",
)


async def async_probe_device(
    hass: HomeAssistant, host: str, port: int, access_token: str
) -> dict[str, Any]:
    """Authenticate a temporary TCP connection and return the reported identity.

    Return only validated device metadata. Leave no reconnect, listener, or
    health-check tasks, and do not change registries or config entries.
    """
    writer = None
    try:
        async with asyncio.timeout(SETUP_TIMEOUT):
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, limit=MAX_MESSAGE_BYTES),
                timeout=CONNECT_TIMEOUT,
            )
            return await async_authenticate(reader, writer, access_token)
    except (TimeoutError, OSError) as err:
        raise WorltyV1ConnectionError("클래식 패드에 연결할 수 없습니다") from err
    finally:
        await async_close_writer(writer)


async def _async_dispose(hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
    """Close entry connections and tasks, then clear its in-memory runtime state."""
    try:
        await coordinator.async_close()
    finally:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        entry.runtime_data = None


def _async_apply_entity_mode(
    hass: HomeAssistant, entry: ConfigEntry, device_id: str
) -> None:
    """Apply the selected mode without overriding user-disabled registry entries."""
    registry = er.async_get(hass)
    advanced = entry.options.get(CONF_V1_MODE, V1_MODE_BASIC) == V1_MODE_ADVANCED
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.platform != DOMAIN
            or entity.unique_id.partition(":")[0] != device_id.lower()
        ):
            continue
        if not is_advanced_entity(entity.domain, entity.unique_id.rsplit(":", 1)[-1]):
            continue
        if advanced and entity.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
            registry.async_update_entity(entity.entity_id, disabled_by=None)
        elif not advanced and entity.disabled_by is None:
            registry.async_update_entity(
                entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )


def _async_remove_app_managed_numbers(
    hass: HomeAssistant, entry: ConfigEntry, device_id: str
) -> None:
    """Remove app-managed number inputs for this pad from the entity registry."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        namespace, separator, suffix = entity.unique_id.partition(":")
        identity, key_separator, key = suffix.partition(":")
        if (
            entity.platform == DOMAIN
            and separator
            and namespace == device_id.lower()
            and key_separator
            and 1 <= len(identity.split("_")) <= 2
            and all(part.isdecimal() for part in identity.split("_"))
            and is_app_managed_number(entity.domain, key)
        ):
            registry.async_remove(entity.entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Authenticate once within a timeout and load all classic pad platforms."""
    coordinator = WorltyDataCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"api": coordinator}
    entry.runtime_data = coordinator
    forwarding = False
    try:
        await coordinator.connect()
        await coordinator.async_config_entry_first_refresh()
        _async_remove_app_managed_numbers(
            hass, entry, coordinator.api.worlty_pad.device_id
        )
        _async_apply_entity_mode(hass, entry, coordinator.api.worlty_pad.device_id)
        hass.data[DOMAIN][entry.entry_id]["task"] = entry.async_create_background_task(
            hass, coordinator.api.listen_for_message(), "Worlty classic listener"
        )
        forwarding = True
        await hass.config_entries.async_forward_entry_setups(entry, V1_PLATFORMS)
    except BaseException as err:
        try:
            if forwarding:
                try:
                    await hass.config_entries.async_unload_platforms(
                        entry, V1_PLATFORMS
                    )
                except Exception:
                    LOGGER.warning(
                        "일부 설정된 클래식 패드 플랫폼을 해제하지 못했습니다"
                    )
        finally:
            await _async_dispose(hass, entry, coordinator)
        if isinstance(err, WorltyV1AuthenticationError):
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="classic_authentication_failed",
            ) from err
        if isinstance(err, (WorltyV1ConnectionError, OSError, asyncio.TimeoutError)):
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="classic_connection_failed"
            ) from err
        raise
    entry.async_on_unload(hass.async_add_shutdown_job(HassJob(coordinator.async_close)))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload classic pad platforms and wait for API tasks and sockets to close."""
    coordinator = entry.runtime_data
    if coordinator is None:
        return True
    # Keep the entry for retries and shutdown if platform unloading fails or is cancelled.
    if not await hass.config_entries.async_unload_platforms(entry, V1_PLATFORMS):
        return False
    await coordinator.async_close()
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    entry.runtime_data = None
    return True


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return connection and task counts without authentication data."""
    coordinator = entry.runtime_data
    if coordinator is None:
        return {"protocol": "v1", "connected": False}
    return await coordinator.api.async_get_diagnostics()
