"""Preservation of existing Core Store values without activating them."""

from __future__ import annotations

import copy
import json
from typing import Any
from uuid import UUID


class CoreStateError(ValueError):
    """Corrupt storage that must not be replaced with another identity."""

    def __init__(self, code: str = "INVALID_STORED_CORE_STATE") -> None:
        """Store the state validation error code."""
        super().__init__(code)
        self.code = code


def restore_stored_state(value: object) -> dict[str, Any]:
    """Copy stored JSON without executing retired public contracts.

    Reject corrupt containers and identity fields without revalidating retired
    contracts. Preserve unknown fields and inactive Space, Automation, and
    allowlist data in the same Store.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CoreStateError()
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as err:
        raise CoreStateError() from err
    revision = value.get("catalog_revision_high_water", 0)
    if type(revision) is not int or revision < 0:
        raise CoreStateError()
    for key in ("allowlist", "space"):
        if key in value and value[key] is not None and not isinstance(value[key], dict):
            raise CoreStateError()
    space = value.get("space")
    if isinstance(space, dict) and "confirmed" in space:
        confirmed = space["confirmed"]
        if not isinstance(confirmed, list) or any(
            not isinstance(item, dict) for item in confirmed
        ):
            raise CoreStateError()
    for key in ("automation", "bindings", "homeassistant_resource_ids"):
        if key in value and (
            not isinstance(value[key], list)
            or any(not isinstance(item, dict) for item in value[key])
        ):
            raise CoreStateError()
    for key in ("owned_floor_ids", "owned_area_ids"):
        if key in value and (
            not isinstance(value[key], list)
            or any(not isinstance(item, str) or not item for item in value[key])
        ):
            raise CoreStateError()
    for item in value.get("bindings", []):
        source = item.get("source")
        if (
            not isinstance(source, dict)
            or any(
                not isinstance(source.get(key), str) or not source[key]
                for key in ("system", "instance_id", "native_key")
            )
            or not isinstance(source.get("pad_id", ""), str)
            or not isinstance(item.get("target_registry_id"), str)
            or not item["target_registry_id"]
        ):
            raise CoreStateError()
    for item in value.get("homeassistant_resource_ids", []):
        if (
            not isinstance(item.get("resource_kind"), str)
            or item["resource_kind"] not in {"device", "entity"}
            or not isinstance(item.get("registry_id"), str)
            or not item["registry_id"]
        ):
            raise CoreStateError()
        for key in ("ha_instance_id", "resource_id"):
            candidate = item.get(key)
            try:
                if not isinstance(candidate, str) or str(UUID(candidate)) != candidate:
                    raise CoreStateError()
            except (ValueError, AttributeError) as err:
                raise CoreStateError() from err
    return copy.deepcopy(value)
