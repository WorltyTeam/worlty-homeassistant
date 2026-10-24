"""Ownership of active hub sessions and existing internal storage in HA."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_DEVICE_ID,
    CONF_HA_INSTANCE_ID,
    DOMAIN,
    EXTERNAL_PLATFORM_ID_HOME_ASSISTANT,
)
from .core_state import CoreStateError, restore_stored_state
from .session import PadSessionRegistry

if TYPE_CHECKING:
    from .runtime import WorltyRuntime

DATA_COORDINATOR = "coordinator"
_STORE_VERSION = 1
_STORE_KEY = f"{DOMAIN}.coordinator"


class WorltyCoreCoordinator:
    """Manage runtimes and storage without exposing inactive contracts."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize hub runtimes, sessions, and shared storage."""
        self._runtimes_by_pad: dict[str, WorltyRuntime] = {}
        self.sessions = PadSessionRegistry()
        self._store: Store[dict[str, Any]] = Store(
            hass,
            _STORE_VERSION,
            _STORE_KEY,
            private=True,
        )
        self._loaded = False

    async def async_load(self) -> None:
        """Validate existing shared storage once."""
        if self._loaded:
            return
        restore_stored_state(await self._store.async_load())
        self._loaded = True

    def register_runtime(self, runtime: WorltyRuntime) -> None:
        """Reject duplicate runtimes and bind the hub session."""
        pad_id = str(runtime.entry.data[CONF_DEVICE_ID])
        existing = self._runtimes_by_pad.get(pad_id)
        if existing is not None and existing is not runtime:
            raise CoreStateError("PAD_ALREADY_LOADED")
        pad_session = self.sessions.bind(
            pad_id=pad_id,
            platform_id=EXTERNAL_PLATFORM_ID_HOME_ASSISTANT,
            platform_instance_id=str(runtime.entry.data[CONF_HA_INSTANCE_ID]),
        )
        self._runtimes_by_pad[pad_id] = runtime
        runtime.pad_session = pad_session
        runtime.coordinator = self

    def unregister_runtime(self, runtime: WorltyRuntime) -> None:
        """Release the session and references for the matching registered runtime."""
        pad_id = str(runtime.entry.data[CONF_DEVICE_ID])
        if self._runtimes_by_pad.get(pad_id) is runtime:
            self._runtimes_by_pad.pop(pad_id, None)
            if runtime.pad_session is not None:
                self.sessions.release(runtime.pad_session)
        runtime.pad_session = None
        runtime.coordinator = None


async def async_get_coordinator(hass: HomeAssistant) -> WorltyCoreCoordinator:
    """Reuse the coordinator or initialize it after validating stored state."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    coordinator = domain_data.get(DATA_COORDINATOR)
    if isinstance(coordinator, WorltyCoreCoordinator):
        return coordinator
    coordinator = WorltyCoreCoordinator(hass)
    await coordinator.async_load()
    domain_data[DATA_COORDINATOR] = coordinator
    return coordinator
