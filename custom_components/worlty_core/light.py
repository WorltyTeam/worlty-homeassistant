"""Worlty light entities."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PROTOCOL, PROTOCOL_V1
from .entity import WorltyEntity, setup_entry_platform


class WorltyLight(WorltyEntity, LightEntity):
    """Hub light with percentage or device-specific brightness levels."""

    def _level_limits(self) -> tuple[int, int] | None:
        resource = self.resource
        if resource is None or 31 not in resource.capabilities:
            return None
        descriptor = resource.overrides.get("31", {})
        minimum, maximum = descriptor.get("mn", 0), descriptor.get("mx")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or int(value) != value
            for value in (minimum, maximum)
        ):
            return None
        if minimum < 0 or maximum < 1 or minimum > maximum:
            return None
        return int(minimum), int(maximum)

    @property
    def is_on(self) -> bool | None:
        """Return the current power state."""
        resource = self.resource
        value = resource.state.get("v") if resource is not None else None
        return bool(value) if isinstance(value, (bool, int, float)) else None

    @property
    def brightness(self) -> int | None:
        """Convert device brightness to the HA brightness range."""
        resource = self.resource
        if resource is None:
            return None
        if 2 in resource.capabilities:
            value = resource.state.get("br")
            if not isinstance(value, (int, float)):
                return None
            return round(max(0, min(100, value)) * 255 / 100)
        limits = self._level_limits()
        value = resource.state.get("br_level")
        if (
            limits is None
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or int(value) != value
        ):
            return None
        return round(max(0, min(limits[1], value)) * 255 / limits[1])

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Return HA color modes matching the device's brightness support."""
        resource = self.resource
        return (
            {ColorMode.BRIGHTNESS}
            if resource is not None
            and (2 in resource.capabilities or self._level_limits() is not None)
            else {ColorMode.ONOFF}
        )

    @property
    def color_mode(self) -> ColorMode:
        """Return the HA color mode supported by the resource."""
        return (
            ColorMode.BRIGHTNESS
            if ColorMode.BRIGHTNESS in self.supported_color_modes
            else ColorMode.ONOFF
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send a request to turn on the resource."""
        state: dict[str, Any] = {"v": True}
        if (
            ATTR_BRIGHTNESS in kwargs
            and ColorMode.BRIGHTNESS in self.supported_color_modes
        ):
            brightness = max(0, min(255, int(kwargs[ATTR_BRIGHTNESS])))
            resource = self.resource
            if resource is not None and 2 in resource.capabilities:
                state["br"] = round(brightness * 100 / 255)
            elif (limits := self._level_limits()) is not None:
                if brightness == 0:
                    state["v"] = False
                else:
                    minimum, maximum = limits
                    level = round(brightness * maximum / 255)
                    state["br_level"] = max(minimum, 1, level)
        await self.runtime.async_set_state(self.resource_id, state)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send a request to turn off the resource."""
        await self.runtime.async_set_state(self.resource_id, {"v": False})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entities for the configured device protocol."""
    if entry.data.get(CONF_PROTOCOL) == PROTOCOL_V1:
        from .v1.light import async_setup_entry as setup_v1

        await setup_v1(hass, entry, async_add_entities)
        return

    setup_entry_platform(
        entry,
        async_add_entities,
        platform="light",
        factory=WorltyLight,
    )
