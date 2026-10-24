"""Worlty switch entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PROTOCOL, PROTOCOL_V1
from .entity import WorltyEntity, setup_entry_platform
from .input_entity import WorltyInputEntity, setup_input_platform


class WorltyInputSwitch(WorltyInputEntity, SwitchEntity):
    """Writable boolean input capability."""

    @property
    def is_on(self) -> bool | None:
        """Return the current power state."""
        return self.value

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send a request to turn on the resource."""
        await self.async_set_value(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send a request to turn off the resource."""
        await self.async_set_value(False)


class WorltySwitch(WorltyEntity, SwitchEntity):
    """Binary actuator exposed by a Worlty hub."""

    @property
    def is_on(self) -> bool | None:
        """Return the current power state."""
        resource = self.resource
        value = resource.state.get("v") if resource is not None else None
        return bool(value) if isinstance(value, (bool, int, float)) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send a request to turn on the resource."""
        await self.runtime.async_set_state(self.resource_id, {"v": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send a request to turn off the resource."""
        await self.runtime.async_set_state(self.resource_id, {"v": False})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entities for the configured device protocol."""
    if entry.data.get(CONF_PROTOCOL) == PROTOCOL_V1:
        from .v1.switch import async_setup_entry as setup_v1

        await setup_v1(hass, entry, async_add_entities)
        return

    setup_entry_platform(
        entry,
        async_add_entities,
        platform="switch",
        factory=WorltySwitch,
    )
    setup_input_platform(
        entry, async_add_entities, platform="switch", factory=WorltyInputSwitch
    )
