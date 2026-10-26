"""Shared behavior for Worlty Core entities."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .model import (
    WorltyModelChange,
    WorltyResource,
    provider_entity_unique_id,
)
from .naming import device_display_name
from .runtime import WorltyRuntime
from .wdp import LOCALIZATION_REGISTRY

_ENTITY_CLASS_LABELS = {
    (int(item["entityType"]), int(item["entityClass"])): item["labels"]
    for item in LOCALIZATION_REGISTRY.get("entityClasses", ())
}
_ENTITY_TYPE_LABELS = {
    int(item["entityType"]): item["labels"]
    for item in LOCALIZATION_REGISTRY.get("entityTypes", ())
}
_LOCALIZED_KEYS = {
    str(item["key"]): item["labels"]
    for item in LOCALIZATION_REGISTRY.get("entries", ())
}


class WorltyEntity(Entity):
    """Base class for one Worlty hub Device API entity."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: WorltyRuntime, resource_id: str) -> None:
        """Retain the resource ID and initial platform and build the HA unique ID."""
        self.runtime = runtime
        self.resource_id = resource_id
        resource = runtime.model.entities.get(resource_id)
        self._platform = resource.platform if resource is not None else None
        self._attr_unique_id = provider_entity_unique_id(
            pad_id=str(runtime.entry.data["device_id"]),
            resource_id=resource_id,
        )
        self._remove_listener: Callable[[], None] | None = None

    @property
    def resource(self) -> WorltyResource | None:
        """Return this entity's resource from the current model."""
        return self.runtime.model.entities.get(self.resource_id)

    @property
    def available(self) -> bool:
        """Determine availability from the connection and resource state."""
        resource = self.resource
        return (
            self.runtime.connected
            and resource is not None
            and self.runtime.model.resource_available(resource)
        )

    @property
    def name(self) -> str:
        """Return the entity name; HA supplies device context where needed."""
        return self._entity_name()

    def _entity_name(self) -> str:
        """Resolve the entity alias or translated name with its literal suffix."""
        resource = self.resource
        if resource is None:
            return self.resource_id
        name = resource.metadata.get("name")
        if isinstance(name, str) and name:
            return name
        locale = self.runtime.hass.config.language
        label = None
        for labels in (
            _LOCALIZED_KEYS.get(str(resource.metadata.get("name_key", "")), {}),
            _ENTITY_CLASS_LABELS.get((resource.entity_type, resource.entity_class), {}),
            _ENTITY_TYPE_LABELS.get(resource.entity_type, {}),
        ):
            label = labels.get(locale) or labels.get("en")
            if label:
                break
        name = str(label or self.resource_id)
        suffix = resource.metadata.get("name_suffix")
        if isinstance(suffix, str) and suffix:
            name = f"{name}{suffix}"
        return name

    def _device_name(self, device_id: str) -> str:
        """Resolve the device alias or translated name with its literal suffix."""
        return device_display_name(
            self.runtime.model.devices.get(device_id, {}),
            device_id,
            self.runtime.hass.config.language,
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return HA device identifiers and the parent hub relationship."""
        resource = self.resource
        device_id = resource.device_id if resource is not None else self.resource_id
        metadata = self.runtime.model.devices.get(device_id, {})
        source = (
            metadata.get("source") if isinstance(metadata.get("source"), dict) else {}
        )
        pad_id = str(self.runtime.entry.data["device_id"])
        identifiers = {(DOMAIN, f"{pad_id}:{device_id}")}
        name = self._device_name(device_id)
        device_registry = dr.async_get(self.runtime.hass)
        parent = device_registry.async_get_device_by_identifier(
            (DOMAIN, pad_id), self.runtime.entry.entry_id
        )
        device_info = DeviceInfo(
            identifiers=identifiers,
            name=name,
            manufacturer=str(source.get("manufacturer") or "Worlty"),
            model=str(source.get("model") or "월티 허브"),
        )
        if parent is not None:
            device_info["via_device_id"] = parent.id
        return device_info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return resource identifiers and descriptive state attributes."""
        resource = self.resource
        if resource is None:
            return {"worlty_resource_id": self.resource_id}
        return {
            "worlty_resource_id": self.resource_id,
            "worlty_revision": resource.revision,
            "worlty_capabilities": list(resource.capabilities),
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to model changes and clean up resources removed during setup."""
        await super().async_added_to_hass()
        self._remove_listener = self.runtime.add_model_listener(
            self._handle_model_change
        )
        # Resources may be removed, hidden, or retyped while platform registration
        # waits.
        if not self._is_current_projection():
            self.hass.async_create_task(
                self._async_remove_completely(), f"{DOMAIN}-remove-{self.resource_id}"
            )

    def _is_current_projection(self) -> bool:
        """Check that the resource remains visible on this entity's platform."""
        resource = self.resource
        return (
            resource is not None
            and resource.visible
            and resource.enabled
            and resource.platform == self._platform
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from model changes before removing the entity."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        await super().async_will_remove_from_hass()

    def _handle_model_change(self, change: WorltyModelChange) -> None:
        if self.resource_id not in change.all_ids:
            return
        if not self._is_current_projection():
            self.hass.async_create_task(
                self._async_remove_completely(),
                f"{DOMAIN}-remove-{self.resource_id}",
            )
            return
        self.async_write_ha_state()

    async def _async_remove_completely(self) -> None:
        """Remove an obsolete entity from both the platform and registry."""
        await asyncio.sleep(0)
        # Keep a resource restored before its scheduled removal runs.
        if self._is_current_projection():
            return
        if self.entity_id and er.async_get(self.hass).async_get(self.entity_id):
            er.async_get(self.hass).async_remove(self.entity_id)
        await self.async_remove(force_remove=True)


def setup_entry_platform(
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    *,
    platform: str,
    factory: Callable[[WorltyRuntime, str], WorltyEntity],
) -> None:
    """Register current resources and subscribe to later catalog additions."""
    runtime: WorltyRuntime = entry.runtime_data
    known = {
        resource.entity_id for resource in runtime.resources_for_platform(platform)
    }
    async_add_entities(factory(runtime, entity_id) for entity_id in sorted(known))

    def model_changed(change: WorltyModelChange) -> None:
        for entity_id in change.removed | change.retyped | change.deactivated:
            resource = runtime.model.entities.get(entity_id)
            if entity_id in known and (
                entity_id in change.deactivated
                or resource is None
                or resource.platform != platform
            ):
                known.discard(entity_id)
        new_ids = [
            entity_id
            for entity_id in change.added | change.retyped | change.activated
            if entity_id not in known
            and (resource := runtime.model.entities.get(entity_id)) is not None
            and resource.platform == platform
            and resource.visible
            and resource.enabled
        ]
        if new_ids:
            known.update(new_ids)
            async_add_entities(
                factory(runtime, entity_id) for entity_id in sorted(new_ids)
            )

    entry.async_on_unload(runtime.add_model_listener(model_changed))
