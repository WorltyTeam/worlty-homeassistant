"""Handle newline-delimited input and raw JSON output over classic pad TCP.

Classic pad firmware does not use the Worlty Hub Device API or a WebSocket
handshake. Authentication starts no background tasks; callers close streams.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import suppress
from typing import Any

CONNECT_TIMEOUT = 5
MESSAGE_TIMEOUT = 5
SETUP_TIMEOUT = 15
CLOSE_TIMEOUT = 2
MAX_MESSAGE_BYTES = 64 * 1024


class WorltyV1Error(Exception):
    """Represent a classic pad TCP protocol error."""


class WorltyV1AuthenticationError(WorltyV1Error):
    """Represent explicit token rejection or a changed device identity."""


class WorltyV1ConnectionError(WorltyV1Error):
    """Represent connection or handshake failure."""


def validate_device_info(data: Any) -> dict[str, Any]:
    """Validate device ID and MAC, using the ID as a fallback display name."""
    if not isinstance(data, dict):
        raise WorltyV1ConnectionError("Invalid authenticated device data")
    device_id = data.get("device_id")
    mac = data.get("mac_address")
    if not isinstance(device_id, str) or not device_id.strip():
        raise WorltyV1ConnectionError("Missing authenticated device identity")
    if not isinstance(mac, str) or not re.fullmatch(
        r"(?:[0-9a-fA-F]{12}|(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}|"
        r"(?:[0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2})",
        mac,
    ):
        raise WorltyV1ConnectionError("Missing authenticated MAC address")
    mac = mac.replace(":", "").replace("-", "").upper()
    if mac in ("000000000000", "FFFFFFFFFFFF"):
        raise WorltyV1ConnectionError("Invalid authenticated MAC address")
    name = next(
        (
            value.strip()
            for key in ("friendly_name", "name")
            if isinstance(value := data.get(key), str) and value.strip()
        ),
        device_id,
    )
    return {**data, "device_id": device_id, "mac_address": mac, "name": name}


async def async_read_message(
    reader, timeout: float | None = MESSAGE_TIMEOUT
) -> dict[str, Any]:
    """Read a bounded JSON line and reject EOF or malformed data."""
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not raw:
            raise WorltyV1ConnectionError("Connection closed")
        message = json.loads(raw.decode("utf-8"))
        if not isinstance(message, dict):
            raise WorltyV1ConnectionError("Invalid message object")
        return message
    except (TimeoutError, OSError, UnicodeError, ValueError) as err:
        raise WorltyV1ConnectionError("Invalid or missing TCP response") from err


async def async_write_message(writer, payload: dict[str, Any]) -> None:
    """Write raw JSON using the classic pad firmware framing format."""
    try:
        writer.write(json.dumps(payload).encode("utf-8"))
        await asyncio.wait_for(writer.drain(), timeout=MESSAGE_TIMEOUT)
    except (TimeoutError, OSError, TypeError, ValueError) as err:
        raise WorltyV1ConnectionError("TCP send failed") from err


def _auth_rejected(message: dict[str, Any]) -> bool:
    """Distinguish explicit credential rejection from transport failure."""
    return message.get("type") == "auth_invalid"


async def async_authenticate(reader, writer, access_token: str) -> dict[str, Any]:
    """Perform the auth_required, auth, and authenticated exchange."""
    message = await async_read_message(reader)
    if _auth_rejected(message):
        raise WorltyV1AuthenticationError("Access token rejected")
    if message.get("type") != "auth_required":
        raise WorltyV1ConnectionError("Missing authentication request")
    await async_write_message(
        writer, {"type": "auth", "access_token": access_token, "platform": "ha"}
    )
    message = await async_read_message(reader)
    if _auth_rejected(message):
        raise WorltyV1AuthenticationError("Access token rejected")
    if message.get("type") != "authenticated":
        raise WorltyV1ConnectionError("Missing authentication response")
    return validate_device_info(message.get("data"))


async def async_close_writer(writer) -> None:
    """Close the TCP connection and wait for completion within a timeout."""
    if writer is None:
        return
    with suppress(TimeoutError, OSError):
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=CLOSE_TIMEOUT)
