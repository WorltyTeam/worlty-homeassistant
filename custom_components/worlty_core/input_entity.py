"""HA views of individual capabilities in hub input entities."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import WorltyEntity
from .input import InputCapability, input_capabilities
from .model import WorltyModelChange, provider_entity_unique_id
from .runtime import WorltyRuntime


class WorltyInputEntity(WorltyEntity):
    """Per-field HA identities that preserve the transport resource ID."""

    def __init__(
        self, runtime: WorltyRuntime, resource_id: str, capability_id: int
    ) -> None:
        """Initialize an input entity from its resource and capability IDs."""
        super().__init__(runtime, resource_id)
        self.capability_id = capability_id
        field = self.field
        self._input_platform = field.platform if field is not None else None
        self._attr_unique_id = provider_entity_unique_id(
            pad_id=str(runtime.entry.data["device_id"]),
            resource_id=resource_id,
            capability_id=capability_id,
        )
        self._attr_entity_category = (
            EntityCategory.CONFIG
            if field is not None and field.writable
            else EntityCategory.DIAGNOSTIC
        )

    @property
    def field(self) -> InputCapability | None:
        """Find this entity's input field in the current resource."""
        resource = self.resource
        return (
            input_capabilities(resource).get(self.capability_id)
            if resource is not None
            else None
        )

    @property
    def available(self) -> bool:
        """Determine availability from the connection and resource state."""
        return (
            super().available
            and self.field is not None
            and self.field.platform == self._input_platform
        )

    @property
    def value(self) -> Any:
        """Decode the input field's raw state to an HA value."""
        resource, field = self.resource, self.field
        if resource is None or field is None:
            return None
        return field.decode(
            resource.state.get("input", {}).get(str(self.capability_id)),
            self.runtime.hass.config.language,
        )

    def _entity_name(self) -> str:
        """Keep input fields distinct within the shared entity name format."""
        base = super()._entity_name()
        field, resource = self.field, self.resource
        if field is None:
            return base or str(self.capability_id)
        label = field.label(self.runtime.hass.config.language)
        if base and len(input_capabilities(resource)) > 1 and base != label:
            return f"{base} · {label}"
        return base or label

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return resource identifiers and descriptive state attributes."""
        attributes = {
            **super().extra_state_attributes,
            "worlty_capability_id": self.capability_id,
        }
        if self.field is not None and self.field.flags:
            attributes["worlty_supported_flags"] = list(self.field.flags)
        return attributes

    async def async_set_value(self, value: Any) -> None:
        """Send a write request for the current input field through the runtime."""
        await self.runtime.async_set_input_value(
            self.resource_id, self.capability_id, value
        )

    def _is_current_projection(self) -> bool:
        """Check the input field's existence and platform as well as its resource."""
        field = self.field
        return (
            super()._is_current_projection()
            and field is not None
            and field.platform == self._input_platform
        )


def setup_input_platform(
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    *,
    platform: str,
    factory: Callable[[WorltyRuntime, str, int], WorltyInputEntity],
) -> None:
    """Track field additions and removals even while the parent type stays 7."""
    runtime = entry.runtime_data

    def fields(resource_id: str) -> set[tuple[str, int]]:
        resource = runtime.model.entities.get(resource_id)
        if resource is None or not resource.visible or not resource.enabled:
            return set()
        return {
            (resource_id, capability_id)
            for capability_id, field in input_capabilities(resource).items()
            if field.platform == platform
        }

    known = set().union(
        *(
            fields(resource.entity_id)
            for resource in runtime.resources_for_platform("input")
        )
    )
    async_add_entities(
        [
            factory(runtime, resource_id, capability_id)
            for resource_id, capability_id in sorted(known)
        ]
    )

    def model_changed(change: WorltyModelChange) -> None:
        affected = {key for key in known if key[0] in change.all_ids}
        current = set().union(*(fields(resource_id) for resource_id in change.all_ids))
        new = current - known
        known.difference_update(affected)
        known.update(current)
        if new:
            async_add_entities(
                [
                    factory(runtime, resource_id, capability_id)
                    for resource_id, capability_id in sorted(new)
                ]
            )

    entry.async_on_unload(runtime.add_model_listener(model_changed))
