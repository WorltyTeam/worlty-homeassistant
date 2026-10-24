"""Worlty fan and ventilation entities."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PROTOCOL, PROTOCOL_V1
from .entity import WorltyEntity, setup_entry_platform

_VENTILATION_MODES = {
    0: "off",
    1: "manual",
    2: "auto",
    3: "sleep",
    4: "heat_exchange",
    5: "bypass",
    6: "air_purify",
}
_VENTILATION_MODE_IDS = {value: key for key, value in _VENTILATION_MODES.items()}


class WorltyFan(WorltyEntity, FanEntity):
    """Fan entity mapping HA percentages to declared ventilation speed levels."""

    def _speeds(self) -> list[int]:
        resource = self.resource
        descriptor = resource.overrides.get("15", {}) if resource is not None else {}
        return [value for value in (1, 2, 3, 5) if value in descriptor.get("ev", [])]

    @property
    def speed_count(self) -> int:
        """Return the number of supported speed levels."""
        return len(self._speeds())

    @property
    def is_on(self) -> bool | None:
        """Return the current power state."""
        resource = self.resource
        value = resource.state.get("v") if resource is not None else None
        return bool(value) if isinstance(value, (bool, int, float)) else None

    @property
    def percentage(self) -> int | None:
        """Convert the current speed level to an HA percentage."""
        resource = self.resource
        value = resource.state.get("speed") if resource is not None else None
        if value == 0 or (resource is not None and resource.state.get("v") is False):
            return 0
        speeds = self._speeds()
        return (
            round(100 * (speeds.index(value) + 1) / len(speeds))
            if value in speeds
            else None
        )

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        resource = self.resource
        value = resource.state.get("vm") if resource is not None else None
        if (
            resource is not None
            and 14 not in resource.capabilities
            and resource.state.get("speed") == 4
        ):
            return "auto"
        return _VENTILATION_MODES.get(int(value)) if isinstance(value, int) else None

    @property
    def preset_modes(self) -> list[str] | None:
        """Return the presets supported by this resource."""
        resource = self.resource
        if resource is None:
            return None
        if 14 not in resource.capabilities:
            return (
                ["auto"]
                if 4 in resource.overrides.get("15", {}).get("ev", [])
                else None
            )
        values = resource.overrides.get("14", {}).get("ev")
        if not isinstance(values, list):
            current = resource.state.get("vm")
            values = [current] if isinstance(current, int) else []
        return [
            _VENTILATION_MODES[value] for value in values if value in _VENTILATION_MODES
        ]

    @property
    def supported_features(self) -> FanEntityFeature:
        """Return the HA features supported by this resource."""
        resource = self.resource
        if resource is None:
            return FanEntityFeature(0)
        features = FanEntityFeature(0)
        if self._speeds() and resource.overrides.get("15", {}).get("ac", 0) & 2:
            features |= FanEntityFeature.SET_SPEED
        if self.preset_modes:
            features |= FanEntityFeature.PRESET_MODE
        if resource.overrides.get("1", {}).get("ac", 0) & 2:
            features |= FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        return features

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Send a request to turn on the resource."""
        if preset_mode == "off":
            if percentage not in (None, 0):
                raise ValueError("OFF conflicts with a positive fan percentage")
            await self.async_turn_off()
            return
        state: dict[str, Any] = {"v": True}
        if (
            percentage is not None
            and self.supported_features & FanEntityFeature.SET_SPEED
        ):
            if percentage == 0:
                await self.async_turn_off()
                return
            state["speed"] = self._speed_for_percentage(percentage)
        if preset_mode is not None and preset_mode in _VENTILATION_MODE_IDS:
            resource = self.resource
            if resource is not None and 14 in resource.capabilities:
                state["vm"] = _VENTILATION_MODE_IDS[preset_mode]
            elif preset_mode == "auto":
                state["speed"] = 4
        await self.runtime.async_set_state(self.resource_id, state)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send a request to turn off the resource."""
        await self.runtime.async_set_state(self.resource_id, {"v": False})

    async def async_set_percentage(self, percentage: int) -> None:
        """Convert the requested percentage to a supported speed and send it."""
        if percentage == 0:
            await self.async_turn_off()
            return
        await self.runtime.async_set_state(
            self.resource_id,
            {"speed": self._speed_for_percentage(percentage)},
        )

    def _speed_for_percentage(self, percentage: int) -> int:
        speeds = self._speeds()
        if (
            isinstance(percentage, bool)
            or not isinstance(percentage, (int, float))
            or not 0 < percentage <= 100
            or not speeds
        ):
            raise ValueError("unsupported fan percentage")
        return speeds[math.ceil(percentage * len(speeds) / 100) - 1]

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Validate and send a supported preset."""
        if preset_mode not in (self.preset_modes or []):
            raise ValueError(f"unsupported ventilation mode: {preset_mode}")
        if preset_mode == "off":
            await self.async_turn_off()
            return
        if self.resource is not None and 14 not in self.resource.capabilities:
            await self.runtime.async_set_state(self.resource_id, {"speed": 4})
            return
        await self.runtime.async_set_state(
            self.resource_id,
            {"vm": _VENTILATION_MODE_IDS[preset_mode]},
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entities for the configured device protocol."""
    if entry.data.get(CONF_PROTOCOL) == PROTOCOL_V1:
        from .v1.fan import async_setup_entry as setup_v1

        await setup_v1(hass, entry, async_add_entities)
        return

    setup_entry_platform(
        entry,
        async_add_entities,
        platform="fan",
        factory=WorltyFan,
    )
