"""Support classic pad climate entities."""

from typing import Any

from homeassistant.components.climate import (
    FAN_AUTO,
    FAN_DIFFUSE,
    FAN_FOCUS,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_OFF,
    FAN_ON,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_NONE,
    PRESET_SLEEP,
    SWING_BOTH,
    SWING_HORIZONTAL,
    SWING_OFF,
    SWING_ON,
    SWING_VERTICAL,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
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
}


FAN_MODE_MAPPING = {
    0: FAN_OFF,
    1: FAN_ON,
    2: FAN_AUTO,
    3: FAN_LOW,
    4: FAN_MEDIUM,
    5: FAN_HIGH,
    6: FAN_FOCUS,
    7: FAN_DIFFUSE,
    8: "natural",
    9: "weak",
    10: "turbo",
}

PRESET_MODE_MAPPING = {
    6: PRESET_AWAY,
    7: PRESET_ECO,
    8: PRESET_SLEEP,
    9: PRESET_BOOST,
}

SWING_MODE_MAPPING = {
    0: SWING_OFF,
    1: SWING_ON,
    2: SWING_VERTICAL,
    3: SWING_HORIZONTAL,
    4: SWING_BOTH,
}

CLIMATE_ACTION_MAPPING = {
    0: HVACAction.OFF,
    1: HVACAction.HEATING,
    2: HVACAction.COOLING,
    4: HVACAction.DRYING,
    5: HVACAction.FAN,
    10: HVACAction.IDLE,
}


def hvac_mode_to_key(hvac_mode: HVACMode) -> int | None:
    """Find the device command value for a Home Assistant operation mode."""
    for key, mode in HVAC_MODE_MAPPING.items():
        if mode == hvac_mode:
            return key
    return None


def fan_mode_to_key(fan_mode: str) -> int | None:
    """Find the device command value for a fan mode name."""
    for key, mode in FAN_MODE_MAPPING.items():
        if mode == fan_mode:
            return key
    return None


def preset_mode_to_key(preset_mode: str) -> int | None:
    """Find the device command value for a preset name."""
    for key, mode in PRESET_MODE_MAPPING.items():
        if mode == preset_mode:
            return key
    return None


def swing_mode_to_key(swing_mode: str) -> int | None:
    """Find the device command value for a swing mode name."""
    for key, mode in SWING_MODE_MAPPING.items():
        if mode == swing_mode:
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
            WorltyClimate(coordinator.api, entity)
            for entity in coordinator.data.get(Platform.CLIMATE, {}).values()
            if entity.get("hide") is not True
        ]

    async_add_entities(entities)

    @callback
    def async_add_entity(entity: dict[str, Any]):
        """Add a newly discovered device if it is eligible for display."""
        if entity.get("hide") is not True:
            async_add_entities([WorltyClimate(coordinator.api, entity)])

    config_entry.async_on_unload(
        coordinator.api.register_add_listener(Platform.CLIMATE, async_add_entity)
    )


class WorltyClimate(WorltyBaseEntity, ClimateEntity):
    """Represent classic pad climate entities."""

    _supported_features: ClimateEntityFeature
    _hvac_modes: list[HVACMode]
    _preset_modes: list[str]
    _fan_modes: list[str]
    _swing_modes: list[str]

    def __init__(self, worlty_coordinator, worlty_entity_info) -> None:
        """Initialize the common entity and its device attributes."""
        super().__init__(worlty_coordinator, worlty_entity_info, self._update_entity)
        self.wt_last_mode: HVACMode | None = None
        self._update_entity()
        self._enable_turn_on_off_backwards_compatibility = False

    @callback
    def _update_entity(self) -> None:
        self._supported_features = 0
        self._hvac_modes = [HVACMode.OFF]
        self._preset_modes = [PRESET_NONE]
        self._fan_modes = []
        self._swing_modes = []

        sm = self.worlty_attribute.get("sm", 0)
        for mode_bit, hvac_mode in HVAC_MODE_MAPPING.items():
            if sm & (1 << mode_bit) and hvac_mode not in self._hvac_modes:
                self._hvac_modes.append(hvac_mode)

        self.wt_last_state = self.worlty_attribute.get("stt", False)
        reported_mode = HVAC_MODE_MAPPING.get(self.worlty_attribute.get("m"))
        if reported_mode is not None and reported_mode != HVACMode.OFF:
            self.wt_last_mode = reported_mode

        for preset_bit, preset_mode in PRESET_MODE_MAPPING.items():
            if sm & (1 << preset_bit) and preset_mode not in self._preset_modes:
                self._preset_modes.append(preset_mode)

        sf = self.worlty_attribute.get("sf", 0)
        for fan_bit, fan_mode in FAN_MODE_MAPPING.items():
            if sf & (1 << fan_bit) and fan_mode not in self._fan_modes:
                self._fan_modes.append(fan_mode)

        ssw = self.worlty_attribute.get("ssw", 0)
        for swing_bit, swing_mode in SWING_MODE_MAPPING.items():
            if ssw & (1 << swing_bit) and swing_mode not in self._swing_modes:
                self._swing_modes.append(swing_mode)

        tt, tc = self.target_temperature, self.current_temperature
        ht, hc = self.target_humidity, self.current_humidity

        features = ClimateEntityFeature.TURN_ON
        features |= ClimateEntityFeature.TURN_OFF
        if tt is not None or tc is not None:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if ht is not None or hc is not None:
            features |= ClimateEntityFeature.TARGET_HUMIDITY
        if len(self._preset_modes) > 1:
            features |= ClimateEntityFeature.PRESET_MODE
        if len(self._fan_modes) > 0:
            features |= ClimateEntityFeature.FAN_MODE
        if len(self._swing_modes) > 0:
            features |= ClimateEntityFeature.SWING_MODE
        self._supported_features = features

    @property
    def hvac_action(self) -> HVACAction:
        """Convert the reported HVAC action to a Home Assistant state."""
        action = self.worlty_attribute.get("a")
        state = self.worlty_attribute.get("stt", False)
        if action is None:
            return None
        return (
            CLIMATE_ACTION_MAPPING.get(action, HVACAction.IDLE)
            if state
            else HVACAction.OFF
        )

    def _active_hvac_mode(self) -> HVACMode | None:
        """Resolve HVAC mode from the report, previous mode, or current action."""
        reported_mode = HVAC_MODE_MAPPING.get(self.worlty_attribute.get("m"))
        if reported_mode is not None and reported_mode != HVACMode.OFF:
            return reported_mode
        supported = [mode for mode in self._hvac_modes if mode != HVACMode.OFF]
        if self.wt_last_mode in supported:
            return self.wt_last_mode
        if self.worlty_attribute.get("stt", False):
            action_mode = {
                1: HVACMode.HEAT,
                2: HVACMode.COOL,
                4: HVACMode.DRY,
                5: HVACMode.FAN_ONLY,
            }.get(self.worlty_attribute.get("a"))
            if action_mode in supported:
                return action_mode
        return supported[0] if len(supported) == 1 else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Distinguish an off device from a running device with an unknown mode."""
        if not self.worlty_attribute.get("stt", False):
            return HVACMode.OFF
        return self._active_hvac_mode()

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the HVAC modes supported by the device."""
        return self._hvac_modes

    @property
    def preset_mode(self) -> str | None:
        """Return the preset name reported by the device."""
        mode = self.worlty_attribute.get("m", 0)
        return PRESET_MODE_MAPPING.get(mode, PRESET_NONE)

    @property
    def preset_modes(self) -> list[str]:
        """Return the available presets."""
        return self._preset_modes

    @property
    def fan_mode(self) -> str | None:
        """Return the fan mode name reported by the device."""
        fan = self.worlty_attribute.get("f", 0)
        fan_mode = FAN_MODE_MAPPING.get(fan, FAN_OFF)
        return fan_mode

    @property
    def fan_modes(self) -> list[str]:
        """Return the available fan modes."""
        return self._fan_modes

    @property
    def swing_mode(self) -> str | None:
        """Return the swing mode name reported by the device."""
        swing = self.worlty_attribute.get("sw", 0)
        swing_mode = SWING_MODE_MAPPING.get(swing, SWING_OFF)
        return swing_mode

    @property
    def swing_modes(self) -> list[str]:
        """Return the available swing modes."""
        return self._swing_modes

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the Home Assistant features supported by device attributes."""
        return self._supported_features

    @property
    def temperature_unit(self) -> UnitOfTemperature:
        """Use Celsius for temperature values."""
        return UnitOfTemperature.CELSIUS

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
    def target_humidity(self) -> float | None:
        """Return the target humidity reported by the device."""
        return self.worlty_attribute.get("ht")

    @property
    def current_humidity(self) -> float | None:
        """Return the current humidity measured by the device."""
        return self.worlty_attribute.get("hc")

    async def async_turn_on(self) -> None:
        """Queue a command to turn on the device."""
        await self.set_device(stt=True)

    async def async_turn_off(self) -> None:
        """Queue a command to turn off the device."""
        await self.set_device(stt=False)

    async def async_toggle(self) -> None:
        """Request the opposite of the last reported power state."""
        await self.set_device(stt=not self.wt_last_state)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Convert the selected HVAC mode to a device command and request it."""
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return None
        await self.set_device(stt=True, m=hvac_mode_to_key(hvac_mode))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Request the power state and mode for the selected preset."""
        hvac_mode = preset_mode_to_key(preset_mode)
        if preset_mode == PRESET_NONE:
            restore_mode = self._active_hvac_mode()
            if restore_mode is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="classic_preset_mode_unknown",
                )
            hvac_mode = hvac_mode_to_key(restore_mode)
        if hvac_mode is None:
            return None
        await self.set_device(stt=True, m=hvac_mode)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Request the selected fan mode and turn on the device."""
        if self.hvac_mode == HVACMode.AUTO:
            return None
        await self.set_device(stt=True, f=fan_mode_to_key(fan_mode))

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Request the selected swing mode and turn on the device."""
        await self.set_device(stt=True, sw=swing_mode_to_key(swing_mode))

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Request the target temperature and turn on the device."""
        if self.hvac_mode == HVACMode.FAN_ONLY:
            return None
        await self.set_device(stt=True, tt=kwargs.get(ATTR_TEMPERATURE))

    async def async_set_humidity(self, humidity: int) -> None:
        """Request the target humidity and turn on the device."""
        if self.hvac_mode == HVACMode.FAN_ONLY:
            return None
        await self.set_device(stt=True, ht=humidity)
