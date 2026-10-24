"""Persistent Worlty Local Device API client."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from aiohttp import ClientError, ClientSession, ClientWebSocketResponse, WSMsgType

from .const import (
    AUTH_SCOPES,
    EXTERNAL_PLATFORM_ID_HOME_ASSISTANT,
    LOCAL_API_PATH,
    LOCAL_MAX_MESSAGE_BYTES,
    LOCAL_PAIRING_PATH,
    LOCAL_SUBPROTOCOL,
)
from .crypto import (
    base64url_decode,
    base64url_encode,
    build_auth_transcript,
    raw_public_key_base64url,
    raw_public_key_sha256,
    sign_auth_transcript,
)
from .errors import (
    DeviceApiValidationError,
    WorltyAuthenticationError,
    WorltyConnectionError,
    WorltyIdentityMismatchError,
    WorltyRequestError,
)
from .identity import WorltyClientIdentity
from .tls import (
    create_worlty_tls_context,
    normalize_tls_hostname,
    validate_peer_leaf,
    verified_peer_identity,
)
from .wdp import local_api_scope_mask
from .wdp.external_auth import (
    encode_local_pairing_proposal_transcript,
    local_pairing_proposal_digest_hex,
)

_LOGGER = logging.getLogger(__name__)
MessageHandler = Callable[[dict[str, Any]], Awaitable[bool | None]]


def _device_envelope(frame: Mapping[str, Any], *, incoming: bool, scope: int) -> str:
    """Validate transport ownership independently of firmware Device schemas."""
    contract = frame.get("contract")
    allowed = (
        {"PadDeviceSyncPage", "PadDeviceControlResult"}
        if incoming
        else {"PadDeviceSyncQuery", "PadDeviceControl"}
    )
    if contract not in allowed:
        raise DeviceApiValidationError("Device application direction is invalid")
    required = 2 if contract in {"PadDeviceControl", "PadDeviceControlResult"} else 1
    if not scope & required:
        raise WorltyAuthenticationError("Device application scope is not granted")
    rid = frame.get("rid")
    if (
        frame.get("t") != ("platform_update" if incoming else "platform_request")
        or not isinstance(rid, str)
        or not rid
        or len(rid) > 128
        or not isinstance(frame.get("payload"), Mapping)
    ):
        raise DeviceApiValidationError("Device application envelope is invalid")
    return str(contract)


def _read_challenge(frame: Mapping[str, Any], *, pairing: bool = False) -> None:
    """Read required authentication fields while allowing extra fields."""
    expected = "pair_challenge" if pairing else "challenge"
    session = frame.get("session_id")
    nonce = frame.get("server_nonce")
    if (
        frame.get("t") != expected
        or not isinstance(session, str)
        or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", session) is None
    ):
        raise WorltyAuthenticationError("PAD challenge session is invalid")
    try:
        decoded = base64url_decode(nonce)
        if len(decoded) != 32 or base64url_encode(decoded) != nonce:
            raise ValueError("nonce width")
    except ValueError as err:
        raise WorltyAuthenticationError("PAD challenge nonce is invalid") from err
    if pairing:
        value = frame.get("pairing_window_id")
        if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{32}", value) is None:
            raise WorltyAuthenticationError("PAD pairing window is invalid")
    else:
        # This is the signed transcript version, not the firmware or Device schema
        # version.
        if frame.get("pv") != "1.0.0":
            raise WorltyAuthenticationError(
                "PAD authentication transcript is unsupported"
            )
        value = frame.get("device_id")
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is None
        ):
            raise WorltyAuthenticationError("PAD challenge identity is invalid")


def _binding_generation(value: Any) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{16}", value) is None
        or int(value, 16) == 0
    ):
        raise WorltyAuthenticationError("PAD binding generation is invalid")
    return value


def _scopes_from_mask(
    value: object,
    *,
    requested_scopes: tuple[str, ...],
) -> tuple[str, ...]:
    """Require the granted scope mask to match the requested scopes exactly."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorltyAuthenticationError("PAD returned invalid scope mask")
    requested_mask = local_api_scope_mask(requested_scopes)
    if value != requested_mask:
        raise WorltyAuthenticationError("PAD did not grant approved scopes")
    return requested_scopes


def _peer_certificate_der(websocket: ClientWebSocketResponse) -> bytes:
    """Read the leaf certificate after TLS chain and hostname validation."""
    ssl_object = websocket.get_extra_info("ssl_object")
    certificate = (
        ssl_object.getpeercert(binary_form=True) if ssl_object is not None else None
    )
    if not certificate:
        raise WorltyConnectionError("WSS peer certificate is unavailable")
    return certificate


def _verify_peer_identity(
    websocket: ClientWebSocketResponse,
    *,
    expected_target_id: str,
    expected_spki_sha256: str,
) -> None:
    """Check the hub leaf profile and extra pin after normal TLS validation."""
    validate_peer_leaf(
        _peer_certificate_der(websocket),
        expected_target_id=expected_target_id,
        expected_spki_sha256=expected_spki_sha256,
    )


async def _receive_json(
    websocket: ClientWebSocketResponse,
    *,
    timeout: float,
) -> dict[str, Any]:
    async with asyncio.timeout(timeout):
        message = await websocket.receive()
    if message.type != WSMsgType.TEXT:
        raise WorltyConnectionError(f"unexpected WebSocket frame: {message.type}")
    try:
        value = json.loads(message.data)
    except (TypeError, ValueError) as err:
        raise WorltyConnectionError("PAD returned invalid JSON") from err
    if not isinstance(value, dict):
        raise WorltyConnectionError("PAD frame must be a JSON object")
    return value


async def _open_verified(
    session: ClientSession,
    *,
    host: str,
    port: int,
    tls_hostname: str,
    expected_pin: str | None,
    expected_target_id: str | None,
    path: str,
    bootstrap: bool = False,
) -> ClientWebSocketResponse:
    websocket = None
    try:
        if not bootstrap and (not expected_pin or not expected_target_id):
            raise WorltyAuthenticationError(
                "registered connection requires identity and pin"
            )
        async with asyncio.timeout(5.0):
            tls_context = await asyncio.to_thread(create_worlty_tls_context)
            websocket = await session.ws_connect(
                f"wss://{host}:{port}{path}",
                protocols=(LOCAL_SUBPROTOCOL,),
                ssl=tls_context,
                server_hostname=normalize_tls_hostname(tls_hostname),
                heartbeat=30 if path == LOCAL_API_PATH else None,
                compress=0,
                max_msg_size=LOCAL_MAX_MESSAGE_BYTES,
            )
            if websocket.protocol != LOCAL_SUBPROTOCOL:
                raise WorltyConnectionError(
                    "PAD did not negotiate the required subprotocol"
                )
            if bootstrap:
                verified_peer_identity(_peer_certificate_der(websocket))
            else:
                _verify_peer_identity(
                    websocket,
                    expected_target_id=expected_target_id,
                    expected_spki_sha256=expected_pin,
                )
        return websocket
    except (Exception, asyncio.CancelledError):
        if websocket is not None:
            await websocket.close()
        raise


async def async_probe_device(
    session: ClientSession, *, host: str, port: int, tls_hostname: str
) -> dict[str, str]:
    """Discover identity over CA-verified TLS without granting client trust."""
    websocket = None
    try:
        websocket = await _open_verified(
            session,
            host=host,
            port=port,
            tls_hostname=tls_hostname,
            expected_pin=None,
            expected_target_id=None,
            path=LOCAL_API_PATH,
            bootstrap=True,
        )
        target_id, pin = verified_peer_identity(_peer_certificate_der(websocket))
        challenge = await _receive_json(websocket, timeout=5.0)
        _read_challenge(challenge)
        if challenge["t"] != "challenge":
            raise WorltyAuthenticationError("PAD challenge missing")
        return {
            "device_id": challenge["device_id"],
            "tls_target_id": target_id,
            "tls_spki_sha256": pin,
        }
    except ClientError as err:
        raise WorltyConnectionError("PAD identity discovery failed") from err
    finally:
        if websocket is not None:
            await websocket.close()


async def async_pair_device(
    session: ClientSession,
    *,
    host: str,
    port: int,
    tls_hostname: str,
    expected_pin: str,
    expected_target_id: str,
    expected_device_id: str,
    identity: WorltyClientIdentity,
    approval_deadline: float,
    proposal_ready: Callable[[str, str], None] | None = None,
) -> tuple[tuple[str, ...], str]:
    """Present PoP and await durable hub approval within one absolute deadline."""
    loop = asyncio.get_running_loop()
    if not 0 < approval_deadline - loop.time() < float("inf"):
        raise ValueError("pairing requires a finite future deadline")
    connection = dict(
        host=host,
        port=port,
        tls_hostname=tls_hostname,
        expected_pin=expected_pin,
        expected_target_id=expected_target_id,
    )
    websocket = None
    try:
        async with asyncio.timeout_at(approval_deadline):
            websocket = await _open_verified(session, path=LOCAL_API_PATH, **connection)
            challenge = await _receive_json(websocket, timeout=5.0)
            _read_challenge(challenge)
            if challenge["t"] != "challenge" or not hmac.compare_digest(
                challenge["device_id"], expected_device_id
            ):
                raise WorltyIdentityMismatchError("PAD challenge identity mismatch")
            await websocket.close()
            websocket = None
            websocket = await _open_verified(
                session, path=LOCAL_PAIRING_PATH, **connection
            )
            async with asyncio.timeout(5.0):
                pairing = await _receive_json(websocket, timeout=5.0)
                _read_challenge(pairing, pairing=True)
                if pairing["t"] != "pair_challenge":
                    raise WorltyAuthenticationError("PAD pair challenge missing")
                nonce = base64url_encode(secrets.token_bytes(32))
                fields = {
                    "protocol_version": challenge["pv"],
                    "pad_id": expected_device_id,
                    "pairing_window_id": pairing["pairing_window_id"],
                    "client_id": identity.client_id,
                    "platform_id": EXTERNAL_PLATFORM_ID_HOME_ASSISTANT,
                    "platform_instance_id": identity.ha_instance_id,
                    "server_nonce": pairing["server_nonce"],
                    "client_nonce": nonce,
                    "session_id": pairing["session_id"],
                    "requested_scope_mask": 3,
                    "client_public_key_sha256": raw_public_key_sha256(
                        identity.private_key_pem
                    ),
                }
                frame = {
                    "t": "pair_proposal",
                    "session_id": pairing["session_id"],
                    "pairing_window_id": pairing["pairing_window_id"],
                    "client_id": identity.client_id,
                    "platform_id": EXTERNAL_PLATFORM_ID_HOME_ASSISTANT,
                    "platform_instance_id": identity.ha_instance_id,
                    "scope_mask": 3,
                    "public_key": raw_public_key_base64url(identity.private_key_pem),
                    "client_nonce": nonce,
                    "signature": sign_auth_transcript(
                        identity.private_key_pem,
                        encode_local_pairing_proposal_transcript(fields),
                    ),
                }
                await websocket.send_json(frame)
            if proposal_ready is not None:
                proposal_ready(
                    pairing["pairing_window_id"],
                    local_pairing_proposal_digest_hex(fields),
                )
            result = await _receive_json(
                websocket, timeout=max(0.0, approval_deadline - loop.time())
            )
            if result.get("t") == "error":
                raise WorltyAuthenticationError("PAD rejected registration")
            if (
                result.get("t") != "pair_ok"
                or type(result.get("platform_id")) is not int
                or result["platform_id"] != EXTERNAL_PLATFORM_ID_HOME_ASSISTANT
                or type(result.get("scope_mask")) is not int
                or result["scope_mask"] != 3
                or not hmac.compare_digest(
                    str(result.get("client_id", "")), identity.client_id
                )
                or not hmac.compare_digest(
                    str(result.get("platform_instance_id", "")), identity.ha_instance_id
                )
            ):
                raise WorltyAuthenticationError("PAD pairing receipt mismatch")
            return AUTH_SCOPES, _binding_generation(result.get("binding_generation"))
    except (ClientError, TimeoutError) as err:
        raise WorltyConnectionError("Local registration did not complete") from err
    finally:
        if websocket is not None:
            await websocket.close()


class WorltyLocalClient:
    """Authenticated persistent Local Device API session."""

    def __init__(
        self,
        session: ClientSession,
        *,
        host: str,
        port: int,
        tls_hostname: str,
        expected_pin: str,
        expected_device_id: str,
        binding_generation: str,
        expected_target_id: str,
        identity: WorltyClientIdentity,
        message_handler: MessageHandler,
        requested_scopes: tuple[str, ...] = AUTH_SCOPES,
    ) -> None:
        """Initialize session state for authentication and request tracking."""
        self._session = session
        self._host = host
        self._port = port
        self._tls_hostname = normalize_tls_hostname(tls_hostname)
        self._expected_pin = expected_pin
        self._expected_device_id = expected_device_id
        self._expected_target_id = expected_target_id
        if (
            not isinstance(binding_generation, str)
            or len(binding_generation) != 16
            or binding_generation == "0000000000000000"
            or any(
                character not in "0123456789abcdef" for character in binding_generation
            )
        ):
            raise ValueError("binding generation must be nonzero lowercase u64 hex")
        self._binding_generation = binding_generation
        self._identity = identity
        self._message_handler = message_handler
        if len(requested_scopes) != 2 or set(requested_scopes) != set(AUTH_SCOPES):
            raise ValueError("HA requires observe and control scopes")
        self._requested_scopes = AUTH_SCOPES
        self._websocket: ClientWebSocketResponse | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._verified_spki_sha256 = ""
        self._send_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._request_updates: dict[str, MessageHandler] = {}
        self._device_queries: set[str] = set()
        self.device_id = ""
        self.granted_scopes: tuple[str, ...] = ()
        self.granted_scope_mask = 0
        self.transport_session_id = ""
        self.session_id_hash = ""
        self._connection_stage = "idle"
        self._last_received_at: str | None = None

    def connection_diagnostics(self) -> dict[str, Any]:
        """Return observations without peer addresses or credentials."""
        return {
            "stage": self._connection_stage,
            "last_received_at": self._last_received_at,
        }

    def _record_received(self) -> None:
        # Optional timing samples must not cause received frames to be rejected.
        with suppress(OSError, OverflowError, ValueError):
            self._last_received_at = datetime.now(UTC).isoformat(timespec="seconds")

    @property
    def connected(self) -> bool:
        """Check whether the current WebSocket is open."""
        return self._websocket is not None and not self._websocket.closed

    @property
    def verified_spki_sha256(self) -> str:
        """Return the SPKI selected by the fully authenticated TLS session."""
        return self._verified_spki_sha256

    def set_expected_pin(self, expected_pin: str) -> None:
        """Select an explicitly approved pin for the next TLS connection."""
        if self.connected:
            raise WorltyConnectionError(
                "cannot change the expected pin on an active TLS session"
            )
        self._expected_pin = expected_pin

    async def async_connect(self) -> str:
        """Pin and authenticate the actual connected TLS session."""
        if self.connected:
            return self.device_id
        self._verified_spki_sha256 = ""
        selected_pin = self._expected_pin
        self._connection_stage = "tls_wss"
        try:
            websocket = await _open_verified(
                self._session,
                host=self._host,
                port=self._port,
                tls_hostname=self._tls_hostname,
                expected_pin=selected_pin,
                expected_target_id=self._expected_target_id,
                path=LOCAL_API_PATH,
            )
            self._connection_stage = "authentication"
            try:
                async with asyncio.timeout(5.0):
                    challenge = await _receive_json(websocket, timeout=5.0)
                    self._record_received()
                    _read_challenge(challenge)
                    if challenge.get("t") != "challenge":
                        raise WorltyAuthenticationError("PAD challenge is missing")
                    device_id = str(challenge["device_id"])
                    if (
                        self._expected_device_id
                        and device_id != self._expected_device_id
                    ):
                        raise WorltyIdentityMismatchError(
                            "discovered PAD identity changed"
                        )

                    client_nonce = base64url_encode(secrets.token_bytes(32))
                    transcript = build_auth_transcript(
                        protocol_version=str(challenge["pv"]),
                        session_id=str(challenge["session_id"]),
                        pad_id=device_id,
                        server_nonce=str(challenge["server_nonce"]),
                        client_nonce=client_nonce,
                        client_id=self._identity.client_id,
                        platform_id=EXTERNAL_PLATFORM_ID_HOME_ASSISTANT,
                        platform_instance_id=self._identity.ha_instance_id,
                        requested_scope_mask=local_api_scope_mask(
                            self._requested_scopes
                        ),
                        binding_generation=self._binding_generation,
                    )
                    auth = {
                        "t": "auth",
                        "session_id": challenge["session_id"],
                        "client_id": self._identity.client_id,
                        "platform_id": EXTERNAL_PLATFORM_ID_HOME_ASSISTANT,
                        "platform_instance_id": self._identity.ha_instance_id,
                        "scope_mask": local_api_scope_mask(self._requested_scopes),
                        "client_nonce": client_nonce,
                        "binding_generation": self._binding_generation,
                        "signature": sign_auth_transcript(
                            self._identity.private_key_pem,
                            transcript,
                        ),
                    }
                    await websocket.send_json(auth)
                    auth_result = await _receive_json(websocket, timeout=5.0)
                    self._record_received()
                    if auth_result.get("t") == "error":
                        raise WorltyAuthenticationError(
                            str(auth_result.get("code", "authentication_failed"))
                        )
                    if auth_result.get("t") != "auth_ok":
                        raise WorltyAuthenticationError(
                            "PAD did not confirm authentication"
                        )
                    granted_scopes = _scopes_from_mask(
                        auth_result.get("scope_mask"),
                        requested_scopes=self._requested_scopes,
                    )
                    if not hmac.compare_digest(
                        str(auth_result.get("session_id", "")),
                        str(challenge["session_id"]),
                    ):
                        raise WorltyAuthenticationError("PAD auth session changed")
                    principal = auth_result.get("principal")
                    if (
                        not isinstance(principal, Mapping)
                        or type(principal.get("platform_id")) is not int
                        or principal.get("platform_id")
                        != EXTERNAL_PLATFORM_ID_HOME_ASSISTANT
                        or not hmac.compare_digest(
                            str(principal.get("platform_instance_id", "")),
                            self._identity.ha_instance_id,
                        )
                        or not hmac.compare_digest(
                            str(principal.get("client_id", "")),
                            self._identity.client_id,
                        )
                        or principal.get("client_public_key_sha256")
                        != raw_public_key_sha256(self._identity.private_key_pem)
                        or principal.get("binding_generation")
                        != self._binding_generation
                        or principal.get("session_id_hash")
                        != hashlib.sha256(
                            challenge["session_id"].encode("utf-8")
                        ).hexdigest()
                        or principal.get("transport") != "local_wss"
                        or type(principal.get("scope_mask")) is not int
                        or principal.get("scope_mask") != auth_result.get("scope_mask")
                    ):
                        raise WorltyAuthenticationError(
                            "PAD auth principal does not match this external platform"
                        )
            except (Exception, asyncio.CancelledError):
                await websocket.close()
                raise
        except (ClientError, TimeoutError) as err:
            raise WorltyConnectionError(str(err)) from err

        self._websocket = websocket
        self._verified_spki_sha256 = selected_pin
        self.device_id = device_id
        self.granted_scopes = granted_scopes
        self.granted_scope_mask = local_api_scope_mask(granted_scopes)
        self.transport_session_id = str(challenge["session_id"])
        self.session_id_hash = hashlib.sha256(
            self.transport_session_id.encode()
        ).hexdigest()
        self._connection_stage = "authenticated"
        self._reader_task = asyncio.create_task(
            self._async_reader(),
            name=f"worlty-core-{device_id}",
        )
        return device_id

    async def async_disconnect(self) -> None:
        """Close the session and fail each pending request exactly once."""
        websocket = self._websocket
        self._websocket = None
        self._verified_spki_sha256 = ""
        self.granted_scopes = ()
        self.granted_scope_mask = 0
        self.transport_session_id = ""
        self.session_id_hash = ""
        if websocket is not None:
            await websocket.close()
        task = self._reader_task
        self._reader_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._fail_pending(WorltyConnectionError("Worlty Local API disconnected"))

    async def async_wait_closed(self, *, timeout: float | None = None) -> bool:
        """Wait for reader exit without cancelling the reader on timeout."""
        task = self._reader_task
        if task is None:
            return True
        if timeout is None:
            await task
            return True
        done, _pending = await asyncio.wait((task,), timeout=max(0.0, timeout))
        return bool(done)

    async def async_subscribe_device(
        self, request_id: str, *, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Wait for a complete automatic baseline through the existing reader."""
        return await self._async_device_transfer(
            {"t": "subscribe", "rid": request_id, "domains": [1]}, timeout=timeout
        )

    async def async_query_device(
        self, request_id: str, *, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Recover with an explicit full query without re-registering."""
        self._device_queries.add(request_id)
        try:
            return await self._async_device_transfer(
                {
                    "t": "platform_request",
                    "rid": request_id,
                    "contract": "PadDeviceSyncQuery",
                    "payload": {"sel": {"all": True, "f": True}},
                },
                timeout=timeout,
            )
        finally:
            self._device_queries.discard(request_id)

    async def _async_device_transfer(
        self, frame: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        request_id = frame["rid"]
        websocket = self._websocket
        if websocket is None or websocket.closed:
            raise WorltyConnectionError("Worlty Local API is not connected")
        if request_id in self._pending:
            raise WorltyRequestError("duplicate_request", request_id)
        if not self.granted_scope_mask & 1:
            raise WorltyAuthenticationError("Device observe scope is not granted")
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with asyncio.timeout(timeout):
                async with self._send_lock:
                    await websocket.send_json(frame)
                return await future
        except TimeoutError as err:
            raise WorltyRequestError("timeout", request_id) from err
        finally:
            self._pending.pop(request_id, None)

    async def async_platform_request(
        self,
        *,
        contract: str,
        payload: Mapping[str, Any],
        timeout: float,
        expected_contracts: frozenset[str],
        on_update: MessageHandler | None = None,
    ) -> dict[str, Any]:
        """Validate updates and await a final response while the request is active.

        Call on_update after state reception and finish waiting only if it returns
        True. Release callbacks on cancellation or completion. Progress updates do
        not extend the timeout.
        """
        request_id = f"ha-platform-{secrets.token_hex(8)}"
        frame = {
            "t": "platform_request",
            "rid": request_id,
            "contract": contract,
            "payload": dict(payload),
        }
        _device_envelope(frame, incoming=False, scope=self.granted_scope_mask)
        encoded_bytes = len(
            json.dumps(frame, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
        if encoded_bytes > LOCAL_MAX_MESSAGE_BYTES:
            raise WorltyRequestError("request_too_large", request_id)
        websocket = self._websocket
        if websocket is None or websocket.closed:
            raise WorltyConnectionError("Worlty Local API is not connected")
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        if on_update is not None:
            self._request_updates[request_id] = on_update
        try:
            async with asyncio.timeout(timeout):
                async with self._send_lock:
                    if self._websocket is not websocket or websocket.closed:
                        raise WorltyConnectionError("Worlty Local API session changed")
                    await websocket.send_json(frame)
                response = await future
        except ClientError as err:
            raise WorltyConnectionError(
                "Worlty Local API platform request failed"
            ) from err
        except TimeoutError as err:
            raise WorltyRequestError("timeout", request_id) from err
        finally:
            self._pending.pop(request_id, None)
            self._request_updates.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()
        if response.get("contract") not in expected_contracts:
            raise WorltyRequestError(
                "platform_contract_mismatch",
                str(response.get("contract", "")),
            )
        return response

    async def _async_reader(self) -> None:
        websocket = self._websocket
        if websocket is None:
            return
        failure: Exception = WorltyConnectionError("Worlty Local API closed")
        try:
            async for frame in websocket:
                if frame.type == WSMsgType.TEXT:
                    try:
                        message = json.loads(frame.data)
                        if not isinstance(message, dict):
                            raise DeviceApiValidationError("message must be an object")
                        if "t" not in message:
                            raise DeviceApiValidationError(
                                "raw MQTT Device API frames are not Local API frames"
                            )
                        self._record_received()
                        await self._async_handle_control(message)
                    except (
                        DeviceApiValidationError,
                        TypeError,
                        ValueError,
                        KeyError,
                    ) as err:
                        _LOGGER.warning("Rejected invalid PAD Local API frame: %s", err)
                        # Application extensions or malformed transfers must not close
                        # the authenticated transport.
                        # Unrelated requests must remain active too.
                        continue
                elif frame.type in {
                    WSMsgType.CLOSE,
                    WSMsgType.CLOSED,
                    WSMsgType.CLOSING,
                    WSMsgType.ERROR,
                }:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as err:  # The connection owner reports and reconnects.
            failure = WorltyConnectionError(str(err))
            _LOGGER.debug("Worlty Local API reader stopped (%s)", type(err).__name__)
        finally:
            await websocket.close()
            if self._websocket is websocket:
                self._websocket = None
            self._verified_spki_sha256 = ""
            self.granted_scopes = ()
            self.granted_scope_mask = 0
            self.transport_session_id = ""
            self.session_id_hash = ""
            self._fail_pending(failure)

    async def _async_handle_control(self, message: dict[str, Any]) -> None:
        if message.get("t") == "ping" and self._websocket is not None:
            if not isinstance(message.get("id"), str):
                return
            async with self._send_lock:
                await self._websocket.send_json({"t": "pong", "id": message["id"]})
        elif message.get("t") == "error":
            request_id = message.get("request_id")
            future = (
                self._pending.get(request_id) if isinstance(request_id, str) else None
            )
            if future is not None and not future.done():
                future.set_exception(
                    WorltyRequestError(str(message.get("code", "local_api_error")))
                )
            else:
                # Automatic subscriptions can report errors on the same RID after the
                # initial response.
                await self._message_handler(message)
        elif message.get("t") == "platform_update":
            contract = message.get("contract")
            if contract not in {"PadDeviceSyncPage", "PadDeviceControlResult"}:
                return
            _device_envelope(message, incoming=True, scope=self.granted_scope_mask)
            try:
                committed = await self._message_handler(message)
            except (TypeError, ValueError, KeyError, WorltyRequestError) as err:
                cause = err.__cause__
                # Only fixed validation messages defined by the receiver are safe to
                # expose.
                detail = (
                    str(cause) if type(cause) is ValueError else "validation failed"
                )
                if detail not in (
                    "Delta base epoch mismatch",
                    "Device collection must be an array",
                    "Device cursor is missing or repeated",
                    "Device epoch requires a full baseline",
                    "Device page collection count mismatch",
                    "Device record identity/revision is invalid",
                    "Device records exceed local memory capacity",
                    "Device removal collection is invalid",
                    "Device transfer collection counters are invalid",
                    "Device transfer completion is invalid",
                    "Device transfer count mismatch",
                    "Device transfer exceeds declared membership",
                    "Device transfer exceeds local byte capacity",
                    "Device transfer exceeds local record capacity",
                    "Device transfer identity changed before completion",
                    "Device transfer identity is invalid",
                    "Device transfer operation is not understood",
                    "Device transfer repeated a collection ID",
                    "Incomplete Device membership",
                ):
                    detail = "validation failed"
                _LOGGER.warning(
                    "Device application transfer rejected (%s; cause=%s: %s)",
                    type(err).__name__,
                    type(cause).__name__ if cause is not None else type(err).__name__,
                    detail[:160],
                )
                future = self._pending.get(message["rid"])
                if future is not None and not future.done():
                    future.set_exception(
                        WorltyRequestError("invalid_device_transfer", message["rid"])
                    )
                return
            request_id = message.get("rid")
            future = (
                self._pending.get(request_id) if isinstance(request_id, str) else None
            )
            on_update = self._request_updates.get(request_id)
            if future is not None and not future.done() and on_update is not None:
                try:
                    committed = await on_update(message)
                except (TypeError, ValueError, KeyError, WorltyRequestError) as err:
                    if not future.done():
                        future.set_exception(err)
                    return
                if committed is not True:
                    return
            if (
                contract == "PadDeviceSyncPage"
                and committed is False
                and request_id in self._device_queries
                and future is not None
                and not future.done()
                and self._websocket is not None
            ):
                async with self._send_lock:
                    await self._websocket.send_json(
                        {
                            "t": "platform_request",
                            "rid": request_id,
                            "contract": "PadDeviceSyncQuery",
                            "payload": {"cur": message["payload"]["nc"]},
                        }
                    )
            if (
                future is not None
                and not future.done()
                and (contract != "PadDeviceSyncPage" or committed is True)
                and (
                    contract != "PadDeviceControlResult"
                    or message["payload"].get("rsn") != "accepted"
                )
            ):
                future.set_result(message)

    def _fail_pending(self, error: Exception) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
