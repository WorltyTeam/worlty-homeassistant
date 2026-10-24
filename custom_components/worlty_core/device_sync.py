"""Schema6 Device receiver with collection revisions and complete transfers."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import replace
from typing import Any

from .model import WorltyModel, WorltyModelChange, WorltyResource
from .wdp.generated_external_platform import CAPABILITY_BY_ID

_MAX_RECORDS = 10240
_MAX_BUFFER_BYTES = 16 * 1024 * 1024

_COLLECTIONS = {
    "se": ("st", "ent"),
    "sd": ("st", "dev"),
    "me": ("md", "ent"),
    "md": ("md", "dev"),
    "mv": ("md", "vw"),
}
_STATE_KEYS = {
    "1": "v",
    "2": "br",
    "3": "tt",
    "4": "ct",
    "5": "m",
    "6": "f",
    "12": "a",
    "14": "vm",
    "15": "speed",
    "16": "pos",
    "30": "swing",
    "31": "br_level",
}


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _retained_size(value):
    """Measure raw rows and replay history using the same UTF-8 byte budget."""
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


_CONTROL_KEYS = {
    value: key for key, value in _STATE_KEYS.items() if key not in {"4", "12"}
}


class DeviceSyncReceiver:
    """Assemble transfers with a fresh baseline on reconnect.

    Support full and delta transfers. Retain unknown application data in raw
    records. Enforce transfer integrity and local buffer limits; the display
    layer consumes only supported values.
    """

    def __init__(self, model: WorltyModel) -> None:
        """Initialize the model, transfer assembly, and revision tracking state."""
        self.model = model
        self.epoch: str | None = None
        self.rows: dict[str, dict[str, dict[str, Any]]] = {
            key: {} for key in _COLLECTIONS
        }
        self.fences: dict[str, dict[str, int]] = {key: {} for key in _COLLECTIONS}
        self.cap_fences: dict[str, dict[str, int]] = {}
        self._transfer: dict[str, Any] | None = None
        self._raw_bytes = 0
        self._history_bytes = 0

    def abort(self) -> None:
        """Discard the pending transfer while retaining the last committed state."""
        self._transfer = None

    def accept(self, page: dict[str, Any]) -> WorltyModelChange | None:
        """Collect raw rows and update the model only after a complete transfer."""
        try:
            self._page_shape(page)
            identity = tuple(
                copy.deepcopy(page[key])
                for key in ("ep", "sid", "sm", "tc", "sc", "rev")
            )
            if self._transfer is None:
                if page["sm"] != 1 and page["ep"] != self.epoch:
                    raise ValueError("Device epoch requires a full baseline")
                self._transfer = {
                    "identity": identity,
                    "rows": {key: {} for key in _COLLECTIONS},
                    "removed": {key: {} for key in _COLLECTIONS},
                    "next": set(),
                    "bytes": 0,
                }
            transfer = self._transfer
            if identity != transfer["identity"]:
                raise ValueError("Device transfer identity changed before completion")
            transfer["bytes"] += len(
                json.dumps(page, ensure_ascii=False, allow_nan=False).encode("utf-8")
            )
            # This consumer memory budget is independent of firmware schema versions.
            if transfer["bytes"] > _MAX_BUFFER_BYTES:
                raise ValueError("Device transfer exceeds local byte capacity")
            if page["sm"] == 2 and page.get("bep") != page["ep"]:
                raise ValueError("Delta base epoch mismatch")
            if page["tc"] != sum(page["sc"][key] for key in _COLLECTIONS) or page[
                "cnt"
            ] != sum(page["pc"][key] for key in _COLLECTIONS):
                raise ValueError("Device transfer count mismatch")
            for key, (section, collection) in _COLLECTIONS.items():
                upserts = page[section][collection]
                removed = page.get("rm", {}).get(key, [])
                if not isinstance(upserts, list) or not isinstance(removed, list):
                    raise ValueError("Device collection must be an array")
                if page["pc"][key] != len(upserts) + len(removed):
                    raise ValueError("Device page collection count mismatch")
                seen = set(transfer["rows"][key]) | set(transfer["removed"][key])
                for rows, target in ((upserts, "rows"), (removed, "removed")):
                    for row in rows:
                        self._row_shape(row)
                        if row["id"] in seen:
                            raise ValueError("Device transfer repeated a collection ID")
                        seen.add(row["id"])
                        transfer[target][key][row["id"]] = copy.deepcopy(row)
                if len(seen) > page["sc"][key]:
                    raise ValueError("Device transfer exceeds declared membership")
            if not page["fin"]:
                cursor = page.get("nc")
                if (
                    type(cursor) not in (str, int)
                    or not cursor
                    or cursor in transfer["next"]
                ):
                    raise ValueError("Device cursor is missing or repeated")
                transfer["next"].add(cursor)
                return None
            if any(
                len(transfer["rows"][key]) + len(transfer["removed"][key])
                != page["sc"][key]
                for key in _COLLECTIONS
            ):
                raise ValueError("Incomplete Device membership")
            change = self._apply(transfer["rows"], transfer["removed"], page=page)
            self.abort()
            return change
        except (KeyError, TypeError, ValueError, OverflowError) as err:
            self.abort()
            raise ValueError("Invalid or incomplete Device transfer") from err

    @staticmethod
    def _row_shape(row: Any) -> None:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not row["id"]
            or len(row["id"]) > 128
            or type(row.get("rv")) is not int
            or row["rv"] < 0
        ):
            raise ValueError("Device record identity/revision is invalid")

    @staticmethod
    def _page_shape(page: Any) -> None:
        if not isinstance(page, dict) or type(page.get("fin")) is not bool:
            raise ValueError("Device transfer completion is invalid")
        if type(page.get("sm")) is not int or page["sm"] not in (1, 2):
            raise ValueError("Device transfer operation is not understood")
        for key in ("ep", "sid"):
            if (
                not isinstance(page.get(key), str)
                or not page[key]
                or len(page[key]) > 128
            ):
                raise ValueError("Device transfer identity is invalid")
        for key in ("tc", "cnt"):
            if type(page.get(key)) is not int or not 0 <= page[key] <= _MAX_RECORDS:
                raise ValueError("Device transfer exceeds local record capacity")
        for key in ("sc", "pc", "rev"):
            values = page.get(key)
            if not isinstance(values, dict) or any(
                type(values.get(name)) is not int or values[name] < 0
                for name in _COLLECTIONS
            ):
                raise ValueError("Device transfer collection counters are invalid")
        if not isinstance(page.get("rm", {}), dict):
            raise ValueError("Device removal collection is invalid")

    @staticmethod
    def _merged_state(previous, row, fences, *, full):
        revision = row["rv"]
        result = dict(previous or {"id": row["id"], "rv": 0, "ts": 0})
        result.update(
            {
                key: value
                for key, value in row.items()
                if key not in {"st", "tx", "rv", "ts"}
            }
        )
        fences = dict(fences)
        for section in ("st", "tx"):
            current = (
                dict(result.get(section, {}))
                if isinstance(result.get(section, {}), dict)
                else {}
            )
            incoming = row.get(section, {})
            # Preserve future-format values as raw data rather than merging them as
            # mappings.
            if not isinstance(incoming, dict):
                # An opaque older section must not erase newer capability values.
                # Retain those values with their original capability revision fences.
                newer = {
                    cap: value
                    for cap, value in current.items()
                    if fences.get(cap, 0) > revision
                }
                result[section] = newer if newer else incoming
                continue
            if full:
                for cap in set(current) - set(incoming):
                    if fences.get(cap, 0) <= revision:
                        current.pop(cap, None)
                        fences[cap] = revision
            for cap, value in incoming.items():
                if revision >= fences.get(cap, 0):
                    current[cap] = value
                    fences[cap] = revision
            result[section] = current
        if full:
            result["rv"] = revision
        old_ts, new_ts = result.get("ts", 0), row.get("ts", 0)
        result["ts"] = max(
            old_ts if type(old_ts) is int else 0, new_ts if type(new_ts) is int else 0
        )
        return result, fences

    def _apply(self, incoming, removed, *, page=None) -> WorltyModelChange:
        """Prepare changed records and decode all display data before committing."""
        reset = page is not None and page["ep"] != self.epoch
        old_rows = {key: {} for key in _COLLECTIONS} if reset else self.rows
        old_fences = {key: {} for key in _COLLECTIONS} if reset else self.fences
        old_caps = {} if reset else self.cap_fences
        updates = {key: {} for key in _COLLECTIONS}
        removals = {key: set() for key in _COLLECTIONS}
        fence_updates = {key: {} for key in _COLLECTIONS}
        cap_updates = {}
        changed = {key: set() for key in _COLLECTIONS}
        deleted = {key: set() for key in _COLLECTIONS}
        for key in _COLLECTIONS:
            absences = dict(removed[key])
            if page is not None and page["sm"] == 1:
                for ident, row in old_rows[key].items():
                    if (
                        ident not in incoming[key]
                        and ident not in absences
                        and row["rv"] <= page["rev"][key]
                    ):
                        absences[ident] = {"id": ident, "rv": page["rev"][key]}
            for ident, row in absences.items():
                revision = row["rv"]
                if revision <= old_fences[key].get(ident, -1):
                    continue
                fence_updates[key][ident] = revision
                deleted[key].add(ident)
                caps = old_caps.get(ident, {}) if key == "se" else {}
                retained = {cap: rv for cap, rv in caps.items() if rv > revision}
                if retained:
                    prior = old_rows[key][ident]
                    value = dict(prior)
                    for section in ("st", "tx"):
                        fields = prior.get(section, {})
                        value[section] = (
                            {
                                cap: item
                                for cap, item in fields.items()
                                if cap in retained
                            }
                            if isinstance(fields, dict)
                            else {}
                        )
                    updates[key][ident] = value
                    cap_updates[ident] = retained
                else:
                    removals[key].add(ident)
                    if key == "se":
                        cap_updates[ident] = None
            for ident, row in incoming[key].items():
                revision = row["rv"]
                if revision <= old_fences[key].get(ident, -1):
                    continue
                if key == "se":
                    value, caps = self._merged_state(
                        old_rows[key].get(ident),
                        row,
                        old_caps.get(ident, {}),
                        full=page is not None,
                    )
                    updates[key][ident], cap_updates[ident] = value, caps
                else:
                    updates[key][ident] = row
                if page is not None:
                    fence_updates[key][ident] = revision
                changed[key].add(ident)

        def read(key, ident):
            if ident in removals[key]:
                return None
            return updates[key].get(ident, old_rows[key].get(ident))

        # Count live rows and deleted IDs together without counting an ID's row and
        # fence twice.
        record_count = sum(
            len(
                (set(old_rows[key]) - removals[key])
                | set(updates[key])
                | set(old_fences[key])
                | set(fence_updates[key])
            )
            for key in _COLLECTIONS
        )
        raw_bytes = 0 if reset else self._raw_bytes
        for key in _COLLECTIONS:
            for ident in set(updates[key]) | removals[key]:
                if ident in old_rows[key]:
                    raw_bytes -= _retained_size(old_rows[key][ident])
                if ident in updates[key]:
                    raw_bytes += _retained_size(updates[key][ident])
        history_bytes = 0 if reset else self._history_bytes
        for key in _COLLECTIONS:
            for ident, revision in fence_updates[key].items():
                if ident in old_fences[key]:
                    history_bytes -= _retained_size((ident, old_fences[key][ident]))
                history_bytes += _retained_size((ident, revision))
        for ident, caps in cap_updates.items():
            if ident in old_caps:
                history_bytes -= _retained_size((ident, old_caps[ident]))
            if caps is not None:
                history_bytes += _retained_size((ident, caps))
        if record_count > _MAX_RECORDS or raw_bytes + history_bytes > _MAX_BUFFER_BYTES:
            raise ValueError("Device records exceed local memory capacity")

        prepared, change = self._prepare_projection(
            read, changed, deleted, reset, cap_updates, old_caps
        )
        # Do not invoke callbacks, await, or transform data between these commit steps.
        if reset:
            self.rows, self.fences, self.cap_fences = old_rows, old_fences, {}
        for key in _COLLECTIONS:
            for ident in removals[key]:
                self.rows[key].pop(ident, None)
            self.rows[key].update(updates[key])
            self.fences[key].update(fence_updates[key])
        for ident, caps in cap_updates.items():
            if caps is None:
                self.cap_fences.pop(ident, None)
            else:
                self.cap_fences[ident] = caps
        self._raw_bytes = raw_bytes
        self._history_bytes = history_bytes
        if page is not None:
            self.epoch = page["ep"]
        for name, (upserts, deleted_ids) in prepared.items():
            target = {} if reset else getattr(self.model, name)
            for ident in deleted_ids:
                target.pop(ident, None)
            target.update(upserts)
            if reset:
                setattr(self.model, name, target)
        return change

    def control_result(self, data: dict[str, Any]) -> WorltyModelChange:
        """Validate same-epoch control results and update known entity state."""
        if data.get("ep") != self.epoch:
            raise ValueError("Control result epoch changed")
        incoming = {key: {} for key in _COLLECTIONS}
        values = data.get("st", {}).get("ent")
        if not isinstance(values, list):
            raise ValueError("Control result state is invalid")
        for row in values:
            self._row_shape(row)
            if row["id"] in incoming["se"]:
                raise ValueError("Control result repeats an Entity")
            if row["id"] in self.rows["me"]:
                incoming["se"][row["id"]] = copy.deepcopy(row)
        return self._apply(incoming, {key: {} for key in _COLLECTIONS})

    @staticmethod
    def _metadata(row):
        item = {
            "id": row["id"],
            "rv": row["rv"],
            "visibility": "visible" if row.get("vis") == 1 else "hidden",
        }
        for wire, semantic in (
            ("nm", "name"),
            ("nk", "name_key"),
            ("ns", "name_suffix"),
        ):
            if isinstance(row.get(wire), str):
                item[semantic] = row[wire]
        item["enabled"] = row.get("en") is True
        return item

    def _prepare_projection(self, read, changed, deleted, reset, cap_updates, old_caps):
        names = ("entities", "devices", "device_availability")
        prepared = {name: ({}, set()) for name in names}
        old_entities = self.model.entities
        old_projection = {
            ident: (r.platform, r.visible and r.enabled)
            for ident, r in old_entities.items()
        }
        old_devices = set(self.model.devices)
        for key, name in (("me", "entities"), ("md", "devices")):
            prepared[name][1].update(deleted[key])
            for ident in changed[key]:
                row = read(key, ident)
                metadata = self._metadata(row)
                if key == "me":
                    resource = (
                        replace(old_entities[ident])
                        if not reset and ident in old_entities
                        else WorltyResource(ident)
                    )
                    caps = row.get("cp") if isinstance(row.get("cp"), dict) else {}
                    resource.metadata = {
                        **metadata,
                        "device_id": row.get("did", ""),
                        "tp": row.get("tp"),
                        "cls": row.get("cls"),
                        "cp": [
                            int(cap)
                            for cap in caps
                            if isinstance(cap, str)
                            and cap.isascii()
                            and cap.isdigit()
                            and len(cap) < 10
                        ],
                        "co": {
                            cap: self._descriptor(value)
                            for cap, value in caps.items()
                            if isinstance(value, dict)
                        },
                        "statistics": row.get("stat")
                        if isinstance(row.get("stat"), dict)
                        else {},
                    }
                    resource.metadata_revision = row["rv"]
                    prepared[name][0][ident] = resource
                elif key == "md":
                    source = row.get("src") if isinstance(row.get("src"), dict) else {}
                    prepared[name][0][ident] = {
                        **metadata,
                        "members": row.get("mb")
                        if isinstance(row.get("mb"), list)
                        else [],
                        "source": {
                            semantic: source.get(wire)
                            for semantic, wire in (
                                ("provider_id", "pv"),
                                ("provider", "pv"),
                                ("instance_id", "iid"),
                                ("native_key", "nid"),
                                ("manufacturer", "mf"),
                                ("model", "mdl"),
                            )
                        },
                    }
        affected = changed["me"] | changed["se"] | deleted["se"]
        for ident in affected:
            if ident in deleted["me"]:
                continue
            resource = prepared["entities"][0].get(ident)
            if resource is None and not reset and ident in old_entities:
                resource = replace(old_entities[ident])
            if resource is None:
                continue
            row = read("se", ident)
            resource.state = {} if row is None else self._semantic_state(resource, row)
            caps = cap_updates.get(ident, old_caps.get(ident, {})) or {}
            resource.state_revision = (
                0 if row is None else max([row["rv"], *caps.values()])
            )
            resource.state_timestamp_ms = 0 if row is None else row.get("ts", 0)
            prepared["entities"][0][ident] = resource
        for ident in changed["sd"] | deleted["sd"]:
            row = read("sd", ident)
            if row is None:
                prepared["device_availability"][1].add(ident)
            else:
                av = row.get("av")
                prepared["device_availability"][0][ident] = (
                    {1: "unknown", 2: "online", 3: "degraded", 4: "unavailable"}.get(
                        av, "unknown"
                    )
                    if type(av) is int
                    else "unknown"
                )
        new_ids = (set() if reset else set(old_entities)) - prepared["entities"][
            1
        ] | set(prepared["entities"][0])
        new_projection = {}
        changed_devices = changed["sd"] | deleted["sd"] | changed["md"] | deleted["md"]
        affected = set(affected)
        for ident in new_ids:
            resource = prepared["entities"][0].get(ident) or old_entities[ident]
            new_projection[ident] = (
                resource.platform,
                resource.visible and resource.enabled,
            )
            if resource.device_id in changed_devices:
                affected.add(ident)
        old_ids, shared = set(old_projection), set(old_projection) & new_ids

        def projected(value):
            return value[0] is not None and value[1]

        added = {
            ident for ident in new_ids - old_ids if projected(new_projection[ident])
        }
        activated = {
            ident
            for ident in shared
            if not projected(old_projection[ident]) and projected(new_projection[ident])
        }
        deactivated = {
            ident
            for ident in shared
            if projected(old_projection[ident]) and not projected(new_projection[ident])
        }
        retyped = {
            ident
            for ident in shared
            if projected(old_projection[ident])
            and projected(new_projection[ident])
            and old_projection[ident][0] != new_projection[ident][0]
        }
        if reset:
            affected.update(shared)
        new_devices = (set() if reset else old_devices) - prepared["devices"][1] | set(
            prepared["devices"][0]
        )
        return prepared, WorltyModelChange(
            added=frozenset(added),
            removed=frozenset(old_ids - new_ids),
            activated=frozenset(activated),
            deactivated=frozenset(deactivated),
            retyped=frozenset(retyped),
            updated=frozenset(
                affected & new_ids - added - activated - deactivated - retyped
            ),
            updated_devices=frozenset(changed_devices & new_devices),
            removed_devices=frozenset(old_devices - new_devices),
        )

    @staticmethod
    def _descriptor(value):
        result = {"vt": 0}
        for key in ("vt", "ac", "ed", "pr", "ml"):
            if type(value.get(key)) is int:
                result[key] = value[key]
        for key in ("mn", "mx", "st"):
            if _number(value.get(key)):
                result[key] = value[key]
        if isinstance(value.get("ev"), list):
            result["ev"] = [item for item in value["ev"] if type(item) is int]
        if isinstance(value.get("fv"), list):
            result["fv"] = [item for item in value["fv"] if type(item) is int]
        if isinstance(value.get("un"), str):
            result["un"] = value["un"]
        if type(value.get("sc")) is int:
            result["sc"] = value["sc"]
        return result

    @staticmethod
    def _semantic_state(resource, row: dict[str, Any]) -> dict[str, Any]:
        values = row.get("st") if isinstance(row.get("st"), dict) else {}
        text = row.get("tx") if isinstance(row.get("tx"), dict) else {}
        result = {}
        if resource.entity_type == 7:
            result["input"] = {**values, **text}
        for cap, key in _STATE_KEYS.items():
            value = values.get(cap)
            descriptor = resource.overrides.get(cap, {})
            expected_type = (
                1
                if cap == "1"
                else 3
                if cap in {"5", "6", "12", "14", "15", "30"}
                else 2
            )
            if descriptor.get("vt", expected_type) != expected_type:
                continue
            if cap == "1":
                if type(value) in (int, bool) and value in (0, 1):
                    result[key] = bool(value)
            elif _number(value):
                result[key] = value
        if resource.entity_type in {1, 2, 5, 7}:
            readable = [
                str(cap)
                for cap in resource.capabilities
                if type(resource.overrides.get(str(cap), {}).get("ac")) is int
                and resource.overrides[str(cap)]["ac"] & 1
            ]
            if len(readable) == 1:
                cap = readable[0]
                value = text.get(cap, values.get(cap))
                vt = resource.overrides[cap].get("vt")
                if vt == 1 and type(value) in (int, bool) and value in (0, 1):
                    result["v"] = bool(value)
                elif (
                    vt in (2, 3, 4, 5, 6, 7, 8)
                    and _number(value)
                    or vt == 9
                    and isinstance(value, str)
                ):
                    result["v"] = value
        if resource.entity_type == 9 and "pos" in result:
            result["v"] = result["pos"]
        return result

    def control_cp(self, resource, state: dict[str, Any]) -> dict[str, int | float]:
        """Validate state keys and values and map them to WDP capability IDs."""
        if not state or any(key not in _CONTROL_KEYS for key in state):
            raise ValueError("Unsupported or empty control")
        result = {}
        for key, value in state.items():
            cap = _CONTROL_KEYS[key]
            if key == "v":
                if not isinstance(value, bool):
                    raise ValueError("Power must be boolean")
                value = int(value)
            if not _number(value):
                raise ValueError("Control value must be finite numeric")
            if key == "br_level" and not isinstance(value, int):
                raise ValueError("Brightness level must be an integer")
            if key == "swing":
                descriptor = resource.overrides.get(cap, {})
                known = {item["value"] for item in CAPABILITY_BY_ID[30]["values"]}
                if (
                    type(value) is not int
                    or value not in known
                    or 30 not in resource.capabilities
                    or descriptor.get("vt") != 3
                    or not descriptor.get("ac", 0) & 2
                    or value not in descriptor.get("ev", [])
                ):
                    raise ValueError("Unsupported swing mode")
            result[cap] = value
        return result
