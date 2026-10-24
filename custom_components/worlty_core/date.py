"""Worlty date inputs and classic pad date support."""

from homeassistant.components.date import DateEntity

from .const import CONF_PROTOCOL, PROTOCOL_V1
from .input_entity import WorltyInputEntity, setup_input_platform


class WorltyInputDate(WorltyInputEntity, DateEntity):
    """Date input exposed as an HA date entity."""

    @property
    def native_value(self):
        """Return the current value in its HA native type."""
        return self.value


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up entities for the configured device protocol."""
    if entry.data.get(CONF_PROTOCOL) == PROTOCOL_V1:
        from .v1.date import async_setup_entry as setup_v1

        await setup_v1(hass, entry, async_add_entities)
        return
    setup_input_platform(
        entry, async_add_entities, platform="date", factory=WorltyInputDate
    )
