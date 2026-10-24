"""Support classic pad switches."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import WorltyDataCoordinator
from .worlty import WorltyBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add initial entities and register a callback for newly discovered devices."""
    coordinator: WorltyDataCoordinator = hass.data[DOMAIN][config_entry.entry_id]["api"]
    entities = []
    if coordinator.data is not None:
        entities = [
            WorltySwitch(coordinator.api, entity)
            for entity in coordinator.data.get(Platform.SWITCH, {}).values()
            if entity.get("hide") is not True
        ]

    async_add_entities(entities)

    @callback
    def async_add_entity(entity: dict[str, Any]):
        """Add a newly discovered device if it is eligible for display."""
        if entity.get("hide") is not True:
            async_add_entities([WorltySwitch(coordinator.api, entity)])

    config_entry.async_on_unload(
        coordinator.api.register_add_listener(Platform.SWITCH, async_add_entity)
    )


class WorltySwitch(WorltyBaseEntity, SwitchEntity):
    """Represent classic pad switches."""

    def __init__(self, worlty_coordinator, worlty_entity_info) -> None:
        """Initialize the common entity and its device attributes."""
        super().__init__(worlty_coordinator, worlty_entity_info, self._update_entity)
        self._update_entity()

    @callback
    def _update_entity(self) -> None:
        """Use the common entity state without additional computed attributes."""

    @property
    def is_on(self) -> bool:
        """Return the power state reported by the device."""
        return self.worlty_attribute.get("stt", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Queue a command to turn on the device."""
        await self.set_device(stt=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Queue a command to turn off the device."""
        await self.set_device(stt=False)
