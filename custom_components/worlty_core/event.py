"""Worlty event entities."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import WorltyEntity, setup_entry_platform
from .model import WorltyModelChange


class WorltyEvent(WorltyEntity, EventEntity):
    """Hub state updates exposed as triggered events."""

    _attr_event_types = ["triggered"]

    def __init__(self, runtime, resource_id: str) -> None:
        """Retain the current epoch and state revision as the event baseline."""
        super().__init__(runtime, resource_id)
        resource = self.resource
        self._last_epoch = runtime.device_epoch
        self._last_state_revision = (
            resource.state_revision if resource is not None else 0
        )

    def _handle_model_change(self, change: WorltyModelChange) -> None:
        resource = self.resource
        if resource is not None and self.resource_id in change.all_ids:
            epoch = self.runtime.device_epoch
            revision = resource.state_revision
            # Metadata changes and reconnect or new-epoch baselines must not replay
            # events.
            if (
                self.resource_id in change.updated
                and self.runtime.connected
                and epoch is not None
                and epoch == self._last_epoch
                and revision > self._last_state_revision
            ):
                self._trigger_event("triggered", {"value": resource.state.get("v")})
            self._last_epoch = epoch
            self._last_state_revision = revision
        super()._handle_model_change(change)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entities for the configured device protocol."""
    setup_entry_platform(
        entry,
        async_add_entities,
        platform="event",
        factory=WorltyEvent,
    )
