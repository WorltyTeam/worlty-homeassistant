"""Worlty UTF-8 text inputs."""

from homeassistant.components.text import TextEntity

from .input_entity import WorltyInputEntity, setup_input_platform


class WorltyInputText(WorltyInputEntity, TextEntity):
    """String input exposed as an HA text entity."""

    @property
    def native_value(self):
        """Return the current value in its HA native type."""
        return self.value

    @property
    def native_max(self):
        """Return the input field's maximum string length."""
        return self.field.max_length if self.field is not None else 256


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up entities for the configured device protocol."""
    setup_input_platform(
        entry, async_add_entities, platform="text", factory=WorltyInputText
    )
