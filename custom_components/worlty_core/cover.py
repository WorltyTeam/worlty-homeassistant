"""Worlty cover entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import WorltyEntity, setup_entry_platform


class WorltyCover(WorltyEntity, CoverEntity):
    """Worlty hub cover with normalized positions."""

    @property
    def current_cover_position(self) -> int | None:
        """Return the current cover position as a percentage."""
        resource = self.resource
        value = resource.state.get("v") if resource is not None else None
        return (
            max(0, min(100, round(value))) if isinstance(value, (int, float)) else None
        )

    @property
    def is_closed(self) -> bool | None:
        """Determine whether the cover is closed from its position."""
        position = self.current_cover_position
        return position == 0 if position is not None else None

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return the HA features supported by this resource."""
        resource = self.resource
        if resource is None or 16 not in resource.capabilities:
            return CoverEntityFeature(0)
        return (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.SET_POSITION
        )

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Send the fully open position."""
        await self.runtime.async_set_state(self.resource_id, {"pos": 100})

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Send the fully closed position."""
        await self.runtime.async_set_state(self.resource_id, {"pos": 0})

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Clamp the requested position to an integer from 0 to 100 and send it."""
        position = max(0, min(100, round(kwargs[ATTR_POSITION])))
        await self.runtime.async_set_state(self.resource_id, {"pos": position})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entities for the configured device protocol."""
    setup_entry_platform(
        entry,
        async_add_entities,
        platform="cover",
        factory=WorltyCover,
    )
