"""HA display model for hub devices and state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .input import input_capabilities

ENTITY_PLATFORM_BY_TYPE = {
    1: "binary_sensor",
    2: "sensor",
    3: "light",
    4: "switch",
    5: "event",
    6: "fan",
    7: "input",
    8: "climate",
    9: "cover",
}


@dataclass(slots=True)
class WorltyResource:
    """Entity record with separate metadata and state sections."""

    entity_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    metadata_revision: int = 0
    state_revision: int = 0
    state_timestamp_ms: int = 0

    @property
    def revision(self) -> int:
        """Return the larger revision without merging metadata and state streams."""
        return max(self.metadata_revision, self.state_revision)

    @property
    def device_id(self) -> str:
        """Return the parent device ID from metadata."""
        return str(self.metadata.get("device_id", ""))

    @property
    def entity_type(self) -> int:
        """Return the entity type ID from metadata."""
        value = self.metadata.get("tp")
        return value if type(value) is int else 0

    @property
    def entity_class(self) -> int:
        """Return the entity class ID from metadata."""
        value = self.metadata.get("cls")
        return value if type(value) is int else 0

    @property
    def capabilities(self) -> tuple[int, ...]:
        """Return integer capability IDs declared in metadata."""
        value = self.metadata.get("cp", ())
        return (
            tuple(item for item in value if type(item) is int)
            if isinstance(value, list)
            else ()
        )

    @property
    def overrides(self) -> dict[str, dict[str, Any]]:
        """Return capability descriptors specified for this resource."""
        value = self.metadata.get("co")
        return value if isinstance(value, dict) else {}

    @property
    def platform(self) -> str | None:
        """Return the HA platform for the resource type."""
        return ENTITY_PLATFORM_BY_TYPE.get(self.entity_type)

    @property
    def enabled(self) -> bool:
        """Check whether the resource is enabled."""
        return bool(self.metadata.get("enabled", True))

    @property
    def visible(self) -> bool:
        """Check whether the resource is marked visible."""
        return self.metadata.get("visibility", "visible") == "visible"


def provider_entity_unique_id(
    *,
    pad_id: str,
    resource_id: str,
    capability_id: int | None = None,
) -> str:
    """Build an HA registry ID from the immutable hub resource UUID."""
    suffix = "" if capability_id is None else f":cp:{capability_id}"
    return f"{pad_id}:{resource_id}{suffix}"


def projected_entity_ids(pad_id: str, resource: WorltyResource) -> dict[str, str]:
    """Find HA entity representations without changing transport IDs."""
    if not resource.visible or not resource.enabled:
        return {}
    if resource.entity_type == 7:
        return {
            provider_entity_unique_id(
                pad_id=pad_id,
                resource_id=resource.entity_id,
                capability_id=capability_id,
            ): field.platform
            for capability_id, field in input_capabilities(resource).items()
        }
    if resource.platform is None:
        return {}
    return {
        provider_entity_unique_id(
            pad_id=pad_id, resource_id=resource.entity_id
        ): resource.platform
    }


@dataclass(frozen=True, slots=True)
class WorltyModelChange:
    """IDs affected by one Device API update."""

    added: frozenset[str] = frozenset()
    updated: frozenset[str] = frozenset()
    removed: frozenset[str] = frozenset()
    retyped: frozenset[str] = frozenset()
    updated_devices: frozenset[str] = frozenset()
    removed_devices: frozenset[str] = frozenset()
    activated: frozenset[str] = frozenset()
    deactivated: frozenset[str] = frozenset()

    @property
    def all_ids(self) -> frozenset[str]:
        """Collect all entity IDs affected by this change."""
        return (
            self.added
            | self.updated
            | self.removed
            | self.retyped
            | self.activated
            | self.deactivated
        )

    @property
    def all_device_ids(self) -> frozenset[str]:
        """Collect all device IDs affected by this change."""
        return self.updated_devices | self.removed_devices


class WorltyModel:
    """Store complete Device data for HA display.

    DeviceSyncReceiver owns reception and validation.
    """

    def __init__(self) -> None:
        """Initialize entity, device, and availability collections."""
        self.entities: dict[str, WorltyResource] = {}
        self.devices: dict[str, dict[str, Any]] = {}
        self.device_availability: dict[str, str] = {}

    def resource_available(self, resource: WorltyResource) -> bool:
        """Check availability without treating unknown status as explicit failure."""
        if not resource.enabled or not resource.visible:
            return False
        if not resource.device_id:
            return resource.state.get("availability") not in {"offline", "unavailable"}
        device = self.devices.get(resource.device_id)
        if device is None or not device.get("enabled", True):
            return False
        availability = self.device_availability.get(resource.device_id, "unknown")
        return availability not in {"offline", "unavailable"}

    def resources_for_platform(self, platform: str) -> list[WorltyResource]:
        """Return visible resources for the requested HA platform."""
        return [
            resource
            for resource in self.entities.values()
            if resource.platform == platform and resource.visible and resource.enabled
        ]
