"""Support classic pad number inputs."""

from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, is_app_managed_number
from .coordinator import WorltyDataCoordinator
from .worlty import WorltyBaseEntity

NUMBER_RANGE = {
    "open_prd_ms": [0, 4 * 60 * 60, 1],
    "worlty_offset": [-2, 2, 0.1],
    "temp_offset": [-5, 5, 0.1],
    "temp_target": [10, 30, 0.1],
}


def _is_exported_number(entity: dict[str, Any]) -> bool:
    key = entity.get("cid" if entity.get("fk", 0) != 0 else "did")
    return entity.get("hide") is not True and not is_app_managed_number(
        Platform.NUMBER, key
    )


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
            WorltyNumber(coordinator.api, entity)
            for entity in coordinator.data.get(Platform.NUMBER, {}).values()
            if _is_exported_number(entity)
        ]

    async_add_entities(entities)

    @callback
    def async_add_entity(entity: dict[str, Any]):
        """Add a newly discovered device if it is eligible for display."""
        if _is_exported_number(entity):
            async_add_entities([WorltyNumber(coordinator.api, entity)])

    config_entry.async_on_unload(
        coordinator.api.register_add_listener(Platform.NUMBER, async_add_entity)
    )


class WorltyNumber(WorltyBaseEntity, NumberEntity):
    """Represent classic pad number inputs."""

    def __init__(self, worlty_coordinator, worlty_entity_info) -> None:
        """Initialize the common entity and its device attributes."""
        super().__init__(worlty_coordinator, worlty_entity_info, self._update_entity)
        self._update_entity()

    @callback
    def _update_entity(self) -> None:
        """Use the common entity state without additional computed attributes."""

    @property
    def native_value(self) -> float | None:
        """Convert millisecond attributes to seconds and reject nonnumeric values."""
        try:
            value = float(self.worlty_state)
            if self.worlty_name.endswith("_ms"):
                value /= 1000
            return value
        except (ValueError, TypeError):
            return None

    @property
    def native_max_value(self) -> float | None:
        """Return the maximum value allowed for this input."""
        default_range = NUMBER_RANGE.get(self.worlty_name, [0, 100, 1])
        return default_range[1]

    @property
    def native_min_value(self) -> float | None:
        """Return the minimum value allowed for this input."""
        default_range = NUMBER_RANGE.get(self.worlty_name, [0, 100, 1])
        return default_range[0]

    @property
    def native_step(self) -> float | None:
        """Return the adjustment step for this input."""
        default_range = NUMBER_RANGE.get(self.worlty_name, [0, 100, 1])
        return default_range[2]

    async def async_set_native_value(self, value: float) -> None:
        """Restore millisecond units where needed and request the input value."""
        if self.worlty_name.endswith("_ms"):
            value *= 1000
        await self.set_device(stt=value)
