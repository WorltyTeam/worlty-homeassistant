"""Worlty sensor entities."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PROTOCOL, PROTOCOL_V1
from .entity import WorltyEntity, setup_entry_platform
from .input_entity import WorltyInputEntity, setup_input_platform

_DEVICE_CLASSES = {
    4: SensorDeviceClass.BATTERY,
    5: SensorDeviceClass.CO2,
    6: SensorDeviceClass.CO,
    7: SensorDeviceClass.CURRENT,
    13: SensorDeviceClass.ENERGY,
    20: SensorDeviceClass.HUMIDITY,
    21: SensorDeviceClass.ILLUMINANCE,
    29: SensorDeviceClass.HUMIDITY,
    31: SensorDeviceClass.PM10,
    32: SensorDeviceClass.PM25,
    34: SensorDeviceClass.POWER,
    44: SensorDeviceClass.TEMPERATURE,
    47: SensorDeviceClass.VOLTAGE,
    48: SensorDeviceClass.VOLUME,
}
_UNITS = {
    4: PERCENTAGE,
    7: UnitOfElectricCurrent.AMPERE,
    13: UnitOfEnergy.KILO_WATT_HOUR,
    20: PERCENTAGE,
    29: PERCENTAGE,
    34: UnitOfPower.WATT,
    44: UnitOfTemperature.CELSIUS,
    48: UnitOfVolume.LITERS,
    61: UnitOfPower.KILO_WATT,
}


class WorltyInputSensor(WorltyInputEntity, SensorEntity):
    """Read-only input capability without HA write services."""

    @property
    def native_value(self):
        """Return the current value in its HA native type."""
        return self.value

    @property
    def device_class(self):
        """Return the HA device class for this resource."""
        return {5: SensorDeviceClass.DATE, 6: SensorDeviceClass.TIMESTAMP}.get(
            self.field.value_type if self.field is not None else 0
        )

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement for the current value."""
        field = self.field
        return field.unit if field is not None and field.value_type == 2 else None


class WorltySensor(WorltyEntity, SensorEntity):
    """Numeric or text observations from a Worlty hub."""

    @property
    def native_value(self) -> bool | int | float | str | None:
        """Return the current value in its HA native type."""
        resource = self.resource
        return resource.state.get("v") if resource is not None else None

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Return the HA device class for this resource."""
        resource = self.resource
        return (
            _DEVICE_CLASSES.get(resource.entity_class) if resource is not None else None
        )

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement for the current value."""
        resource = self.resource
        if resource is None:
            return None
        descriptors = [
            value for value in resource.overrides.values() if value.get("ac", 0) & 1
        ]
        return descriptors[0].get("un") if len(descriptors) == 1 else None

    @property
    def state_class(self) -> SensorStateClass | None:
        """Return the HA state class for the measurement type."""
        resource = self.resource
        if resource is None:
            return None
        descriptors = [
            value for value in resource.overrides.values() if value.get("ac", 0) & 1
        ]
        state_class = descriptors[0].get("sc") if len(descriptors) == 1 else None
        return {
            1: SensorStateClass.MEASUREMENT,
            3: SensorStateClass.TOTAL,
            4: SensorStateClass.TOTAL_INCREASING,
        }.get(state_class)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entities for the configured device protocol."""
    if entry.data.get(CONF_PROTOCOL) == PROTOCOL_V1:
        from .v1.sensor import async_setup_entry as setup_v1

        await setup_v1(hass, entry, async_add_entities)
        return

    setup_entry_platform(
        entry,
        async_add_entities,
        platform="sensor",
        factory=WorltySensor,
    )
    setup_input_platform(
        entry, async_add_entities, platform="sensor", factory=WorltyInputSensor
    )
