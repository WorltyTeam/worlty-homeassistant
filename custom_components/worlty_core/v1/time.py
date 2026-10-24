"""Support classic pad time inputs."""

import re
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.sun import get_astral_event_date
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
            WorltyTime(coordinator.api, entity, hass)
            for entity in coordinator.data.get(Platform.TIME, {}).values()
            if entity.get("hide") is not True
        ]

    async_add_entities(entities)

    @callback
    def async_add_entity(entity: dict[str, Any]):
        """Add a newly discovered device if it is eligible for display."""
        if entity.get("hide") is not True:
            async_add_entities([WorltyTime(coordinator.api, entity, hass)])

    config_entry.async_on_unload(
        coordinator.api.register_add_listener(Platform.TIME, async_add_entity)
    )


class WorltyTime(WorltyBaseEntity, TimeEntity):
    """Represent classic pad time inputs."""

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
    def native_value(self) -> time | None:
        """Convert solar keywords and ordinary time strings to time values."""
        if self.worlty_state is None:
            return None

        try:
            state = self.worlty_state
            if isinstance(state, str):
                special = self._parse_special_time(state)
                if special is not None:
                    return special
                return time.fromisoformat(state)
            return None
        except (ValueError, TypeError):
            return None

    def _parse_special_time(self, state: str) -> time | None:
        """Resolve solar and clock keywords with an optional time offset."""
        now = dt_util.now()

        match = re.match(r"^(sunset|sunrise|noon|midnight)([+-]\d{1,2}:\d{2})?$", state)
        if match:
            keyword, offset_str = match.groups()
            offset = timedelta(0)

            if offset_str:
                offset = self._parse_time_offset(offset_str)

            base_time = None
            if keyword in {"sunset", "sunrise"}:
                base_time = get_astral_event_date(self.hass, keyword, now.date())
                if base_time is not None:
                    return dt_util.as_local(base_time + offset).time()
            elif keyword == "noon":
                base_time = datetime.combine(now.date(), time(12, 0))
            elif keyword == "midnight":
                base_time = datetime.combine(now.date(), time(0, 0))

            if base_time:
                adjusted_time = (base_time + offset).time()
                return adjusted_time
        return None

    def _parse_time_offset(self, offset_str: str) -> timedelta:
        sign = 1 if "+" in offset_str else -1
        hours, minutes = map(int, offset_str[1:].split(":"))
        return timedelta(hours=hours, minutes=minutes) * sign

    async def async_set_value(self, value: time) -> None:
        """Convert the time to an hour-minute string and request it on the device."""
        time_string = value.strftime("%H:%M")
        await self.set_device(stt=time_string)
