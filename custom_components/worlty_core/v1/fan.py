"""Support classic pad fans."""

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from .const import DOMAIN
from .coordinator import WorltyDataCoordinator
from .worlty import WorltyBaseEntity

FAN_MODE_MAPPING = {
    1: "on",
    2: "eco",
    3: "boost",
    4: "sleep",
    5: "auto",
    6: "cook",
    7: "purifier",
    8: "purifier_eco",
    9: "purifier_boost",
    10: "bypass",
}

FAN_SPEED_MAPPING = {
    1: "on",
    2: "auto",
    3: "low",
    4: "medium",
    5: "high",
    6: "weak",
    7: "boost",
    8: "eco",
}

FAN_SWING_MAPPING = {
    0: "off",
    1: "on",
    2: "vertical",
    3: "horizontal",
    4: "both",
}


def fan_mode_to_key(fan_mode: str) -> int | None:
    """Find the device command value for a fan mode name."""
    for key, mode in FAN_MODE_MAPPING.items():
        if mode == fan_mode:
            return key
    return None


def fan_speed_to_key(fan_speed: str) -> int | None:
    """Find the device command value for a fan speed name."""
    for key, speed in FAN_SPEED_MAPPING.items():
        if speed == fan_speed:
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
            WorltyFan(coordinator.api, entity)
            for entity in coordinator.data.get(Platform.FAN, {}).values()
            if entity.get("hide") is not True
        ]

    async_add_entities(entities)

    @callback
    def async_add_entity(entity: dict[str, Any]):
        """Add a newly discovered device if it is eligible for display."""
        if entity.get("hide") is not True:
            async_add_entities([WorltyFan(coordinator.api, entity)])

    config_entry.async_on_unload(
        coordinator.api.register_add_listener(Platform.FAN, async_add_entity)
    )


class WorltyFan(WorltyBaseEntity, FanEntity):
    """Represent classic pad fans."""

    _supported_features: FanEntityFeature
    _fan_modes: list[str]
    _fan_speeds: list[str]
    _fan_swings: list[str]

    def __init__(self, worlty_coordinator, worlty_entity_info) -> None:
        """Initialize the common entity and its device attributes."""
        super().__init__(worlty_coordinator, worlty_entity_info, self._update_entity)
        self._update_entity()

    @callback
    def _update_entity(self) -> None:
        self._supported_features = 0
        self._fan_modes = []
        self._fan_speeds = []
        self._fan_swings = []

        sm = self.worlty_attribute.get("sm", 0)
        for mode_bit, fan_mode in FAN_MODE_MAPPING.items():
            if sm & (1 << mode_bit):
                self._fan_modes.append(fan_mode)

        ssp = self.worlty_attribute.get("ssp", 0)
        for speed_bit, fan_speed in FAN_SPEED_MAPPING.items():
            if ssp & (1 << speed_bit):
                self._fan_speeds.append(fan_speed)

        ssw = self.worlty_attribute.get("ssw", 0)
        for swing_bit, fan_swing in FAN_SWING_MAPPING.items():
            if ssw & (1 << swing_bit):
                self._fan_swings.append(fan_swing)

        features = 0
        if len(self._fan_modes) > 0 or len(self._fan_speeds) > 0:
            features |= FanEntityFeature.TURN_ON
            features |= FanEntityFeature.TURN_OFF
        if len(self._fan_modes) > 0:
            features |= FanEntityFeature.PRESET_MODE
        if len(self._fan_speeds) > 0:
            features |= FanEntityFeature.SET_SPEED
        if len(self._fan_swings) > 0:
            features |= FanEntityFeature.DIRECTION

        self._supported_features = features

    @property
    def is_on(self) -> bool:
        """Return the power state reported by the device."""
        return self.worlty_attribute.get("stt")

    @property
    def supported_features(self) -> FanEntityFeature:
        """Return the Home Assistant features supported by device attributes."""
        return self._supported_features

    @property
    def percentage(self) -> int | None:
        """Convert the reported speed level to a percentage."""
        if len(self._fan_speeds) == 0:
            return None
        if self.is_on:
            sp = self.worlty_attribute.get("sp")
            return ordered_list_item_to_percentage(
                self._fan_speeds, FAN_SPEED_MAPPING.get(sp, "off")
            )
        return 0

    @property
    def speed_count(self) -> int:
        """Return the number of supported speed levels."""
        return len(self._fan_speeds)

    @property
    def preset_mode(self) -> str | None:
        """Return the preset name reported by the device."""
        mode = self.worlty_attribute.get("m")
        return (
            FAN_MODE_MAPPING.get(mode, "on" if self.is_on else "off")
            if len(self._fan_modes) > 0
            else None
        )

    @property
    def preset_modes(self) -> list[str] | None:
        """Return the available presets."""
        return self._fan_modes if len(self._fan_modes) > 0 else None

    async def async_set_percentage(self, percentage: int) -> None:
        """Convert a speed percentage to device speed and power commands."""
        speed_value = (
            percentage_to_ordered_list_item(self._fan_speeds, percentage)
            if percentage > 0
            else "off"
        )
        speed_key = fan_speed_to_key(speed_value)
        payload = {"stt": speed_key is not None}
        if self.preset_mode is not None and self.preset_mode != "off":
            payload["m"] = fan_mode_to_key(self.preset_mode)
        if speed_key is not None:
            payload["sp"] = speed_key
        await self.set_device(**payload)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Request the power state and mode for the selected preset."""
        if preset_mode != "off":
            await self.set_device(stt=True, m=fan_mode_to_key(preset_mode))
        else:
            await self.set_device(stt=False)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Queue a command to turn on the device."""
        speed = (
            percentage_to_ordered_list_item(self._fan_speeds, percentage)
            if percentage is not None and percentage > 0
            else None
        )
        payload = {"stt": True}
        if speed is not None:
            payload["sp"] = fan_speed_to_key(speed)
        elif preset_mode is not None:
            payload["m"] = fan_mode_to_key(preset_mode)
        await self.set_device(**payload)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Queue a command to turn off the device."""
        await self.set_device(stt=False)
