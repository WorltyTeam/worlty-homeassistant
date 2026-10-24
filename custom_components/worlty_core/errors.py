"""Exceptions for Worlty Core."""

from __future__ import annotations


class DeviceApiValidationError(ValueError):
    """Invalid incoming or outgoing Local Device API data."""


class WorltyError(Exception):
    """Base error for Worlty Core."""


class WorltyConnectionError(WorltyError):
    """Local WSS connection failure."""


class WorltyAuthenticationError(WorltyError):
    """Hub rejection of the local client identity."""


class WorltyIdentityMismatchError(WorltyAuthenticationError):
    """Verified server identity that differs from the selected hub."""


class WorltyPinMismatchError(WorltyIdentityMismatchError):
    """Hub TLS SPKI that differs from the trusted pin."""


class WorltyRequestError(WorltyError):
    """Device API request failure with an error code."""

    def __init__(self, code: str, detail: str = "") -> None:
        """Store the request failure code and details."""
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
