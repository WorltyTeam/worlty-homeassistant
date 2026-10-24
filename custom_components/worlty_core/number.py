"""Worlty numeric inputs and classic pad number support."""

from homeassistant.components.number import NumberEntity, NumberMode

from .const import CONF_PROTOCOL, PROTOCOL_V1
from .input_entity import WorltyInputEntity, setup_input_platform


class WorltyInputNumber(WorltyInputEntity, NumberEntity):
    """Numeric input exposed as an HA number entity."""

    _attr_mode = NumberMode.BOX

    @property
    def native_value(self):
        """Return the current value in its HA native type."""
        return self.value

    @property
    def native_min_value(self):
        """Return the input field's minimum value."""
        return self.field.minimum if self.field is not None else 0

    @property
    def native_max_value(self):
        """Return the input field's maximum value."""
        return self.field.maximum if self.field is not None else 100

    @property
    def native_step(self):
        """Return the input field's adjustment step."""
        return self.field.step if self.field is not None else 1

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement for the current value."""
        return self.field.unit if self.field is not None else None

    async def async_set_native_value(self, value):
        """Forward the HA value as an input field write request."""
        await self.async_set_value(value)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up entities for the configured device protocol."""
    if entry.data.get(CONF_PROTOCOL) == PROTOCOL_V1:
        from .v1.number import async_setup_entry as setup_v1

        await setup_v1(hass, entry, async_add_entities)
        return
    setup_input_platform(
        entry, async_add_entities, platform="number", factory=WorltyInputNumber
    )
