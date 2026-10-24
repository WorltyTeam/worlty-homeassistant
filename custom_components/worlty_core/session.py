"""Per-hub Home Assistant session bindings."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from .errors import WorltyAuthenticationError


@dataclass(frozen=True, slots=True)
class PadSession:
    """Active binding of one hub to an authenticated external platform."""

    pad_id: str
    platform_id: int
    platform_instance_id: str


class PadSessionRegistry:
    """One fail-closed HA binding per hub ID."""

    def __init__(self) -> None:
        """Initialize the registry of active hub sessions."""
        self._sessions_by_pad: dict[str, PadSession] = {}

    def bind(
        self,
        *,
        pad_id: str,
        platform_id: int,
        platform_instance_id: str,
    ) -> PadSession:
        """Bind the hub and reject a different platform principal."""
        if not pad_id or platform_id <= 0 or not platform_instance_id:
            raise WorltyAuthenticationError("PAD session identity is invalid")
        existing = self._sessions_by_pad.get(pad_id)
        if existing is not None:
            if existing.platform_id != platform_id or not secrets.compare_digest(
                existing.platform_instance_id,
                platform_instance_id,
            ):
                raise WorltyAuthenticationError(
                    "PAD session is already bound to another external platform"
                )
            return existing

        session = PadSession(
            pad_id=pad_id,
            platform_id=platform_id,
            platform_instance_id=platform_instance_id,
        )
        self._sessions_by_pad[pad_id] = session
        return session

    def release(self, session: PadSession) -> None:
        """Release only an exactly matching active binding."""
        if self._sessions_by_pad.get(session.pad_id) is session:
            self._sessions_by_pad.pop(session.pad_id)
