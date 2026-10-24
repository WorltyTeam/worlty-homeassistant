"""Worlty UTC timestamp inputs."""

from homeassistant.components.datetime import DateTimeEntity

from .input_entity import WorltyInputEntity, setup_input_platform


class WorltyInputDateTime(WorltyInputEntity, DateTimeEntity):
    """Timestamp input exposed as an HA datetime entity."""

    @property
    def native_value(self):
        """Return the current value in its HA native type."""
        return self.value


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up entities for the configured device protocol."""
    setup_input_platform(
        entry, async_add_entities, platform="datetime", factory=WorltyInputDateTime
    )
