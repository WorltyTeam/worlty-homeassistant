"""Worlty climate entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PROTOCOL, PROTOCOL_V1
from .entity import WorltyEntity, setup_entry_platform
from .wdp.generated_external_platform import CAPABILITY_BY_ID

_HVAC_MODES = {
    0: HVACMode.OFF,
    1: HVACMode.HEAT,
    2: HVACMode.COOL,
    3: HVACMode.AUTO,
    4: HVACMode.DRY,
    5: HVACMode.FAN_ONLY,
}
_HVAC_MODE_IDS = {value: key for key, value in _HVAC_MODES.items()}
_HVAC_ACTIONS = {
    0: HVACAction.OFF,
    1: HVACAction.HEATING,
    2: HVACAction.COOLING,
    4: HVACAction.DRYING,
    5: HVACAction.FAN,
    6: HVACAction.FAN,
    10: HVACAction.IDLE,
}
_FAN_MODES = {
    0: "off",
    1: "on",
    2: "auto",
    3: "low",
    4: "medium",
    5: "high",
    6: "focus",
    7: "diffuse",
    8: "natural",
    9: "weak",
    10: "turbo",
}
_FAN_MODE_IDS = {value: key for key, value in _FAN_MODES.items()}
_SWING_MODES = {item["value"]: item["key"] for item in CAPABILITY_BY_ID[30]["values"]}
_SWING_MODE_IDS = {value: key for key, value in _SWING_MODES.items()}


class WorltyClimate(WorltyEntity, ClimateEntity):
    """Worlty hub climate entity mapping WDP mode IDs to HA modes."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self) -> float | None:
        """Return the observed temperature."""
        resource = self.resource
        value = resource.state.get("ct") if resource is not None else None
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        resource = self.resource
        value = resource.state.get("tt") if resource is not None else None
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Map power and operating state to an HA HVAC mode."""
        resource = self.resource
        if resource is None:
            return None
        if resource.state.get("v") is False:
            return HVACMode.OFF
        value = resource.state.get("m")
        if value == 6 and self._supports_away_mode:
            return HVACMode.HEAT
        return _HVAC_MODES.get(int(value)) if isinstance(value, int) else None

    @property
    def _supports_away_mode(self) -> bool:
        resource = self.resource
        if (
            resource is None
            or resource.entity_class != 1
            or 5 not in resource.capabilities
        ):
            return False
        descriptor = resource.overrides.get("5", {})
        values = descriptor.get("ev")
        return (
            descriptor.get("vt") == 3
            and isinstance(values, list)
            and {1, 6}.issubset(values)
        )

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        if not self._supports_away_mode:
            return None
        value = self.resource.state.get("m")
        if type(value) is not int:
            return None
        return PRESET_AWAY if value == 6 else PRESET_NONE if value == 1 else None

    @property
    def preset_modes(self) -> list[str] | None:
        """Return the presets supported by this resource."""
        resource = self.resource
        if (
            not self._supports_away_mode
            or not resource.overrides.get("5", {}).get("ac", 0) & 2
        ):
            return None
        return [PRESET_NONE, PRESET_AWAY]

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the HVAC modes supported by this resource."""
        resource = self.resource
        if resource is None:
            return [HVACMode.OFF]
        values = resource.overrides.get("5", {}).get("ev")
        if not isinstance(values, list):
            current = resource.state.get("m")
            values = [0, current] if isinstance(current, int) else [0]
        modes = [
            _HVAC_MODES[value]
            for value in dict.fromkeys(values)
            if value in _HVAC_MODES
        ]
        if (
            resource.overrides.get("1", {}).get("ac", 0) & 2
            and HVACMode.OFF not in modes
        ):
            modes.insert(0, HVACMode.OFF)
        return modes or [HVACMode.OFF]

    @property
    def hvac_action(self) -> HVACAction | None:
        """Map the operating state to an HA HVAC action."""
        resource = self.resource
        value = resource.state.get("a") if resource is not None else None
        return _HVAC_ACTIONS.get(int(value)) if isinstance(value, int) else None

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode."""
        resource = self.resource
        value = resource.state.get("f") if resource is not None else None
        return _FAN_MODES.get(int(value)) if isinstance(value, int) else None

    @property
    def fan_modes(self) -> list[str] | None:
        """Return the fan modes supported by this resource."""
        resource = self.resource
        if resource is None or 6 not in resource.capabilities:
            return None
        values = resource.overrides.get("6", {}).get("ev")
        if not isinstance(values, list):
            current = resource.state.get("f")
            values = [current] if isinstance(current, int) else []
        return [_FAN_MODES[value] for value in values if value in _FAN_MODES]

    @property
    def swing_mode(self) -> str | None:
        """Return the current swing mode."""
        resource = self.resource
        value = resource.state.get("swing") if resource is not None else None
        return _SWING_MODES.get(value) if type(value) is int else None

    @property
    def swing_modes(self) -> list[str] | None:
        """Return the swing modes supported by this resource."""
        resource = self.resource
        if resource is None or 30 not in resource.capabilities:
            return None
        descriptor = resource.overrides.get("30", {})
        values = descriptor.get("ev")
        if (
            descriptor.get("vt") != 3
            or not descriptor.get("ac", 0) & 2
            or not isinstance(values, list)
        ):
            return None
        return [
            _SWING_MODES[value]
            for value in values
            if type(value) is int and value in _SWING_MODES
        ]

    @property
    def min_temp(self) -> float:
        """Return the minimum target temperature."""
        resource = self.resource
        value = resource.overrides.get("3", {}).get("mn") if resource else None
        return float(value) if isinstance(value, (int, float)) else 5.0

    @property
    def max_temp(self) -> float:
        """Return the maximum target temperature."""
        resource = self.resource
        value = resource.overrides.get("3", {}).get("mx") if resource else None
        return float(value) if isinstance(value, (int, float)) else 35.0

    @property
    def target_temperature_step(self) -> float:
        """Return the target temperature step."""
        resource = self.resource
        value = resource.overrides.get("3", {}).get("st") if resource else None
        return float(value) if isinstance(value, (int, float)) else 1.0

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the HA features supported by this resource."""
        resource = self.resource
        if resource is None:
            return ClimateEntityFeature(0)
        features = ClimateEntityFeature(0)
        if resource.overrides.get("3", {}).get("ac", 0) & 2:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if resource.overrides.get("6", {}).get("ac", 0) & 2:
            features |= ClimateEntityFeature.FAN_MODE
        if self.swing_modes:
            features |= ClimateEntityFeature.SWING_MODE
        if self.preset_modes:
            features |= ClimateEntityFeature.PRESET_MODE
        if resource.overrides.get("1", {}).get("ac", 0) & 2:
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        return features

    async def async_turn_on(self) -> None:
        """Send a request to turn on the resource."""
        await self.runtime.async_set_state(self.resource_id, {"v": True})

    async def async_turn_off(self) -> None:
        """Send a request to turn off the resource."""
        await self.runtime.async_set_state(self.resource_id, {"v": False})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Convert the selected HVAC mode to device control values and send them."""
        if hvac_mode == HVACMode.OFF:
            await self.runtime.async_set_state(self.resource_id, {"v": False})
            return
        mode_id = _HVAC_MODE_IDS.get(hvac_mode)
        if mode_id is None:
            raise ValueError(f"unsupported HVAC mode: {hvac_mode}")
        await self.runtime.async_set_state(
            self.resource_id,
            {"v": True, "m": mode_id},
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Send the requested target temperature."""
        if ATTR_TEMPERATURE not in kwargs:
            return
        await self.runtime.async_set_state(
            self.resource_id,
            {"tt": float(kwargs[ATTR_TEMPERATURE])},
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Validate and send the selected fan mode."""
        if fan_mode not in _FAN_MODE_IDS:
            raise ValueError(f"unsupported fan mode: {fan_mode}")
        await self.runtime.async_set_state(
            self.resource_id,
            {"f": _FAN_MODE_IDS[fan_mode]},
        )

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Validate and send a supported swing mode."""
        if swing_mode not in (self.swing_modes or []):
            raise ValueError(f"unsupported swing mode: {swing_mode}")
        await self.runtime.async_set_state(
            self.resource_id,
            {"swing": _SWING_MODE_IDS[swing_mode]},
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Validate and send a supported preset."""
        if preset_mode not in (self.preset_modes or []):
            raise ValueError(f"unsupported preset mode: {preset_mode}")
        await self.runtime.async_set_state(
            self.resource_id,
            {"m": 6 if preset_mode == PRESET_AWAY else 1},
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entities for the configured device protocol."""
    if entry.data.get(CONF_PROTOCOL) == PROTOCOL_V1:
        from .v1.climate import async_setup_entry as setup_v1

        await setup_v1(hass, entry, async_add_entities)
        return

    setup_entry_platform(
        entry,
        async_add_entities,
        platform="climate",
        factory=WorltyClimate,
    )
