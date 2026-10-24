"""Support classic pad date inputs."""

from calendar import isleap
from datetime import date, datetime
from typing import Any

from homeassistant.components.date import DateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

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
            WorltyDate(coordinator.api, entity, hass)
            for entity in coordinator.data.get(Platform.DATE, {}).values()
            if entity.get("hide") is not True
        ]

    async_add_entities(entities)

    @callback
    def async_add_entity(entity: dict[str, Any]):
        """Add a newly discovered device if it is eligible for display."""
        if entity.get("hide") is not True:
            async_add_entities([WorltyDate(coordinator.api, entity, hass)])

    config_entry.async_on_unload(
        coordinator.api.register_add_listener(Platform.DATE, async_add_entity)
    )


class WorltyDate(WorltyBaseEntity, DateEntity):
    """Represent classic pad date inputs."""

    def __init__(
        self, worlty_coordinator, worlty_entity_info, hass: HomeAssistant
    ) -> None:
        """Initialize the common entity and its device attributes."""
        self.hass = hass
        super().__init__(worlty_coordinator, worlty_entity_info, self._update_entity)
        self._update_entity()

    @callback
    def _update_entity(self) -> None:
        """Use the common entity state without additional computed attributes."""

    @property
    def native_value(self) -> date | None:
        """Apply the current year to recurring dates, using a leap year for February 29."""
        if self.worlty_state is None:
            return None

        try:
            state = self.worlty_state
            if isinstance(state, str):
                parsed = datetime.strptime(f"{state};2000", "%m/%d;%Y")
                year = dt_util.now().year
                # Use the next leap year for recurring February 29 dates in non-leap years.
                if parsed.month == 2 and parsed.day == 29:
                    while not isleap(year):
                        year += 1
                return parsed.replace(year=year).date()
            return None
        except (ValueError, TypeError):
            return None

    async def async_set_value(self, value: date) -> None:
        """Convert the date to a month-day string and request it on the device."""
        date_string = value.strftime("%m/%d")
        await self.set_device(stt=date_string)
