"""Worlty enumeration inputs."""

from homeassistant.components.select import SelectEntity

from .input_entity import WorltyInputEntity, setup_input_platform


class WorltyInputSelect(WorltyInputEntity, SelectEntity):
    """Enumeration input exposed as an HA select entity."""

    @property
    def options(self):
        """Return selectable labels in the current language."""
        return (
            list(self.field.options(self.runtime.hass.config.language))
            if self.field is not None
            else []
        )

    @property
    def current_option(self):
        """Return the selected value."""
        return self.value

    async def async_select_option(self, option):
        """Forward the selected option as an input field write request."""
        await self.async_set_value(option)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up entities for the configured device protocol."""
    setup_input_platform(
        entry, async_add_entities, platform="select", factory=WorltyInputSelect
    )
