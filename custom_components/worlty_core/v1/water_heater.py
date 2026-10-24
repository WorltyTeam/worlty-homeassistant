"""Support classic pad water heaters."""

from typing import Any

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_SLEEP,
    HVACMode,
)
from homeassistant.components.water_heater import (
    STATE_GAS,
    STATE_OFF,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import WorltyDataCoordinator
from .worlty import WorltyBaseEntity

HVAC_MODE_MAPPING = {
    0: HVACMode.OFF,
    1: HVACMode.HEAT,
    2: HVACMode.COOL,
    3: HVACMode.AUTO,
    4: HVACMode.DRY,
    5: HVACMode.FAN_ONLY,
    6: PRESET_AWAY,
    7: PRESET_ECO,
    8: PRESET_SLEEP,
    9: PRESET_BOOST,
}


def hvac_mode_to_key(hvac_mode: str) -> int | None:
    """Find the device command value for a Home Assistant operation mode."""
    for key, mode in HVAC_MODE_MAPPING.items():
        if mode == hvac_mode:
            return key
    return None


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
            WorltyWaterHeater(coordinator.api, entity)
            for entity in coordinator.data.get(Platform.WATER_HEATER, {}).values()
            if entity.get("hide") is not True
        ]

    async_add_entities(entities)

    @callback
    def async_add_entity(entity: dict[str, Any]):
        """Add a newly discovered device if it is eligible for display."""
        if entity.get("hide") is not True:
            async_add_entities([WorltyWaterHeater(coordinator.api, entity)])

    config_entry.async_on_unload(
        coordinator.api.register_add_listener(Platform.WATER_HEATER, async_add_entity)
    )


class WorltyWaterHeater(WorltyBaseEntity, WaterHeaterEntity):
    """Represent classic pad water heaters."""

    _supported_features: WaterHeaterEntityFeature
    _hvac_modes: list[HVACMode]

    def __init__(self, worlty_coordinator, worlty_entity_info) -> None:
        """Initialize the common entity and its device attributes."""
        super().__init__(worlty_coordinator, worlty_entity_info, self._update_entity)
        self._update_entity()
        self._enable_turn_on_off_backwards_compatibility = False
        self.wt_last_state: bool = self.worlty_attribute.get("stt", False)

    @callback
    def _update_entity(self) -> None:
        self._supported_features = 0
        self._hvac_modes = [HVACMode.OFF]

        sm = self.worlty_attribute.get("sm", 0)
        for mode_bit, hvac_mode in HVAC_MODE_MAPPING.items():
            if sm & (1 << mode_bit):
                self._hvac_modes.append(hvac_mode)

        features = WaterHeaterEntityFeature.ON_OFF
        features |= WaterHeaterEntityFeature.TARGET_TEMPERATURE
        features |= WaterHeaterEntityFeature.OPERATION_MODE

        if PRESET_AWAY in self._hvac_modes:
            features |= WaterHeaterEntityFeature.AWAY_MODE

        self._supported_features = features

    @property
    def supported_features(self) -> WaterHeaterEntityFeature:
        """Return the Home Assistant features supported by device attributes."""
        return self._supported_features

    @property
    def max_temp(self) -> float:
        """Return the maximum allowed target temperature."""
        return 30

    @property
    def min_temp(self) -> float:
        """Return the minimum allowed target temperature."""
        return 10

    @property
    def target_temperature_step(self) -> float | None:
        """Return the temperature step reported by the device."""
        return self.worlty_attribute.get("ts")

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature reported by the device."""
        return self.worlty_attribute.get("tt")

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature measured by the device."""
        return self.worlty_attribute.get("tc")

    @property
    def temperature_unit(self) -> UnitOfTemperature:
        """Use Celsius for temperature values."""
        return UnitOfTemperature.CELSIUS

    @property
    def is_away_mode_on(self) -> bool:
        """Check both power and operation mode to determine away mode."""
        return self.worlty_attribute.get("stt", False) and hvac_mode_to_key(
            PRESET_AWAY
        ) == self.worlty_attribute.get("m", 0)

    @property
    def current_operation(self) -> str | None:
        """Return gas heating or off based on the power state."""
        return STATE_GAS if self.worlty_attribute.get("stt", False) else STATE_OFF

    @property
    def operation_list(self) -> list[str] | None:
        """Return the available gas heating and off modes."""
        return [STATE_GAS, STATE_OFF]

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Request the target temperature and turn on the device."""
        await self.set_device(stt=True, m=1, tt=kwargs.get(ATTR_TEMPERATURE))

    async def async_turn_away_mode_on(self) -> None:
        """Request that the device turn on in away mode."""
        await self.set_device(stt=True, m=hvac_mode_to_key(PRESET_AWAY))

    async def async_turn_away_mode_off(self) -> None:
        """Leave away mode and request heating mode with power on."""
        await self.set_device(stt=True, m=hvac_mode_to_key(HVACMode.HEAT))

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Request the power state for the selected heating mode."""
        await self.set_device(stt=operation_mode == STATE_GAS)

    async def async_turn_on(self) -> None:
        """Queue a command to turn on the device."""
        await self.set_device(stt=True)

    async def async_turn_off(self) -> None:
        """Queue a command to turn off the device."""
        await self.set_device(stt=False)
