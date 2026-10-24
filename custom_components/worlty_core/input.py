"""Hub inputs driven by descriptors rather than names or provider policy."""

from __future__ import annotations

import math
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .wdp.generated_external_platform import (
    CAPABILITY_BY_ID,
    LOCALIZATION_REGISTRY,
    MEASUREMENT_UNITS,
)

_CAPABILITY_LABELS = {
    item["capabilityId"]: item["labels"]
    for item in LOCALIZATION_REGISTRY.get("capabilities", ())
}
_ENUM_LABELS = {
    item["enumDomainId"]: {value["value"]: value["labels"] for value in item["values"]}
    for item in LOCALIZATION_REGISTRY.get("enumDomains", ())
}
_EPOCH_DATE = date(1970, 1, 1)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_UNITS = {item["id"]: item["wire"] for item in MEASUREMENT_UNITS}


def _number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True, slots=True)
class InputCapability:
    """View of the current descriptor without a separate configuration store."""

    capability_id: int
    descriptor: dict[str, Any]

    @property
    def defaults(self) -> dict[str, Any]:
        """Return the capability's default WDP descriptor."""
        return CAPABILITY_BY_ID.get(self.capability_id, {})

    @property
    def value_type(self) -> int:
        """Return the value type declared by the descriptor."""
        return self.descriptor.get("vt", 0)

    @property
    def readable(self) -> bool:
        """Check whether the descriptor permits reads."""
        return bool(self.descriptor.get("ac", 0) & 1)

    @property
    def writable(self) -> bool:
        """Check whether the descriptor permits writes."""
        return bool(self.descriptor.get("ac", 0) & 2)

    @property
    def platform(self) -> str | None:
        """Select an HA platform from the value type and access permissions."""
        if self.writable:
            return {
                1: "switch",
                2: "number",
                3: "select",
                4: "time",
                5: "date",
                6: "datetime",
                7: "number",
                9: "text",
            }.get(self.value_type)
        if self.readable:
            return (
                "binary_sensor"
                if self.value_type == 1
                else ("sensor" if self.value_type in range(2, 10) else None)
            )
        return None

    def label(self, language: str) -> str:
        """Return the capability label in the requested language."""
        labels = _CAPABILITY_LABELS.get(self.capability_id, {})
        return (
            labels.get(language)
            or labels.get("en")
            or str(self.defaults.get("key", self.capability_id))
        )

    def _definition(self, key: str, default_key: str, fallback: Any = None) -> Any:
        return self.descriptor.get(key, self.defaults.get(default_key, fallback))

    @property
    def flags(self) -> tuple[int, ...]:
        """Return flags declared by the descriptor or default definition."""
        values = self.descriptor.get("fv")
        if values is None:
            values = [item["value"] for item in self.defaults.get("values", ())]
        return tuple(value for value in values if type(value) is int and value > 0)

    @property
    def minimum(self) -> float:
        """Return the minimum from the descriptor or default definition."""
        return self._definition(
            "mn",
            "minimum",
            0 if self.value_type in (4, 5, 6, 7) else -sys.float_info.max,
        )

    @property
    def flag_mask(self) -> int:
        """Combine the declared flags into an allowed bit mask."""
        mask = 0
        for flag in self.flags:
            mask |= flag
        return mask

    @property
    def maximum(self) -> float:
        """Return the maximum allowed by the descriptor and flag limits."""
        if self.value_type == 7 and self.has_flag_limits:
            return min(
                self._definition("mx", "maximum", self.flag_mask), self.flag_mask
            )
        fallback = {4: 86399, 5: 2932896, 6: 8640000000000000, 7: 4294967295}.get(
            self.value_type, sys.float_info.max
        )
        return self._definition("mx", "maximum", fallback)

    @property
    def step(self) -> float:
        """Return a valid adjustment step."""
        step = self._definition("st", "step")
        if _number(step) and step > 0:
            return step
        precision = self._definition("pr", "precision", 0)
        return 10**-precision if type(precision) is int and 0 <= precision <= 12 else 1

    @property
    def max_length(self) -> int:
        """Return the maximum length capped by the capability default."""
        return min(
            self._definition("ml", "maxLength", 256),
            self.defaults.get("maxLength", 256),
        )

    @property
    def unit(self) -> str | None:
        """Return the unit from the descriptor or default definition."""
        return (
            self._definition("un", "unit", _UNITS.get(self.defaults.get("unitId")))
            or None
        )

    @property
    def has_flag_limits(self) -> bool:
        """Check whether an allowed flag list is defined."""
        return "fv" in self.descriptor or "values" in self.defaults

    def options(self, language: str) -> dict[str, int]:
        """Return selectable values and their labels."""
        values = self.descriptor.get("ev")
        defaults = {
            item["value"]: item["key"] for item in self.defaults.get("values", ())
        }
        if values is None:
            values = list(defaults)
        labels = _ENUM_LABELS.get(self.descriptor.get("ed"), {})
        labeled = []
        for value in values:
            localized = labels.get(value, {})
            label = (
                localized.get(language)
                or localized.get("en")
                or defaults.get(value)
                or str(value)
            )
            labeled.append((label, value))
        counts = Counter(label for label, _ in labeled)
        return {
            f"{label} ({value})" if counts[label] > 1 else label: value
            for label, value in labeled
        }

    def decode(self, value: Any, language: str) -> Any:
        """Convert a readable raw value to its HA input type."""
        if not self.readable:
            return None
        vt = self.value_type
        if vt == 1:
            return (
                bool(value) if type(value) in (int, bool) and value in (0, 1) else None
            )
        if vt == 9:
            return value if isinstance(value, str) else None
        if not _number(value):
            return None
        if vt == 2:
            return value
        if type(value) is not int:
            return None
        if vt == 3:
            return next(
                (
                    label
                    for label, wire in self.options(language).items()
                    if wire == value
                ),
                None,
            )
        try:
            if vt == 4:
                return (
                    time(value // 3600, value % 3600 // 60, value % 60)
                    if 0 <= value < 86400
                    else None
                )
            if vt == 5:
                return _EPOCH_DATE + timedelta(days=value) if value >= 0 else None
            if vt == 6:
                return _EPOCH + timedelta(milliseconds=value) if value >= 0 else None
            if vt == 7:
                return (
                    value
                    if value >= 0
                    and (not self.has_flag_limits or value & ~self.flag_mask == 0)
                    else None
                )
            if vt == 8:
                return value
        except (OverflowError, ValueError):
            return None
        return None

    def encode(self, value: Any, language: str) -> int | float | str:
        """Validate an HA native value against the current writable descriptor."""
        if not self.writable or self.platform is None:
            raise ValueError("Capability is not writable")
        vt = self.value_type
        if vt == 1:
            if type(value) is not bool:
                raise ValueError("Switch value must be boolean")
            return int(value)
        if vt == 3:
            options = self.options(language)
            if not isinstance(value, str) or value not in options:
                raise ValueError("Unsupported selection")
            return options[value]
        if vt == 9:
            if not isinstance(value, str) or len(value) > self.max_length:
                raise ValueError("Invalid text length")
            value.encode("utf-8")
            return value
        if vt == 4:
            if (
                not isinstance(value, time)
                or value.tzinfo is not None
                or value.microsecond
            ):
                raise ValueError("Time must be local whole seconds")
            value = value.hour * 3600 + value.minute * 60 + value.second
        elif vt == 5:
            if type(value) is not date:
                raise ValueError("Date required")
            value = (value - _EPOCH_DATE).days
        elif vt == 6:
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError("Timezone-aware datetime required")
            if value.microsecond % 1000:
                raise ValueError("Whole milliseconds required")
            elapsed = value.astimezone(UTC) - _EPOCH
            value = (
                elapsed.days * 86400000
                + elapsed.seconds * 1000
                + elapsed.microseconds // 1000
            )
        if not _number(value) or not self.minimum <= value <= self.maximum:
            raise ValueError("Value is outside the declared range")
        if vt in (4, 5, 6, 7) and int(value) != value:
            raise ValueError("Integer value required")
        if vt == 7 and self.has_flag_limits and int(value) & ~self.flag_mask:
            raise ValueError("Unsupported flag bits")
        step = self._definition("st", "step")
        if _number(step) and step > 0:
            origin = self._definition("mn", "minimum", 0)
            try:
                if (Decimal(str(value)) - Decimal(str(origin))) % Decimal(str(step)):
                    raise ValueError("Value does not match the declared step")
            except InvalidOperation as err:
                raise ValueError("Invalid numeric precision") from err
        return int(value) if vt in (4, 5, 6, 7) else value


def input_capabilities(resource: Any) -> dict[int, InputCapability]:
    """Expose supported descriptor types and retain unknown raw data."""
    if resource.entity_type != 7:
        return {}
    result = {}
    for capability_id in resource.capabilities:
        descriptor = resource.overrides.get(str(capability_id), {})
        if (
            type(descriptor.get("vt")) is not int
            or type(descriptor.get("ac")) is not int
        ):
            continue
        if descriptor["ac"] not in (1, 2, 3):
            continue
        field = InputCapability(capability_id, descriptor)
        if field.platform is not None:
            result[capability_id] = field
    return result
