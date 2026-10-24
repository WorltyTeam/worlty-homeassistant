"""Support classic pad sensors."""

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

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
            WorltySensor(coordinator.api, entity)
            for entity in coordinator.data.get(Platform.SENSOR, {}).values()
            if entity.get("hide") is not True
        ]

    async_add_entities(entities)

    @callback
    def async_add_entity(entity: dict[str, Any]):
        """Add a newly discovered device if it is eligible for display."""
        if entity.get("hide") is not True:
            async_add_entities([WorltySensor(coordinator.api, entity)])

    config_entry.async_on_unload(
        coordinator.api.register_add_listener(Platform.SENSOR, async_add_entity)
    )


class WorltySensor(WorltyBaseEntity, SensorEntity):
    """Represent classic pad sensors."""

    def __init__(self, worlty_coordinator, worlty_entity_info) -> None:
        """Initialize the common entity and its device attributes."""
        super().__init__(worlty_coordinator, worlty_entity_info, self._update_entity)
        self._update_entity()

    @callback
    def _update_entity(self) -> None:
        """Use the common entity state without additional computed attributes."""

    @property
    def native_value(self) -> StateType:
        """Apply time zones to timestamps and validate numeric sensor values."""
        if self.worlty_state is None:
            return None

        tz = timezone(timedelta(hours=9))
        if self.worlty_class == 45:
            if self.worlty_state in ("-", ""):
                return None
            try:
                return datetime.fromtimestamp(int(self.worlty_state), tz)
            except (ValueError, TypeError, OSError):
                return None

        try:
            value = float(self.worlty_state)
            if self.worlty_name.endswith("_ms"):
                value /= 1000
            return value
        except (ValueError, TypeError):
            pass

        if self.worlty_state == "-":
            return None

        # HA requires numeric values for several device classes even without units.
        # A device may briefly report text such as "updated" instead of a number.
        # Return None in that case to avoid validation errors.
        if (
            self.entity_description is not None
            and getattr(self.entity_description, "device_class", None) is not None
        ):
            return None

        return self.worlty_state

    @property
    def state_class(self) -> SensorStateClass | None:
        """Return the statistical classification for the sensor value."""
        if self.worlty_name in ["usage"]:
            return SensorStateClass.MEASUREMENT
        return None
