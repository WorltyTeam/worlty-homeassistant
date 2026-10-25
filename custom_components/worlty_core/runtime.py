"""Runtime reflecting authenticated hub Device streams in Home Assistant."""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
import secrets
from collections.abc import Callable, Coroutine
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import (
    CONF_AUTH_SCOPES,
    CONF_BINDING_GENERATION,
    CONF_CLIENT_ID,
    CONF_DEVICE_ID,
    CONF_HA_INSTANCE_ID,
    CONF_TLS_HOSTNAME,
    CONF_TLS_SPKI_SHA256,
    CONF_TLS_TARGET_ID,
    DOMAIN,
    LOCAL_API_SCOPES,
)
from .core_state import CoreStateError, restore_stored_state
from .device_sync import DeviceSyncReceiver
from .errors import (
    WorltyAuthenticationError,
    WorltyConnectionError,
    WorltyIdentityMismatchError,
    WorltyPinMismatchError,
    WorltyRequestError,
)
from .identity import WorltyClientIdentity, async_get_client_identity
from .input import input_capabilities
from .model import WorltyModel, WorltyModelChange, WorltyResource, projected_entity_ids
from .naming import child_device_name, configured_pad_name
from .protocol import MessageHandler, WorltyLocalClient
from .session import PadSession

if TYPE_CHECKING:
    from .coordinator import WorltyCoreCoordinator

_LOGGER = logging.getLogger(__name__)
ModelListener = Callable[[WorltyModelChange], None]
_STORE_VERSION = 1


class WorltyRuntime:
    """Owner of one hub's local session, Device state, and HA representation."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize hub connection, model, and HA registration state."""
        self.hass = hass
        self.entry = entry
        self.coordinator: WorltyCoreCoordinator | None = None
        self.model = WorltyModel()
        self.connected = False
        self.identity: WorltyClientIdentity | None = None
        self.pad_session: PadSession | None = None
        self._client: WorltyLocalClient | None = None
        self._connection_task: asyncio.Task[None] | None = None
        self._owned_tasks: set[asyncio.Task[Any]] = set()
        self._stop = asyncio.Event()
        self._listeners: set[ModelListener] = set()
        self._first_connection: asyncio.Future[None] | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass,
            _STORE_VERSION,
            f"{DOMAIN}.{entry.entry_id}.core_state",
            private=True,
        )
        self._device_sync = DeviceSyncReceiver(self.model)
        self._device_subscription_rid: str | None = None
        self._device_query_rid: str | None = None
        self._device_resync_used = False
        self._device_sync_ready = False
        self._device_resync_task: asyncio.Task[Any] | None = None
        self._last_connection_error: dict[str, Any] | None = None
        self._last_connection_error_stage: str | None = None
        self._last_connection_error_at: str | None = None

    def connection_diagnostics(self) -> dict[str, Any]:
        """Keep connection observations outside shared state and wire contracts."""
        transport = (
            self._client.connection_diagnostics() if self._client is not None else {}
        )
        return {
            "last_error": (
                dict(self._last_connection_error)
                if self._last_connection_error is not None
                else None
            ),
            "last_error_stage": self._last_connection_error_stage,
            "last_error_at": self._last_connection_error_at,
            "last_received_at": transport.get("last_received_at"),
        }

    def _record_connection_failure(self, stage: str, error: Exception) -> None:
        """Store bounded codes rather than arbitrary exception or hub text."""
        cause = error.__cause__ or error
        detail: dict[str, Any] = {"type": type(error).__name__[:64]}
        if isinstance(error, WorltyPinMismatchError):
            code = "pin_mismatch"
        elif isinstance(error, WorltyIdentityMismatchError):
            code = "identity_mismatch"
        elif isinstance(error, WorltyAuthenticationError):
            code = "authentication_failed"
        elif isinstance(cause, TimeoutError) or (
            isinstance(error, WorltyRequestError) and error.code == "timeout"
        ):
            code = "timeout"
        elif isinstance(error, WorltyRequestError):
            code = (
                error.code
                if error.code in {"invalid_device_transfer", "not_authorized"}
                or re.fullmatch(r"[0-9]{1,5}", error.code)
                else "request_failed"
            )
        elif stage == "device_sync" and isinstance(
            error, (ValueError, TypeError, KeyError)
        ):
            code = "invalid_device_transfer"
        else:
            code = "connection_closed" if stage == "stream" else "connection_failed"
        if isinstance(error, WorltyConnectionError):
            frame = re.fullmatch(
                r"unexpected WebSocket frame: ([0-9]{1,5})", str(error)
            )
            if frame is not None:
                code = "unexpected_websocket_frame"
                detail["frame_type"] = int(frame[1])
        detail["code"] = code
        if cause is not error:
            detail["cause_type"] = type(cause).__name__[:64]
        self._last_connection_error = detail
        self._last_connection_error_stage = stage
        self._last_connection_error_at = None
        with suppress(OSError, OverflowError, ValueError):
            self._last_connection_error_at = datetime.now(UTC).isoformat(
                timespec="seconds"
            )

    async def async_start(self) -> None:
        """Validate stored identity and start one authenticated Device connection."""
        await self._async_load_state()
        configured_client_id = self.entry.data.get(CONF_CLIENT_ID)
        configured_ha_instance_id = self.entry.data.get(CONF_HA_INSTANCE_ID)
        identity = await async_get_client_identity(
            self.hass,
            client_id=(
                configured_client_id if isinstance(configured_client_id, str) else None
            ),
        )
        if (
            configured_client_id != identity.client_id
            or configured_ha_instance_id != identity.ha_instance_id
        ):
            raise WorltyAuthenticationError(
                "stored pairing principal does not match the HA client identity"
            )
        configured_scopes = self._configured_auth_scopes()
        binding_generation = self.entry.data.get(CONF_BINDING_GENERATION)
        if (
            not isinstance(binding_generation, str)
            or not re.fullmatch(r"[0-9a-f]{16}", binding_generation)
            or binding_generation == "0000000000000000"
        ):
            raise WorltyAuthenticationError(
                "stored binding generation is invalid; explicit registration is required"
            )
        self.identity = identity
        session = async_get_clientsession(self.hass)
        self._client = WorltyLocalClient(
            session,
            host=str(self.entry.data[CONF_HOST]),
            port=int(self.entry.data[CONF_PORT]),
            tls_hostname=str(self.entry.data[CONF_TLS_HOSTNAME]),
            expected_pin=str(self.entry.data[CONF_TLS_SPKI_SHA256]),
            expected_device_id=str(self.entry.data[CONF_DEVICE_ID]),
            binding_generation=binding_generation,
            expected_target_id=str(self.entry.data[CONF_TLS_TARGET_ID]),
            identity=identity,
            message_handler=self._async_handle_message,
            requested_scopes=configured_scopes,
        )
        if self.coordinator is None:
            raise WorltyAuthenticationError("coordinator is not registered")
        first_connection = self._first_connection = (
            asyncio.get_running_loop().create_future()
        )
        self._connection_task = self.hass.async_create_background_task(
            self._async_connection_loop(),
            f"worlty-core-{self.entry.entry_id}",
        )
        try:
            done, _pending = await asyncio.wait(
                {first_connection},
                timeout=20.0,
            )
            if not done:
                _LOGGER.warning(
                    "설정 중 월티 허브에 연결할 수 없습니다. "
                    "인증된 HA 식별 정보와 로컬 투영 상태를 유지합니다"
                )
                return
            await first_connection
        except Exception:
            await self.async_stop()
            raise
        finally:
            self._first_connection = None
            if not first_connection.done():
                first_connection.cancel()
            elif not first_connection.cancelled():
                # Consume late authentication errors when no caller is left waiting.
                first_connection.exception()

    async def async_stop(self) -> None:
        """Stop reconnect tasks, listeners, and the local session."""
        self._stop.set()
        if self._first_connection is not None and not self._first_connection.done():
            self._first_connection.cancel()
        task = self._connection_task
        self._connection_task = None
        owned_tasks = tuple(self._owned_tasks)
        tasks = tuple(
            candidate
            for candidate in (task, *owned_tasks)
            if candidate is not None and candidate is not asyncio.current_task()
        )
        for candidate in tasks:
            candidate.cancel()
        try:
            if self._client is not None:
                await self._client.async_disconnect()
        finally:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._owned_tasks.clear()
            self._set_connected(False)

    def _create_owned_task(
        self,
        target: Coroutine[Any, Any, Any],
        name: str,
    ) -> asyncio.Task[Any]:
        """Track entry tasks so unloading can cancel their side effects."""
        task = self.hass.async_create_task(target, name)
        self._owned_tasks.add(task)
        task.add_done_callback(self._owned_tasks.discard)
        return task

    def add_model_listener(self, listener: ModelListener) -> Callable[[], None]:
        """Subscribe to catalog and state changes."""
        self._listeners.add(listener)

        def unsubscribe() -> None:
            self._listeners.discard(listener)

        return unsubscribe

    def resources_for_platform(self, platform: str) -> list[WorltyResource]:
        """Return visible resources for the requested HA platform."""
        return self.model.resources_for_platform(platform)

    @property
    def device_epoch(self) -> str | None:
        """Return the epoch; consumers discard old event cursors when it changes."""
        return self._device_sync.epoch

    async def async_platform_request(
        self,
        *,
        contract: str,
        payload: dict[str, Any],
        expected_contracts: frozenset[str],
        on_update: MessageHandler | None = None,
    ) -> dict[str, Any]:
        """Send the generated platform contract through the active session."""
        client = self._client
        if client is None or not client.connected:
            raise WorltyConnectionError("PAD is not connected")
        return await client.async_platform_request(
            contract=contract,
            payload=payload,
            timeout=8,
            expected_contracts=expected_contracts,
            on_update=on_update,
        )

    async def async_set_state(self, resource_id: str, state: dict[str, Any]) -> None:
        """Preserve the entity write batch and per-item RIDs until confirmation."""
        self._require_scope("control")
        resource = self.model.entities.get(resource_id)
        if not self.connected or resource is None:
            raise WorltyRequestError("resource_unavailable", resource_id)
        try:
            cp = self._device_sync.control_cp(resource, state)
        except ValueError as err:
            raise WorltyRequestError("invalid_control", str(err)) from err
        await self._async_confirm_control(resource_id, cp)

    async def async_set_input_value(
        self, resource_id: str, capability_id: int, value: Any
    ) -> None:
        """Use the current descriptor and the confirmed Device control path."""
        self._require_scope("control")
        resource = self.model.entities.get(resource_id)
        if (
            not self.connected
            or resource is None
            or not self.model.resource_available(resource)
        ):
            raise WorltyRequestError("resource_unavailable", resource_id)
        field = input_capabilities(resource).get(capability_id)
        if field is None:
            raise WorltyRequestError("invalid_control", "Unknown input capability")
        try:
            wire_value = field.encode(value, self.hass.config.language)
        except ValueError as err:
            raise WorltyRequestError("invalid_control", str(err)) from err
        key = str(capability_id)
        if field.value_type == 9:
            await self._async_confirm_control(
                resource_id, {}, {key: wire_value}, require_readback=field.readable
            )
        else:
            await self._async_confirm_control(
                resource_id, {key: wire_value}, require_readback=field.readable
            )

    async def _async_confirm_control(
        self,
        resource_id: str,
        cp: dict[str, int | float],
        tx: dict[str, str] | None = None,
        *,
        require_readback: bool = True,
    ) -> None:
        """Confirm accepted batches, requiring readback only for readable fields."""
        item_rid = f"ha-item-{secrets.token_hex(12)}"
        epoch = self._device_sync.epoch
        item = {"id": resource_id, "rid": item_rid}
        if cp:
            item["cp"] = cp
        if tx:
            item["tx"] = tx

        def correlated_data(message):
            result = message.get("payload")
            data = result.get("d") if isinstance(result, dict) else None
            state = data.get("st") if isinstance(data, dict) else None
            rows = state.get("ent") if isinstance(state, dict) else None
            if (
                not isinstance(rows, list)
                or len(rows) != 1
                or not isinstance(rows[0], dict)
                or data.get("ep") != epoch
                or epoch != self._device_sync.epoch
                or rows[0].get("id") != resource_id
                or rows[0].get("rid") != item_rid
            ):
                return None
            return data

        async def on_update(message):
            if message.get("contract") != "PadDeviceControlResult":
                return False
            result = message["payload"]
            if "err" in result:
                return True
            data = correlated_data(message)
            if data is None:
                return False
            return result.get("rsn") != "accepted"

        response = await self.async_platform_request(
            contract="PadDeviceControl",
            payload={"op": 131075, "it": [item]},
            expected_contracts=frozenset({"PadDeviceControlResult"}),
            on_update=on_update,
        )
        result = response["payload"]
        if not isinstance(result, dict):
            raise WorltyRequestError("control_result_mismatch", item_rid)
        if "err" in result:
            error = result["err"]
            code = (
                str(error.get("code", "rejected"))
                if isinstance(error, dict)
                else "rejected"
            )
            raise WorltyRequestError(code, item_rid)
        reason = result.get("rsn")
        resource = self.model.entities.get(resource_id)
        call = (
            resource is not None
            and resource.entity_type == 4
            and resource.entity_class == 6
            and cp == {"1": 1}
            and not tx
        )
        failed_call = call and reason in {"sent_unconfirmed", "failed"}
        if reason not in {"confirmed", "confirmed_noop"} and not failed_call:
            raise WorltyRequestError("sent_unconfirmed", item_rid)
        data = correlated_data(response)
        state = data.get("st") if isinstance(data, dict) else None
        rows = state.get("ent") if isinstance(state, dict) else None
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
        ):
            raise WorltyRequestError("control_result_mismatch", item_rid)
        row = rows[0]
        # New calls, RX, or arrivals can change CALL display state after a terminal
        # result.
        # The PAD result confirms physical action; current POWER is display state.
        expected_cp = cp if require_readback and not call else {}
        expected_tx = (tx or {}) if require_readback else {}
        if (
            data.get("ep") != self._device_sync.epoch
            or row.get("id") != resource_id
            or row.get("rid") != item_rid
            or (
                call
                and (
                    not isinstance(row.get("st"), dict)
                    or type(row["st"].get("1")) is not int
                    or row["st"]["1"] not in (0, 1)
                )
            )
            or (expected_cp and not isinstance(row.get("st"), dict))
            or any(
                row.get("st", {}).get(key) != value
                for key, value in expected_cp.items()
            )
            or (expected_tx and not isinstance(row.get("tx"), dict))
            or any(
                row.get("tx", {}).get(key) != value
                for key, value in expected_tx.items()
            )
        ):
            raise WorltyRequestError("control_result_mismatch", item_rid)
        if failed_call:
            raise WorltyRequestError(reason, item_rid)

    async def _async_connection_loop(self) -> None:
        delay = 1
        while not self._stop.is_set():
            stage = "tls_wss"
            try:
                assert self._client is not None
                expected_pin = str(self.entry.data[CONF_TLS_SPKI_SHA256])
                self._client.set_expected_pin(expected_pin)
                await self._client.async_connect()
                stage = "pin_verification"
                if not hmac.compare_digest(
                    self._client.verified_spki_sha256,
                    expected_pin,
                ):
                    raise WorltyConnectionError(
                        "verified PAD pin does not match the paired identity"
                    )
                stage = "device_sync"
                await self._async_full_device_sync()
                self._set_connected(self._device_sync_ready)
                if (
                    self._first_connection is not None
                    and not self._first_connection.done()
                ):
                    self._first_connection.set_result(None)
                delay = 1
                stage = "stream"
                await self._client.async_wait_closed()
                if not self._stop.is_set():
                    self._record_connection_failure(
                        stage, WorltyConnectionError("Local channel closed")
                    )
            except asyncio.CancelledError:
                raise
            except WorltyAuthenticationError as err:
                self._record_connection_failure(
                    self._client.connection_diagnostics()["stage"]
                    if stage == "tls_wss" and self._client is not None
                    else stage,
                    err,
                )
                self._set_connected(False)
                if (
                    self._first_connection is not None
                    and not self._first_connection.done()
                ):
                    self._first_connection.set_exception(err)
                    return
                _LOGGER.warning(
                    "설정 후 월티 허브가 인증을 거절했습니다: %s",
                    type(err).__name__,
                )
                self.entry.async_start_reauth(self.hass)
                return
            except Exception as err:
                self._record_connection_failure(
                    self._client.connection_diagnostics()["stage"]
                    if stage == "tls_wss" and self._client is not None
                    else stage,
                    err,
                )
                self._set_connected(False)
                if (
                    self._first_connection is not None
                    and not self._first_connection.done()
                ):
                    self._first_connection.set_result(None)
                _LOGGER.warning(
                    "월티 허브 연결이 중단됐습니다 (%s): %s", stage, type(err).__name__
                )
            finally:
                self._device_sync.abort()
                self._device_subscription_rid = None
                self._device_query_rid = None
                self._device_sync_ready = False
                if self._device_resync_task is not None:
                    self._device_resync_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._device_resync_task
                    self._device_resync_task = None
                self._set_connected(False)
                if self._client is not None:
                    await self._client.async_disconnect()
            if not self._stop.is_set():
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def _async_full_device_sync(self) -> None:
        self._require_scope("observe")
        client = self._client
        if client is None or not client.connected:
            raise WorltyConnectionError("PAD is not connected")
        self._device_sync.abort()
        self._device_sync_ready = False
        self._device_resync_used = False
        self._device_subscription_rid = f"ha-sync-{secrets.token_hex(12)}"
        try:
            await client.async_subscribe_device(self._device_subscription_rid)
        except WorltyRequestError:
            await self._async_device_resync()
        except BaseException:
            self._device_sync.abort()
            self._device_subscription_rid = None
            raise

    async def _async_device_resync(self) -> None:
        client = self._client
        if (
            self._device_resync_used
            or client is None
            or not client.connected
            or self._stop.is_set()
        ):
            return
        self._device_resync_used = True
        self._device_sync.abort()
        self._device_query_rid = f"ha-resync-{secrets.token_hex(12)}"
        try:
            await client.async_query_device(self._device_query_rid)
        except (WorltyRequestError, WorltyConnectionError) as err:
            self._record_connection_failure("device_sync", err)
            _LOGGER.warning(
                "Device resync paused; authenticated channel retained: %s",
                type(err).__name__,
            )
        finally:
            self._device_query_rid = None
            self._device_sync.abort()

    async def _async_handle_message(self, message: dict[str, Any]) -> bool | None:
        if message.get("t") == "error":
            if (
                self._device_subscription_rid is not None
                and message.get("request_id") == self._device_subscription_rid
            ):
                previous = self._last_connection_error
                previous_stage = self._last_connection_error_stage
                self._record_connection_failure(
                    "device_sync",
                    WorltyRequestError(str(message.get("code", "local_api_error"))),
                )
                if isinstance(message.get("retryable"), bool):
                    self._last_connection_error["retryable"] = message["retryable"]
                if (
                    previous_stage != "device_sync"
                    or previous != self._last_connection_error
                ):
                    _LOGGER.warning(
                        "월티 허브의 상태 동기화 중 오류가 발생했습니다 (코드: %s)",
                        self._last_connection_error["code"],
                    )
            return None
        if message.get("t") != "platform_update":
            return None
        if message.get("contract") == "PadDeviceSyncPage":
            expected = self._device_query_rid or self._device_subscription_rid
            if message.get("rid") != expected:
                return None
            try:
                change = self._device_sync.accept(message["payload"])
            except (ValueError, TypeError, KeyError) as err:
                self._record_connection_failure("device_sync", err)
                if (
                    self._device_sync_ready
                    and not self._device_resync_used
                    and (
                        self._device_resync_task is None
                        or self._device_resync_task.done()
                    )
                ):
                    self._device_resync_task = self._create_owned_task(
                        self._async_device_resync(), f"{DOMAIN}-device-resync"
                    )
                raise
            if change is not None:
                self._device_sync_ready = True
                await self._async_publish_device_change(
                    change, full=message["payload"]["sm"] == 1
                )
                if (
                    not self._stop.is_set()
                    and self._client is not None
                    and self._client.connected
                ):
                    self._set_connected(True)
                return True
            return False
        if message.get("contract") == "PadDeviceControlResult":
            result = message.get("payload")
            if not isinstance(result, dict) or result.get("rsn") not in {
                "accepted",
                "confirmed",
                "confirmed_noop",
                "sent_unconfirmed",
                "failed",
            }:
                return None
            data = result.get("d")
            if not isinstance(data, dict) or data.get("ep") != self._device_sync.epoch:
                return None
            # State updates outlive RID waits, including cancellation and timeout.
            # Apply late observations using epoch and field RV rules without completing
            # requests again.
            await self._async_publish_device_change(
                self._device_sync.control_result(data)
            )
        return None

    async def _async_publish_device_change(
        self, change: WorltyModelChange, *, full: bool = False
    ) -> None:
        removed_device_ids = change.removed_devices
        try:
            # Check current display targets when the listener performs removal.
            # Early registry removal can lose user settings for an entity restored
            # immediately afterward.
            if (
                full
                or change.removed
                or change.retyped
                or change.deactivated
                or any(
                    (resource := self.model.entities.get(resource_id)) is not None
                    and resource.entity_type == 7
                    for resource_id in change.updated
                )
            ):
                self._create_owned_task(
                    self._async_remove_registry_entities(change, full=full),
                    f"{DOMAIN}-prune-entities",
                )
            if full:
                prefix = f"{self.entry.data[CONF_DEVICE_ID]}:"
                stale_device_ids = set()
                for entry in dr.async_entries_for_config_entry(
                    dr.async_get(self.hass), self.entry.entry_id
                ):
                    for namespace, identifier in entry.identifiers:
                        if namespace != DOMAIN or not identifier.startswith(prefix):
                            continue
                        device_id = identifier[len(prefix) :]
                        try:
                            if str(UUID(device_id)) != device_id:
                                continue
                        except ValueError:
                            continue
                        if device_id not in self.model.devices:
                            stale_device_ids.add(device_id)
                removed_device_ids |= frozenset(stale_device_ids)
            if change.all_ids or change.all_device_ids:
                self._sync_device_registry_metadata(change)
        except Exception:
            _LOGGER.exception(
                "Worlty registry presentation failed; Device records remain committed"
            )
        if change.all_ids:
            self._notify(change)
        if removed_device_ids:
            self._create_owned_task(
                self._async_prune_removed_devices(removed_device_ids),
                f"{DOMAIN}-prune-devices",
            )

    def _set_connected(self, connected: bool) -> None:
        if self.connected == connected:
            return
        self.connected = connected
        self._notify(WorltyModelChange(updated=frozenset(self.model.entities)))

    def _notify(self, change: WorltyModelChange) -> None:
        for listener in tuple(self._listeners):
            try:
                listener(change)
            except Exception:
                _LOGGER.exception(
                    "Worlty entity presentation failed; continuing other listeners"
                )

    def _require_scope(self, scope: str) -> None:
        if not self._has_scope(scope):
            raise WorltyRequestError("not_authorized", scope)

    def _has_scope(self, scope: str) -> bool:
        client = self._client
        return (
            client is not None and client.connected and scope in client.granted_scopes
        )

    def _configured_auth_scopes(self) -> tuple[str, ...]:
        value = self.entry.data.get(CONF_AUTH_SCOPES)
        if not isinstance(value, list) or any(
            not isinstance(scope, str) for scope in value
        ):
            raise WorltyAuthenticationError("stored pairing scopes are invalid")
        configured = set(value)
        if (
            not configured
            or "observe" not in configured
            or len(configured) != len(value)
            or not configured.issubset(LOCAL_API_SCOPES)
        ):
            raise WorltyAuthenticationError("stored pairing scopes are invalid")
        return tuple(scope for scope in LOCAL_API_SCOPES if scope in configured)

    def _sync_device_registry_metadata(self, change: WorltyModelChange) -> None:
        """Update metadata only for devices owned by this Worlty Core entry."""
        pad_id = str(self.entry.data[CONF_DEVICE_ID])
        device_registry = dr.async_get(self.hass)
        parent = device_registry.async_get_device_by_identifier(
            (DOMAIN, pad_id), self.entry.entry_id
        )
        pad_name = (
            (parent.name_by_user or parent.name) if parent is not None else None
        ) or configured_pad_name(self.entry.data, title=self.entry.title, pad_id=pad_id)
        for device_id in change.updated_devices:
            metadata = self.model.devices.get(device_id)
            if metadata is None:
                continue
            entry = device_registry.async_get_device_by_identifier(
                (DOMAIN, f"{pad_id}:{device_id}"), self.entry.entry_id
            )
            if entry is None or entry.config_entry_id != self.entry.entry_id:
                continue
            source = (
                metadata.get("source")
                if isinstance(metadata.get("source"), dict)
                else {}
            )
            name = metadata.get("name")
            manufacturer = source.get("manufacturer")
            model = source.get("model")
            updates: dict[str, Any] = {}
            if isinstance(name, str) and name:
                own_identifier = (DOMAIN, f"{pad_id}:{device_id}")
                existing_names = {
                    candidate.name
                    for candidate in device_registry.devices
                    if candidate.name
                    and candidate.id != entry.id
                    and own_identifier not in candidate.identifiers
                }
                display_name = child_device_name(
                    pad_name,
                    name,
                    pad_id,
                    existing_names=existing_names,
                )
                if entry.name != display_name:
                    updates["name"] = display_name
            if (
                isinstance(manufacturer, str)
                and manufacturer
                and entry.manufacturer != manufacturer
            ):
                updates["manufacturer"] = manufacturer
            if isinstance(model, str) and model and entry.model != model:
                updates["model"] = model
            if updates:
                device_registry.async_update_device(entry.id, **updates)

    async def _async_prune_removed_devices(
        self,
        device_ids: frozenset[str],
    ) -> None:
        """Remove only unlinked registry devices owned by this config entry."""
        await asyncio.sleep(0)
        pad_id = str(self.entry.data[CONF_DEVICE_ID])
        device_registry = dr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        live_identifiers = {(DOMAIN, pad_id)} | {
            (DOMAIN, f"{pad_id}:{device_id}") for device_id in self.model.devices
        }
        for device_id in device_ids:
            if device_id in self.model.devices:
                continue
            entry = device_registry.async_get_device_by_identifier(
                (DOMAIN, f"{pad_id}:{device_id}"), self.entry.entry_id
            )
            if entry is None or entry.config_entry_id != self.entry.entry_id:
                continue
            if entry.identifiers & live_identifiers:
                continue
            if er.async_entries_for_device(
                entity_registry,
                entry.id,
                include_disabled_entities=True,
            ):
                continue
            if entry.identifiers == {(DOMAIN, f"{pad_id}:{device_id}")}:
                device_registry.async_remove_device(entry.id)

    async def _async_remove_registry_entities(
        self,
        change: WorltyModelChange,
        *,
        full: bool = False,
    ) -> None:
        """Remove obsolete registry entities against the fully committed model."""
        # Defer removal beyond the receive callback even with HA eager tasks.
        await asyncio.sleep(0)
        resource_ids = change.removed | change.retyped | change.deactivated
        # Input fields can disappear or change platform while their parent remains tp=7.
        # Disabled HA entities have no listeners, so also reconcile the registry
        # directly.
        resource_ids |= {
            resource_id
            for resource_id in change.updated
            if (resource := self.model.entities.get(resource_id)) is not None
            and resource.entity_type == 7
        }
        if not full and not resource_ids:
            return
        pad_id = str(self.entry.data[CONF_DEVICE_ID])
        entity_registry = er.async_get(self.hass)
        owned_entries = tuple(
            entry
            for entry in er.async_entries_for_config_entry(
                entity_registry, self.entry.entry_id
            )
            if entry.platform == DOMAIN
        )
        prefix = f"{pad_id}:"
        for entry in owned_entries:
            if not entry.unique_id.startswith(prefix):
                continue
            resource_id, separator, raw_capability = entry.unique_id[
                len(prefix) :
            ].partition(":cp:")
            try:
                if str(UUID(resource_id)) != resource_id:
                    continue
                capability_id = int(raw_capability) if separator else None
                if separator and (
                    str(capability_id) != raw_capability
                    or not 0 < capability_id <= 65535
                ):
                    continue
            except ValueError:
                continue
            if not full and resource_id not in resource_ids:
                continue
            resource = self.model.entities.get(resource_id)
            platform = (
                projected_entity_ids(pad_id, resource).get(entry.unique_id)
                if resource is not None
                else None
            )
            if platform != entry.entity_id.partition(".")[0]:
                entity_registry.async_remove(entry.entity_id)

    async def _async_load_state(self) -> None:
        """Preserve inactive stored values without interpreting them or issuing IDs."""
        try:
            restore_stored_state(await self._store.async_load())
        except CoreStateError as err:
            raise WorltyAuthenticationError("stored Core state is corrupt") from err
