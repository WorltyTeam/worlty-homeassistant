"""Worlty switch entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PROTOCOL, PROTOCOL_V1
from .entity import WorltyEntity, setup_entry_platform
from .input import interphone_modes
from .input_entity import WorltyInputEntity, setup_input_platform
from .model import WorltyModelChange, provider_entity_unique_id


class WorltyInterphoneModeSwitch(WorltyInputEntity, SwitchEntity):
    """Expose one mode while retaining the original transport resource."""

    def __init__(self, runtime: Any, resource_id: str, mode: int) -> None:
        """Give each supported mode an independent HA registry identity."""
        super().__init__(runtime, resource_id, 26)
        self.mode = mode
        self._attr_unique_id = provider_entity_unique_id(
            pad_id=str(runtime.entry.data["device_id"]),
            resource_id=resource_id,
            capability_id=26,
            mode=mode,
        )

    @property
    def available(self) -> bool:
        """Require a current supported mode descriptor and an available resource."""
        return super().available and self.mode in interphone_modes(self.resource)

    @property
    def is_on(self) -> bool | None:
        """Derive all switches from the shared confirmed mode state."""
        resource, field = self.resource, self.field
        if (
            resource is None
            or field is None
            or self.mode not in interphone_modes(resource)
        ):
            return None
        value = resource.state.get("input", {}).get("26")
        supported = field.descriptor.get("ev", ())
        if type(value) is not int or value not in supported:
            return None
        return value == self.mode

    def _entity_name(self) -> str:
        """Keep the source entity name and add the mode's functional label."""
        labels = (
            {1: "열기예약", 2: "자동열기", 3: "거절"}
            if self.runtime.hass.config.language == "ko"
            else {1: "Open once", 2: "Auto open", 3: "Reject"}
        )
        return f"{super()._entity_name()} · {labels[self.mode]}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Include the selected mode along with the original resource identifiers."""
        return {**super().extra_state_attributes, "worlty_interphone_mode": self.mode}

    def _is_current_projection(self) -> bool:
        """Remove only this switch when its supported mode disappears."""
        return super()._is_current_projection() and self.mode in interphone_modes(
            self.resource
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Select this mode through the existing confirmed write path."""
        await self.runtime.async_set_interphone_mode(self.resource_id, self.mode, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Clear this mode only if the device still has it selected."""
        await self.runtime.async_set_interphone_mode(self.resource_id, self.mode, False)


def setup_interphone_switches(
    entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Track supported mode projections without replacing retained switches."""
    runtime = entry.runtime_data

    def modes(resource_id: str) -> set[tuple[str, int]]:
        """List the current visible mode switches for one source entity."""
        resource = runtime.model.entities.get(resource_id)
        if resource is None or not resource.visible or not resource.enabled:
            return set()
        return {(resource_id, mode) for mode in interphone_modes(resource)}

    known = set().union(
        *(
            modes(resource.entity_id)
            for resource in runtime.resources_for_platform("input")
        )
    )
    async_add_entities(
        [
            WorltyInterphoneModeSwitch(runtime, resource_id, mode)
            for resource_id, mode in sorted(known)
        ]
    )

    def model_changed(change: WorltyModelChange) -> None:
        """Register additions while entity listeners handle obsolete projections."""
        affected = {key for key in known if key[0] in change.all_ids}
        current = set().union(*(modes(resource_id) for resource_id in change.all_ids))
        new = current - known
        known.difference_update(affected)
        known.update(current)
        if new:
            async_add_entities(
                [
                    WorltyInterphoneModeSwitch(runtime, resource_id, mode)
                    for resource_id, mode in sorted(new)
                ]
            )

    entry.async_on_unload(runtime.add_model_listener(model_changed))


class WorltyInputSwitch(WorltyInputEntity, SwitchEntity):
    """Writable boolean input capability."""

    @property
    def is_on(self) -> bool | None:
        """Return the current power state."""
        return self.value

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send a request to turn on the resource."""
        await self.async_set_value(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send a request to turn off the resource."""
        await self.async_set_value(False)


class WorltySwitch(WorltyEntity, SwitchEntity):
    """Binary actuator exposed by a Worlty hub."""

    @property
    def is_on(self) -> bool | None:
        """Return the current power state."""
        resource = self.resource
        value = resource.state.get("v") if resource is not None else None
        return bool(value) if isinstance(value, (bool, int, float)) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send a request to turn on the resource."""
        await self.runtime.async_set_state(self.resource_id, {"v": True})

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
        from .v1.switch import async_setup_entry as setup_v1

        await setup_v1(hass, entry, async_add_entities)
        return

    setup_entry_platform(
        entry,
        async_add_entities,
        platform="switch",
        factory=WorltySwitch,
    )
    setup_input_platform(
        entry, async_add_entities, platform="switch", factory=WorltyInputSwitch
    )
    setup_interphone_switches(entry, async_add_entities)
