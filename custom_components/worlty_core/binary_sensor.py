"""Worlty binary sensor entities."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PROTOCOL, PROTOCOL_V1
from .entity import WorltyEntity, setup_entry_platform
from .input_entity import WorltyInputEntity, setup_input_platform

_DEVICE_CLASSES = {
    5: BinarySensorDeviceClass.CONNECTIVITY,
    6: BinarySensorDeviceClass.DOOR,
    7: BinarySensorDeviceClass.PROBLEM,
    9: BinarySensorDeviceClass.SMOKE,
    11: BinarySensorDeviceClass.GAS,
    15: BinarySensorDeviceClass.MOISTURE,
    16: BinarySensorDeviceClass.MOTION,
    18: BinarySensorDeviceClass.OCCUPANCY,
    19: BinarySensorDeviceClass.OPENING,
    22: BinarySensorDeviceClass.PRESENCE,
    23: BinarySensorDeviceClass.PROBLEM,
    26: BinarySensorDeviceClass.SMOKE,
    31: BinarySensorDeviceClass.WINDOW,
}


class WorltyInputBinarySensor(WorltyInputEntity, BinarySensorEntity):
    """Read-only boolean input capability."""

    @property
    def is_on(self) -> bool | None:
        """Return the current power state."""
        return self.value


class WorltyBinarySensor(WorltyEntity, BinarySensorEntity):
    """Binary state reported by a Worlty hub."""

    @property
    def is_on(self) -> bool | None:
        """Return the current power state."""
        resource = self.resource
        value = resource.state.get("v") if resource is not None else None
        return bool(value) if isinstance(value, (bool, int, float)) else None

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        """Return the HA device class for this resource."""
        resource = self.resource
        return (
            _DEVICE_CLASSES.get(resource.entity_class) if resource is not None else None
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entities for the configured device protocol."""
    if entry.data.get(CONF_PROTOCOL) == PROTOCOL_V1:
        from .v1.binary_sensor import async_setup_entry as setup_v1

        await setup_v1(hass, entry, async_add_entities)
        return

    setup_entry_platform(
        entry,
        async_add_entities,
        platform="binary_sensor",
        factory=WorltyBinarySensor,
    )
    setup_input_platform(
        entry,
        async_add_entities,
        platform="binary_sensor",
        factory=WorltyInputBinarySensor,
    )
