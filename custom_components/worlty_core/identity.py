"""Local HA client identity and preservation of earlier storage records."""

from __future__ import annotations

import asyncio
import re
import secrets
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .crypto import generate_private_key_pem, load_private_key
from .errors import WorltyAuthenticationError

_STORE_VERSION = 3
# Keep the storage key so HA can migrate the existing version 2 Store.
_STORE_KEY = f"{DOMAIN}.client_identity_v2"
_DATA_IDENTITY_REPOSITORY = f"{DOMAIN}.client_identity_repository"
_CLIENT_ID_PATTERN = re.compile(r"^ha_[A-Za-z0-9._:-]{5,125}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")

PadRotationState = Literal[
    "awaiting_ticket",
    "pairing",
    "paired",
    "revoke_sent",
    "committed",
]
_PAD_ROTATION_STATES = frozenset(
    {
        "awaiting_ticket",
        "pairing",
        "paired",
        "revoke_sent",
        "committed",
    }
)


@dataclass(frozen=True, slots=True)
class WorltyClientIdentity:
    """Client identity shared by hub entries in one HA installation."""

    client_id: str
    ha_instance_id: str
    private_key_pem: str


@dataclass(frozen=True, slots=True)
class WorltyPadClientRotation:
    """Durable progress for one hub in an HA-wide key rotation."""

    state: PadRotationState
    ticket_digest: str | None
    overlap_expires_at: int | None


@dataclass(frozen=True, slots=True)
class WorltyClientRotation:
    """Pending HA-wide identity rotation."""

    replaces_client_id: str
    replacement: WorltyClientIdentity
    created_at: int
    pads: Mapping[str, WorltyPadClientRotation]


@dataclass(frozen=True, slots=True)
class WorltyIdentityState:
    """Active identity and an optional replacement that is not yet active."""

    active: WorltyClientIdentity
    rotation: WorltyClientRotation | None


class _IdentityStore(Store[dict[str, Any]]):
    """Internal Store with explicit v2-to-v3 migration."""

    async def async_save(self, data: dict[str, Any]) -> None:
        # During shutdown, Store defers writes to FINAL_WRITE instead of completing
        # them.
        # Automatic migration uses this path too, so prevent scheduling the write.
        # There is no await between this check and the parent Store's shutdown check.
        if self.hass.is_stopping:
            raise WorltyAuthenticationError(
                "HA client identity is unavailable during shutdown"
            )
        await super().async_save(data)

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        if old_major_version != 2 or old_minor_version != 1:
            raise WorltyAuthenticationError(
                "stored HA client identity version is unsupported; "
                "explicit re-pairing is required"
            )
        return {
            "active": old_data,
            "rotation": None,
        }


class _IdentityRepository:
    """Serialize identity reads and durable multi-device rotation updates."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._lock = asyncio.Lock()
        self._state: WorltyIdentityState | None = None

    async def async_resolve(self, client_id: str | None) -> WorltyClientIdentity:
        async with self._lock:
            state = await self._async_ensure_loaded()
            if client_id is None or secrets.compare_digest(
                client_id,
                state.active.client_id,
            ):
                return state.active
            rotation = state.rotation
            if rotation is not None and secrets.compare_digest(
                client_id,
                rotation.replacement.client_id,
            ):
                return rotation.replacement
            raise WorltyAuthenticationError(
                "stored pairing principal does not match the HA client identity"
            )

    async def _async_ensure_loaded(self) -> WorltyIdentityState:
        if self._state is not None:
            return self._state
        # Cancellation must not leave a Store write that could overwrite the next
        # attempt.
        # Hold the repository lock until that I/O finishes.
        task = asyncio.create_task(self._async_load_state())
        try:
            state = await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                with suppress(Exception, asyncio.CancelledError):
                    await asyncio.shield(task)
            if not task.cancelled():
                task.exception()
            raise
        self._state = state
        return state

    def _new_store(self) -> _IdentityStore:
        return _IdentityStore(
            self._hass,
            _STORE_VERSION,
            _STORE_KEY,
            private=True,
            atomic_writes=True,
        )

    async def _async_load_state(self) -> WorltyIdentityState:
        if self._hass.is_stopping:
            raise WorltyAuthenticationError(
                "HA client identity is unavailable during shutdown"
            )
        # A failed or deferred write's RAM cache is not proof of durable storage.
        # Read the same key through a fresh Store instance each time.
        store = self._new_store()
        value = await store.async_load()
        if self._hass.is_stopping:
            raise WorltyAuthenticationError(
                "HA client identity is unavailable during shutdown"
            )
        if value is None:
            state = WorltyIdentityState(
                active=_new_identity(),
                rotation=None,
            )
        else:
            try:
                # Some test Stores and older HA fixtures do not run Store migrations.
                if {"client_id", "ha_instance_id", "private_key_pem"}.issubset(value):
                    state = WorltyIdentityState(
                        active=_parse_identity(value),
                        rotation=None,
                    )
                else:
                    return _parse_state(value)
            except (KeyError, TypeError, ValueError, AttributeError) as err:
                raise WorltyAuthenticationError(
                    "stored HA client identity is corrupt; explicit re-pairing is required"
                ) from err
        serialized = _serialize_state(state)
        await store.async_save(serialized)
        # HA Store may only log write failures or defer writes during shutdown.
        # The same instance can return pending RAM values, so use a fresh reader.
        # Expose the identity for registration only after verifying durable storage.
        if await self._new_store().async_load() != serialized:
            raise WorltyAuthenticationError("HA client identity could not be persisted")
        return state


def _new_identity() -> WorltyClientIdentity:
    return WorltyClientIdentity(
        client_id=f"ha_{secrets.token_hex(12)}",
        ha_instance_id=str(uuid.uuid4()),
        private_key_pem=generate_private_key_pem(),
    )


def _parse_identity(value: Mapping[str, Any]) -> WorltyClientIdentity:
    identity = WorltyClientIdentity(
        client_id=str(value["client_id"]),
        ha_instance_id=str(value["ha_instance_id"]),
        private_key_pem=str(value["private_key_pem"]),
    )
    if (
        _CLIENT_ID_PATTERN.fullmatch(identity.client_id) is None
        or str(uuid.UUID(identity.ha_instance_id)) != identity.ha_instance_id
    ):
        raise ValueError("invalid client identity")
    load_private_key(identity.private_key_pem)
    return identity


def _parse_pad_rotation(value: Mapping[str, Any]) -> WorltyPadClientRotation:
    state = str(value["state"])
    if state not in _PAD_ROTATION_STATES:
        raise ValueError("invalid PAD rotation state")
    ticket_digest = value.get("ticket_digest")
    overlap_expires_at = value.get("overlap_expires_at")
    if state == "awaiting_ticket":
        if ticket_digest is not None or overlap_expires_at is not None:
            raise ValueError("awaiting PAD rotation cannot retain a ticket")
    elif (
        not isinstance(ticket_digest, str)
        or _DIGEST_PATTERN.fullmatch(ticket_digest) is None
        or type(overlap_expires_at) is not int
        or overlap_expires_at <= 0
    ):
        raise ValueError("invalid PAD rotation ticket binding")
    return WorltyPadClientRotation(
        state=state,  # type: ignore[arg-type]
        ticket_digest=ticket_digest,
        overlap_expires_at=overlap_expires_at,
    )


def _parse_state(value: Mapping[str, Any]) -> WorltyIdentityState:
    active_value = value["active"]
    if not isinstance(active_value, Mapping):
        raise TypeError("active identity must be an object")
    active = _parse_identity(active_value)
    rotation_value = value.get("rotation")
    if rotation_value is None:
        return WorltyIdentityState(active=active, rotation=None)
    if not isinstance(rotation_value, Mapping):
        raise TypeError("rotation must be an object")
    replacement_value = rotation_value["replacement"]
    pads_value = rotation_value["pads"]
    if not isinstance(replacement_value, Mapping) or not isinstance(
        pads_value, Mapping
    ):
        raise TypeError("rotation replacement and pads must be objects")
    replacement = _parse_identity(replacement_value)
    replaces_client_id = str(rotation_value["replaces_client_id"])
    created_at = rotation_value["created_at"]
    if (
        not secrets.compare_digest(replaces_client_id, active.client_id)
        or replacement.ha_instance_id != active.ha_instance_id
        or secrets.compare_digest(replacement.client_id, active.client_id)
        or type(created_at) is not int
        or created_at <= 0
        or not pads_value
    ):
        raise ValueError("invalid client rotation")
    pads: dict[str, WorltyPadClientRotation] = {}
    for pad_id, pad_value in pads_value.items():
        if (
            not isinstance(pad_id, str)
            or not pad_id
            or not isinstance(pad_value, Mapping)
        ):
            raise TypeError("invalid PAD rotation record")
        pads[pad_id] = _parse_pad_rotation(pad_value)
    return WorltyIdentityState(
        active=active,
        rotation=WorltyClientRotation(
            replaces_client_id=replaces_client_id,
            replacement=replacement,
            created_at=created_at,
            pads=pads,
        ),
    )


def _serialize_identity(identity: WorltyClientIdentity) -> dict[str, str]:
    return {
        "client_id": identity.client_id,
        "ha_instance_id": identity.ha_instance_id,
        "private_key_pem": identity.private_key_pem,
    }


def _serialize_state(state: WorltyIdentityState) -> dict[str, Any]:
    rotation = state.rotation
    return {
        "active": _serialize_identity(state.active),
        "rotation": (
            None
            if rotation is None
            else {
                "replaces_client_id": rotation.replaces_client_id,
                "replacement": _serialize_identity(rotation.replacement),
                "created_at": rotation.created_at,
                "pads": {
                    pad_id: {
                        "state": pad.state,
                        "ticket_digest": pad.ticket_digest,
                        "overlap_expires_at": pad.overlap_expires_at,
                    }
                    for pad_id, pad in sorted(rotation.pads.items())
                },
            }
        ),
    }


def _repository(hass: HomeAssistant) -> _IdentityRepository:
    repository: Any = hass.data.get(_DATA_IDENTITY_REPOSITORY)
    if isinstance(repository, _IdentityRepository):
        return repository
    repository = _IdentityRepository(hass)
    hass.data[_DATA_IDENTITY_REPOSITORY] = repository
    return repository


async def async_get_client_identity(
    hass: HomeAssistant,
    *,
    client_id: str | None = None,
) -> WorltyClientIdentity:
    """Resolve the active or pending identity selected by the config entry."""
    return await _repository(hass).async_resolve(client_id)
