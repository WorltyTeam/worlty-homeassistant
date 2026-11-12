"""Provide the Worlty integration API."""

import asyncio
import datetime
import json
import random
import re
import time
from collections import defaultdict
from contextlib import suppress
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.components.climate import ClimateEntityDescription
from homeassistant.components.date import DateEntityDescription
from homeassistant.components.fan import FanEntityDescription
from homeassistant.components.light import LightEntityDescription
from homeassistant.components.number import NumberEntityDescription
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.switch import SwitchEntityDescription
from homeassistant.components.time import TimeEntityDescription
from homeassistant.components.water_heater import WaterHeaterEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityCategory, generate_entity_id

from ..const import CONF_V1_MODE, V1_MODE_ADVANCED, V1_MODE_BASIC
from .const import (
    DOMAIN,
    LOGGER,
    MANUFACTURER,
    WorltyBaseType,
    get_worlty_description,
    is_advanced_entity,
    map_worlty_state,
    map_worlty_sub,
    map_worlty_to_platform,
)
from .transport import (
    CONNECT_TIMEOUT,
    MAX_MESSAGE_BYTES,
    SETUP_TIMEOUT,
    WorltyV1AuthenticationError,
    WorltyV1ConnectionError,
    async_authenticate,
    async_close_writer,
)

BACKOFF_BASE = 1.0
BACKOFF_CAP = 30.0
AUTH_BACKOFF_BASE = 5.0
AUTH_BACKOFF_CAP = 300.0
HEALTH_IDLE_SECONDS = 90
INITIAL_DATA_TIMEOUT = 15
HEALTH_PROBE_TIMEOUT = 5


def _reported_name(value: Any) -> str | None:
    """Remove nonprintable characters and limit the name to 128 characters."""
    if not isinstance(value, str):
        return None
    return (
        "".join(character for character in value if character.isprintable()).strip()[
            :128
        ]
        or None
    )


def _build_worlty_entity_description(
    entity_description_cls,
    key: str,
    worlty_sub: str,
    *,
    device_class=None,
    icon: str | None = None,
    unit_of_measurement=None,
    use_translation: bool = True,
):
    """Build the description selected for a supported classic pad entity."""
    description_kwargs: dict[str, Any] = {
        "key": key,
        "has_entity_name": True,
    }
    if icon is not None:
        description_kwargs["icon"] = icon
    if device_class is not None:
        description_kwargs["device_class"] = device_class
    if unit_of_measurement is not None:
        description_kwargs["native_unit_of_measurement"] = unit_of_measurement
    if use_translation:
        description_kwargs["translation_key"] = key
        description_kwargs["translation_placeholders"] = {"sub_id": worlty_sub}
    return entity_description_cls(**description_kwargs)


def map_worlty_entity_description(
    worlty_type: int, worlty_class: int, worlty_sub: str
) -> tuple:
    """Return the HA entity description and name key for the device type."""
    key, device_class, icon, unit_of_measurement = get_worlty_description(
        worlty_type, worlty_class
    )
    platform_type = map_worlty_to_platform(worlty_type, worlty_class)
    entity_description_cls = {
        Platform.BINARY_SENSOR: BinarySensorEntityDescription,
        Platform.CLIMATE: ClimateEntityDescription,
        Platform.FAN: FanEntityDescription,
        Platform.NUMBER: NumberEntityDescription,
        Platform.DATE: DateEntityDescription,
        Platform.TIME: TimeEntityDescription,
        Platform.LIGHT: LightEntityDescription,
        Platform.SENSOR: SensorEntityDescription,
        Platform.SWITCH: SwitchEntityDescription,
        Platform.WATER_HEATER: WaterHeaterEntityDescription,
    }.get(platform_type)

    if entity_description_cls is None:
        return None, "unknown"

    if key is None:
        return (
            _build_worlty_entity_description(
                entity_description_cls,
                "unknown",
                worlty_sub,
                use_translation=False,
            ),
            "unknown",
        )

    return (
        _build_worlty_entity_description(
            entity_description_cls,
            key,
            worlty_sub,
            device_class=device_class,
            icon=icon,
            unit_of_measurement=unit_of_measurement,
        ),
        key,
    )


class WorltyLocal:
    """Manage the Worlty local API."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        access_token: str,
    ) -> None:
        """Initialize TCP state, device caches, and the command queue."""
        self.hass = hass
        self._entry: ConfigEntry | None = None
        self._host: str = host
        self._port: int = port
        self._access_token: str = access_token
        self._subscribe = None
        self._publish = None
        self._disconnect = False
        self._connected = False
        self._reconnect_lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task | None = None
        self._health = datetime.datetime.now()
        self._received_device_data = False
        self._received_device_data_count = 0
        self._session_generation = 0
        self._connection_issue_counts: dict[str, int] = {}
        self._last_connection_issue: dict[str, Any] | None = None
        self._last_issue_generation: int | None = None
        self._last_device_received_monotonic: float | None = None
        self._reconnect_attempts = 0
        self._reconnect_successes = 0
        self._authentication_rejections = 0
        self._last_reconnected_at: str | None = None
        self._health_probe_attempts = 0
        self._health_probe_responses = 0
        self._last_health_probe: dict[str, Any] | None = None
        self._timings: dict[str, float] = {}
        self._queue: list[dict[str, Any]] = []
        self._message_tasks: set[asyncio.Task] = set()

        self._add_entity_listeners: dict[Platform, Any] = defaultdict()
        self._entity_map: dict[str, dict[str, Any]] = {}
        self._health_map: dict[str, int] = {}
        self._entities: dict[Platform, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.worlty_pad: WorltyBaseDevice | None = None
        self.worlty_entity: dict[str, WorltyBaseEntity] = {}
        self._pending_health: dict[int, dict[str, Any]] = {}
        self._pending_gets: dict[int, dict[str, Any]] = {}
        self._device_lock = asyncio.Lock()
        LOGGER.debug("클래식 패드 연결을 준비합니다")

    def _compute_backoff(self, attempt: int) -> float:
        """Increase the upper bound of the random delay with each retry."""
        expo = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** min(attempt, 5)))
        return random.uniform(0, expo)

    def _diagnostic_entry_id(self) -> str:
        """Identify the HA entry without exposing peer identity or credentials."""
        if self._entry is not None:
            entry_id = self._entry.entry_id
            if isinstance(entry_id, str) and re.fullmatch(
                r"[A-Za-z0-9_-]{1,128}", entry_id
            ):
                return entry_id
        return "unregistered"

    @staticmethod
    def _diagnostic_timestamp() -> str | None:
        """Return an optional UTC timestamp without affecting transport recovery."""
        with suppress(OSError, OverflowError, ValueError):
            return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
        return None

    def _record_connection_issue(
        self, reason: str, *, error: Exception | None = None
    ) -> None:
        """Record the first cause per session instead of secondary close effects."""
        if self._disconnect or self._last_issue_generation == self._session_generation:
            return
        self._last_issue_generation = self._session_generation
        self._connection_issue_counts[reason] = (
            self._connection_issue_counts.get(reason, 0) + 1
        )
        event = {
            "reason": reason,
            "at": self._diagnostic_timestamp(),
            "session_generation": self._session_generation,
            "last_received_age_seconds": (
                max(0.0, time.monotonic() - self._last_device_received_monotonic)
                if self._last_device_received_monotonic is not None
                else None
            ),
        }
        if error is not None:
            event["error_type"] = type(error).__name__[:64]
            if isinstance(error, OSError) and type(error.errno) is int:
                event["errno"] = error.errno
        self._last_connection_issue = event
        LOGGER.warning(
            "클래식 패드 %s 연결이 중단됐습니다 (사유: %s)",
            self._diagnostic_entry_id(),
            reason,
        )

    def _compute_auth_backoff(self, attempt: int) -> float:
        """Use longer retry delays for repeated authentication rejections."""
        expo = min(
            AUTH_BACKOFF_CAP,
            AUTH_BACKOFF_BASE * (2 ** min(attempt, 6)),
        )
        return random.uniform(expo / 2, expo)

    def _entry_data(self) -> dict[str, Any] | None:
        """Return runtime data for the active config entry."""
        if self._entry is None:
            return None
        return self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)

    def _schedule_reconnect(
        self, *, force: bool = False, health_guard: tuple[int, int, str] | None = None
    ) -> asyncio.Task | None:
        """Schedule a single shared reconnect task."""
        if self._disconnect or self.hass.is_stopping or self._entry is None:
            return None

        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self._entry.async_create_background_task(
                self.hass,
                self._run_reconnect(force=force, health_guard=health_guard),
                "Worlty classic reconnect",
            )
        return self._reconnect_task

    def _set_device_available(self, available: bool) -> None:
        """Update device availability and notify loaded entities."""
        if self.worlty_pad is None or self.worlty_pad.device_available == available:
            return

        self.worlty_pad.device_available = available
        for entity in tuple(self.worlty_entity.values()):
            if entity._loaded:
                entity._update_callback()

    def _schedule_queue_flush(self) -> None:
        """Schedule a single task to send queued commands."""
        if self._disconnect or self.hass.is_stopping or not self._queue:
            return

        entry_data = self._entry_data()
        if entry_data is None:
            return

        loop_task = entry_data.get("loop")
        if loop_task is None or loop_task.done():
            entry_data["loop"] = self._entry.async_create_task(
                self.hass, self.loop(), "Worlty classic commands"
            )

    async def _connect(self) -> bool:
        """Attempt one connection within a timeout; leave retries to the reconnect loop."""
        if self._disconnect:
            return False
        try:
            self._subscribe, self._publish = await asyncio.wait_for(
                asyncio.open_connection(
                    self._host, self._port, limit=MAX_MESSAGE_BYTES
                ),
                timeout=CONNECT_TIMEOUT,
            )
            self._connected = True
            return True
        except (TimeoutError, OSError):
            self._connected = False
            return False

    async def terminate(self) -> None:
        """Close the streams and wait for socket cleanup within a timeout."""
        self._connected = False
        self._queue.clear()
        self._pending_health.clear()
        self._pending_gets.clear()
        self._set_device_available(False)
        writer, self._publish = self._publish, None
        self._subscribe = None
        await async_close_writer(writer)

    async def disconnect(self):
        """Cancel tasks, clear commands and callbacks, and close the TCP connection."""
        self._disconnect = True
        entry_data = self._entry_data()
        current_task = asyncio.current_task()
        tasks = []
        if entry_data is not None:
            for task_name in ("reauth", "health", "task", "loop"):
                task = entry_data.get(task_name)
                if task is not None and task is not current_task and not task.done():
                    task.cancel()
                    tasks.append(task)

        reconnect_task = self._reconnect_task
        if (
            reconnect_task is not None
            and reconnect_task is not current_task
            and not reconnect_task.done()
        ):
            reconnect_task.cancel()
            tasks.append(reconnect_task)

        for task in tuple(self._message_tasks):
            if task is not current_task and not task.done():
                task.cancel()
                tasks.append(task)
        if tasks:
            await asyncio.gather(*set(tasks), return_exceptions=True)
        self._message_tasks.clear()
        self._pending_health.clear()
        self._add_entity_listeners.clear()
        self._queue.clear()

        if entry_data is not None:
            for task_name in ("reauth", "health", "task", "loop"):
                entry_data[task_name] = None
        self._reconnect_task = None
        await self.terminate()

    async def is_connected(self) -> bool:
        """Check that both TCP streams are open and the connection is valid."""
        if not self._publish or self._publish.is_closing():
            return False
        if not self._subscribe or self._subscribe.at_eof():
            return False
        return self._connected

    async def auth(self, entry: ConfigEntry = None):
        """Authenticate within a timeout and check the saved device identity."""
        authenticated = False
        try:
            async with asyncio.timeout(SETUP_TIMEOUT):
                if not self._connected and not await self._connect():
                    return False, "cannot_connect"
                data = await async_authenticate(
                    self._subscribe, self._publish, self._access_token
                )
                if entry is None:
                    authenticated = True
                    return True, data
                if (
                    entry.data.get("mac_address") is not None
                    and entry.data["mac_address"] != data["mac_address"]
                ) or (
                    entry.data.get("device_id") is not None
                    and entry.data["device_id"] != data["device_id"]
                ):
                    raise WorltyV1AuthenticationError(
                        "클래식 패드의 기기 식별 정보가 변경되었습니다"
                    )
                if self.worlty_pad is not None and (
                    self.worlty_pad.mac_address != data["mac_address"]
                    or self.worlty_pad.device_id != data["device_id"]
                ):
                    raise WorltyV1AuthenticationError(
                        "클래식 패드의 기기 식별 정보가 변경되었습니다"
                    )
                self._received_device_data = False
                self._queue.clear()
                self._session_generation += 1
                self._pending_health.clear()
                self._pending_gets.clear()
                self._health = datetime.datetime.now()
                if self._entry is None:
                    self._entry = entry
                    self._health = datetime.datetime.now()
                    self.worlty_pad = WorltyBaseDevice(data)
                    device_registry = dr.async_get(self.hass)
                    device_registry.async_get_or_create(
                        config_entry_id=entry.entry_id,
                        identifiers={(DOMAIN, self.worlty_pad.mac_address)},
                        serial_number=self.worlty_pad.mac_address,
                        manufacturer=self.worlty_pad.manufacturer,
                        name=entry.data.get("pad_name") or entry.title or data["name"],
                        model=self.worlty_pad.device_model,
                        sw_version=self.worlty_pad.fw_version,
                    )
                    cached = self.get_data("devices", {})
                    if isinstance(cached, dict):
                        for device in tuple(cached.values()):
                            if isinstance(device, dict) and not device.get("fk"):
                                self.update_device(device)
                    self.set_data("device_id", data["device_id"])
                    self.hass.data[DOMAIN][entry.entry_id]["health"] = (
                        entry.async_create_background_task(
                            self.hass, self.health_check(), "Worlty classic health"
                        )
                    )
                authenticated = True
                return True, None
        except WorltyV1AuthenticationError:
            self._authentication_rejections += 1
            return False, "invalid_access_token"
        except (TimeoutError, WorltyV1ConnectionError, OSError):
            return False, "cannot_connect"
        finally:
            if not authenticated:
                await self.terminate()

    async def _run_reconnect(
        self, *, force: bool = False, health_guard: tuple[int, int, str] | None = None
    ) -> bool:
        """Reconnect and restart runtime tasks without creating duplicates."""
        current_task = asyncio.current_task()
        try:
            async with self._reconnect_lock:
                if self._disconnect:
                    return False
                if not force and await self.is_connected():
                    return True

                entry_data = self._entry_data()
                if entry_data is None:
                    return False

                # Health recovery is provisional until the owner starts teardown.
                # No await separates this receipt check from listener cancellation.
                if health_guard is not None:
                    generation, received_count, reason = health_guard
                    if generation != self._session_generation:
                        return True
                    if (
                        self._received_device_data_count > received_count
                        and self._connected
                        and self._publish is not None
                        and not self._publish.is_closing()
                        and self._subscribe is not None
                        and not self._subscribe.at_eof()
                    ):
                        return True
                    self._record_connection_issue(reason)

                listener_task = entry_data.get("task")
                if (
                    listener_task is not None
                    and listener_task is not current_task
                    and not listener_task.done()
                ):
                    listener_task.cancel()
                    await asyncio.gather(listener_task, return_exceptions=True)
                if entry_data.get("task") is listener_task:
                    entry_data["task"] = None

                await self.terminate()
                attempt = 0
                auth_attempt = 0
                auth = False
                while not auth and not self._disconnect:
                    self._reconnect_attempts += 1
                    auth, error = await self.auth(self._entry)
                    if auth:
                        break

                    await self.terminate()
                    if error == "invalid_access_token":
                        delay = self._compute_auth_backoff(auth_attempt)
                        auth_attempt += 1
                        LOGGER.warning(
                            "클래식 패드 %s 인증이 거절됐습니다 (%.2f초 후 재시도)",
                            self._diagnostic_entry_id(),
                            delay,
                        )
                    else:
                        delay = self._compute_backoff(attempt)
                        attempt += 1

                    await asyncio.sleep(delay)

                if not auth or self._disconnect:
                    return False

                self._reconnect_successes += 1
                self._last_reconnected_at = self._diagnostic_timestamp()

                self._set_device_available(True)

                result = await self.get_worlty_devices()
                if result is None:
                    await self.terminate()
                    return False

                listener_task = entry_data.get("task")
                if listener_task is None or listener_task.done():
                    entry_data["task"] = self._entry.async_create_background_task(
                        self.hass, self.listen_for_message(), "Worlty classic listener"
                    )

                self._health = datetime.datetime.now()
                health_task = entry_data.get("health")
                if health_task is None or health_task.done():
                    entry_data["health"] = self._entry.async_create_background_task(
                        self.hass, self.health_check(), "Worlty classic health"
                    )
                self._schedule_queue_flush()
                return True
        finally:
            if self._reconnect_task is current_task:
                self._reconnect_task = None

    async def reauth(self) -> bool:
        """Wait for the shared reconnect task."""
        current_task = asyncio.current_task()
        reconnect_task = self._reconnect_task
        if (
            reconnect_task is not None
            and reconnect_task is not current_task
            and not reconnect_task.done()
        ):
            return await reconnect_task
        if await self.is_connected():
            return True
        reconnect_task = self._schedule_reconnect()
        if reconnect_task is None:
            return False
        return await reconnect_task

    def set_data(self, name: str, value: Any) -> Any:
        """Preserve existing settings while saving a value and current device metadata."""
        if self._entry is None:
            return value

        new_data = {
            **self._entry.data,
            "version": self.worlty_pad.fw_version,
            "model": self.worlty_pad.device_model,
            "device_id": self.worlty_pad.device_id,
            "mac_address": self.worlty_pad.mac_address,
        }

        new_data.update({name: value})

        self.hass.config_entries.async_update_entry(
            entry=self._entry,
            data=new_data,
        )
        return value

    def get_data(self, name: str, default_value=False) -> Any:
        """Read a saved setting or return the default if it is absent."""
        if self._entry is None:
            return default_value

        return self._entry.data.get(name, default_value)

    async def publish(self, payload) -> bool:
        """Send JSON over TCP and schedule reconnection after a transport failure."""
        writer = self._publish
        generation = self._session_generation
        try:
            message = json.dumps(payload)
        except (TypeError, ValueError):
            LOGGER.error("클래식 패드에 보낼 메시지를 만들지 못했습니다")
            return False

        try:
            current_task = asyncio.current_task()
            reconnect_task = self._reconnect_task
            if (
                reconnect_task is not None
                and reconnect_task is not current_task
                and not reconnect_task.done()
            ):
                return False

            if not await self.is_connected():
                self._schedule_reconnect()
                return False

            if (
                writer is None
                or writer is not self._publish
                or generation != self._session_generation
            ):
                LOGGER.debug("Publish stream is not initialized")
                return False

            LOGGER.debug("클래식 패드에 메시지를 보냅니다")
            writer.write(message.encode())
            await asyncio.wait_for(writer.drain(), timeout=5)
            return (
                generation == self._session_generation
                and writer is self._publish
                and self._connected
            )
        except TimeoutError as err:
            LOGGER.debug("TCP publish timed out")
            reason = "publish_timeout"
            failure = err
        except (OSError, asyncio.IncompleteReadError) as err:
            LOGGER.debug("TCP publish connection failed")
            reason = "publish_socket_error"
            failure = err
        if generation == self._session_generation and writer is self._publish:
            self._record_connection_issue(reason, error=failure)
            await self.terminate()
            self._schedule_reconnect()
        return False

    async def subscribe(self, timeout: float = None) -> dict[str, Any]:
        """Read one JSON line and distinguish timeout from connection loss."""
        try:
            line = await asyncio.wait_for(self._subscribe.readline(), timeout=timeout)
            if not line:
                self._record_connection_issue("eof")
                self._connected = False
                return {"error": "connection_lost"}
        except TimeoutError:
            return {"error": "timeout"}
        except asyncio.IncompleteReadError as err:
            self._record_connection_issue("incomplete_read", error=err)
            self._connected = False
            return {"error": "connection_lost"}
        except OSError as err:
            self._record_connection_issue("socket_error", error=err)
            self._connected = False
            return {"error": "connection_lost"}
        except ValueError as err:
            self._record_connection_issue("line_limit", error=err)
            self._connected = False
            return {"error": "connection_lost"}

        msg = line.decode("utf-8", errors="replace").strip()
        if not msg:
            self._record_connection_issue("empty_frame")
            return {}
        try:
            message = json.loads(msg)
        except ValueError as err:
            self._record_connection_issue("invalid_json", error=err)
            self._connected = False
            return {"error": "connection_lost"}
        if not isinstance(message, dict):
            self._record_connection_issue("invalid_frame")
            return {}
        return message

    async def health_check(self):
        """Probe an idle connection and reconnect if no response arrives."""
        while not self._disconnect:
            scheduled = time.monotonic() + 1
            await asyncio.sleep(1)
            self._record_timing("health_loop_lag", time.monotonic() - scheduled)
            elapsed = datetime.datetime.now() - self._health
            idle_seconds = (
                HEALTH_IDLE_SECONDS
                if self._received_device_data
                else INITIAL_DATA_TIMEOUT
            )
            if elapsed < datetime.timedelta(seconds=idle_seconds):
                continue

            LOGGER.debug(
                "Health idle; probing connection",
            )
            generation = self._session_generation
            received_count = self._received_device_data_count
            previous_probe = self._last_health_probe
            if await self._probe_health():
                continue

            probe = self._last_health_probe
            health_guard = None
            if (
                probe is not previous_probe
                and probe is not None
                and probe["result"] == "timeout"
            ):
                health_guard = (
                    generation,
                    received_count,
                    "health_probe_timeout"
                    if self._received_device_data
                    else "initial_data_timeout",
                )
            reconnect_task = self._schedule_reconnect(
                force=True, health_guard=health_guard
            )
            if reconnect_task is not None:
                await reconnect_task

    async def _probe_health(self) -> bool:
        """Probe an idle connection without competing with the stream reader."""
        if not await self.is_connected():
            self._record_connection_issue("connection_unavailable")
            return False

        generation = self._session_generation
        if self._received_device_data and self._health_map:
            pk = next(iter(self._health_map))
            payload = {"type": "get", "data": {"devices": [int(pk)]}}
        else:
            payload = {"type": "list", "data": {}}

        probe = self._last_health_probe = {
            "at": self._diagnostic_timestamp(),
            "session_generation": generation,
            "kind": payload["type"],
            "result": "waiting",
        }
        self._health_probe_attempts += 1
        previous_count = self._received_device_data_count
        sent = (
            await self._request_devices([int(pk)], generation)
            if payload["type"] == "get"
            else await self.publish(payload)
        )
        if not sent:
            probe["result"] = "publish_failed"
            return self._session_generation != generation

        deadline = self.hass.loop.time() + HEALTH_PROBE_TIMEOUT
        while self.hass.loop.time() < deadline:
            # A new authenticated session owns its own initial response deadline.
            if self._session_generation != generation:
                probe["result"] = "session_changed"
                return True
            if self._received_device_data_count > previous_count:
                probe["result"] = "response_received"
                self._health_probe_responses += 1
                return True
            await asyncio.sleep(0.1)
        if self._session_generation != generation:
            probe["result"] = "session_changed"
            return True
        if self._received_device_data_count > previous_count:
            probe["result"] = "response_received"
            self._health_probe_responses += 1
            return True
        probe["result"] = "timeout"
        return False

    async def listen_for_message(self):
        """Dispatch received messages and schedule reconnection when needed."""
        listener_generation = self._session_generation
        listener_writer = self._publish
        LOGGER.debug(
            "Listen for messages",
        )
        try:
            LOGGER.debug(
                "Subscribe message, connection[%s]",
                self._connected,
            )
            await asyncio.sleep(1)
            while self._connected:
                generation = self._session_generation
                message = await self.subscribe(3)
                if generation != self._session_generation:
                    return

                if message.get("error") == "connection_lost":
                    self._record_connection_issue("receive_error")
                    self._connected = False
                    break
                elif message.get("type"):
                    received_at = time.monotonic()
                    # Record receipt before handlers can wait across a reconnect.
                    if self._is_device_data(message):
                        self._received_device_data = True
                        self._received_device_data_count += 1
                        self._last_device_received_monotonic = received_at
                        self._health = datetime.datetime.now()
                    task = self._entry.async_create_background_task(
                        self.hass,
                        self.handle_message(
                            message,
                            received_at=received_at,
                            generation=generation,
                        ),
                        "Worlty classic message",
                    )
                    self._message_tasks.add(task)
                    task.add_done_callback(self._message_tasks.discard)
                elif message.get("error") == "timeout":
                    continue
                else:
                    self._record_connection_issue("invalid_frame")
                    LOGGER.debug(
                        "클래식 패드에서 올바르지 않은 메시지를 받았습니다",
                    )
                    break
        except asyncio.CancelledError:
            LOGGER.debug(
                "Listener cancelled",
            )
            raise
        finally:
            entry_data = self._entry_data()
            current_task = asyncio.current_task()
            if entry_data is not None and entry_data.get("task") is current_task:
                entry_data["task"] = None

            LOGGER.debug(
                "Listener finished",
            )
            if (
                listener_generation == self._session_generation
                and listener_writer is self._publish
            ):
                self._connected = False
                await self.terminate()

                if not self._disconnect:
                    LOGGER.debug(
                        "Try auth as listener finished",
                    )
                    self._schedule_reconnect()

    def is_entity_changed(self, pk, lct) -> bool:
        """Compare the cached change time for a device key with the reported time."""
        return self._health_map.get(pk, 0) != lct

    def _expire_device_requests(self) -> None:
        """Release expired reads lazily, without retransmission or extra tasks."""
        now = self.hass.loop.time()
        for pk, request in tuple(self._pending_gets.items()):
            if (
                request["generation"] != self._session_generation
                or request["deadline"] <= now
            ) and self._pending_gets.get(pk) is request:
                self._pending_gets.pop(pk, None)

    async def _request_devices(
        self,
        pks: list[int],
        generation: int,
        *,
        revisions: dict[int, int] | None = None,
    ) -> bool:
        """Coalesce reads until their own update or a finite lazy expiry."""
        if self._disconnect or generation != self._session_generation:
            return False
        self._expire_device_requests()
        requests = {}
        for pk in pks:
            if pk in self._pending_gets:
                pending = self._pending_gets[pk]
                if revisions is not None and pk in revisions:
                    revision = revisions[pk]
                    if pending["revision"] != revision:
                        pending["revision"] = revision
                        pending["followup"] = True
                continue
            request = {
                "generation": generation,
                "deadline": self.hass.loop.time() + HEALTH_PROBE_TIMEOUT,
                "revision": revisions.get(pk) if revisions is not None else None,
                "followup": False,
            }
            requests[pk] = self._pending_gets[pk] = request
        if not requests:
            return True
        sent = False
        try:
            sent = await self.publish(
                {"type": "get", "data": {"devices": list(requests)}}
            )
            return sent
        finally:
            if not sent:
                for pk, request in requests.items():
                    if self._pending_gets.get(pk) is request:
                        self._pending_gets.pop(pk, None)

    def _record_timing(self, name: str, seconds: float) -> None:
        """Keep only the last and maximum duration for each fixed metric."""
        seconds = max(0.0, seconds)
        self._timings[f"{name}_last_seconds"] = seconds
        key = f"{name}_max_seconds"
        self._timings[key] = max(self._timings.get(key, 0.0), seconds)

    def _is_device_data(self, message: dict[str, Any]) -> bool:
        """Recognize valid live device responses independently of cached records."""
        if "error" in message or not isinstance(message.get("data"), dict):
            return False
        devices = message["data"].get("devices")
        data_type = message.get("type")
        if data_type == "update":
            return isinstance(devices, list) and all(
                self._valid_device(device) for device in devices
            )
        if data_type == "health":
            return isinstance(devices, dict) and all(
                isinstance(pk, str)
                and pk.isdecimal()
                and int(pk) > 0
                and type(revision) is int
                and revision >= 0
                for pk, revision in devices.items()
            )
        if data_type == "device/list":
            return isinstance(devices, list) and all(
                type(pk) is int and pk > 0 for pk in devices
            )
        return False

    async def handle_message(
        self,
        message: dict[str, Any],
        *,
        received_at: float | None = None,
        generation: int | None = None,
    ) -> None:
        """Update device lists and states or remove entries based on a message."""
        if generation is None:
            generation = self._session_generation
        if self._disconnect or generation != self._session_generation:
            return
        if received_at is not None:
            self._record_timing("handler_queue_lag", time.monotonic() - received_at)
        if not isinstance(message, dict):
            return
        data_type = message.get("type")
        if not isinstance(data_type, str):
            return
        data: dict[str, Any] = message.get("data", {})
        if not isinstance(data, dict):
            return
        devices = data.get("devices")
        if (
            data_type == "update"
            and isinstance(devices, list)
            and all(self._valid_device(device) for device in devices)
        ):
            lock_started = time.monotonic()
            followup = {}
            async with self._device_lock:
                if generation != self._session_generation or self._disconnect:
                    return
                self._expire_device_requests()
                update_started = time.monotonic()
                self._record_timing("update_lock_wait", update_started - lock_started)
                LOGGER.debug(
                    "Handle %s devices",
                    len(devices),
                )

                devices = sorted(devices, key=lambda device: device.get("pk", 0))

                for device in devices:
                    device["children"] = sorted(
                        device.get("children", []), key=lambda child: child.get("pk", 0)
                    )

                    for child in device["children"]:
                        child["fk"] = device.get("pk", 0)

                    self.update_device(device)
                    if "lct" in device:
                        self._health_map[str(device["pk"])] = device["lct"]
                    request = self._pending_gets.pop(device["pk"], None)
                    if (
                        request is not None
                        and request["generation"] == generation
                        and request["followup"]
                        and self.is_entity_changed(
                            str(device["pk"]), request["revision"]
                        )
                    ):
                        followup[device["pk"]] = request["revision"]

                if len(devices) > 0:
                    self.set_data("devices", self._entity_map.copy())
                self._record_timing(
                    "update_handling", time.monotonic() - update_started
                )
            if followup:
                await self._request_devices(
                    list(followup), generation, revisions=followup
                )
        elif data_type == "health" and isinstance(devices, dict):
            self._expire_device_requests()
            changed = {}
            for pk, lct in devices.items():
                if (
                    not isinstance(pk, str)
                    or not pk.isdecimal()
                    or int(pk) <= 0
                    or type(lct) is not int
                    or lct < 0
                ):
                    continue
                key = int(pk)
                pending = self._pending_gets.get(key)
                if pending is not None:
                    if pending["revision"] != lct:
                        pending["revision"] = lct
                        pending["followup"] = True
                    continue
                pending = self._pending_health.get(key)
                if pending is not None:
                    pending["revision"] = lct
                elif self.is_entity_changed(pk, lct):
                    request = {"revision": lct}
                    changed[key] = self._pending_health[key] = request
            if changed:
                try:
                    await asyncio.sleep(1)
                    if generation != self._session_generation or self._disconnect:
                        return
                    # Do not request the same state if a valid update arrived during the delay.
                    revisions = {
                        pk: request["revision"]
                        for pk, request in changed.items()
                        if self._pending_health.get(pk) is request
                        and self.is_entity_changed(str(pk), request["revision"])
                    }
                    if revisions:
                        await self._request_devices(
                            list(revisions), generation, revisions=revisions
                        )
                finally:
                    for pk, request in changed.items():
                        if self._pending_health.get(pk) is request:
                            self._pending_health.pop(pk, None)
        elif data_type in {"device/list", "device/delete"}:
            # Firmware list contains all parent PKs; delete contains successfully deleted PKs.
            if not isinstance(devices, list) or any(
                type(pk) is not int or pk <= 0 for pk in devices
            ):
                return
            if data_type == "device/list":
                # Do not remove existing devices based on a response marked incomplete.
                if data.get("complete") is False or any(
                    key in data for key in ("cursor", "next", "offset")
                ):
                    return
                present = set(devices)
                removed = {
                    item["pk"]
                    for item in self._entity_map.values()
                    if not item.get("fk") and item["pk"] not in present
                }
                await self._async_remove_devices(removed, generation=generation)
                if generation != self._session_generation or self._disconnect:
                    return
                if devices:
                    await asyncio.sleep(1)
                    if generation != self._session_generation or self._disconnect:
                        return
                    await self._request_devices(devices, generation)
            else:
                await self._async_remove_devices(set(devices), generation=generation)
        else:
            LOGGER.debug("클래식 패드의 처리 대상이 아닌 메시지를 건너뜁니다")

    @staticmethod
    def _valid_device(device: Any, *, child: bool = False) -> bool:
        """Validate identifiers and data types used to update device records."""
        if (
            not isinstance(device, dict)
            or type(device.get("pk")) is not int
            or device["pk"] <= 0
        ):
            return False
        if not isinstance(device.get("cid" if child else "did"), str):
            return False
        if "payload" in device and not isinstance(device["payload"], dict):
            return False
        children = device.get("children", [])
        return isinstance(children, list) and all(
            WorltyLocal._valid_device(item, child=True) for item in children
        )

    async def _async_remove_devices(
        self, pks: set[int], *, generation: int | None = None
    ) -> None:
        """Remove device runtime state while preserving HA registries for re-registration."""
        if not pks:
            return
        async with self._device_lock:
            if generation is not None and generation != self._session_generation:
                return
            unique_ids = {
                unique_id
                for unique_id, item in self._entity_map.items()
                if (item.get("fk") or item.get("pk")) in pks
            }
            # Clear the cache first so HA removal callbacks cannot re-register stale records.
            entities_to_remove = []
            for unique_id in unique_ids:
                entity = self.worlty_entity.get(unique_id)
                if entity is not None:
                    entity._removed = True
                    if entity._loaded:
                        entities_to_remove.append(entity)
                    # Keep pending entities in this map until HA aborts their addition.
                self._entity_map.pop(unique_id, None)
                for entities in self._entities.values():
                    entities.pop(unique_id, None)
            for entity in entities_to_remove:
                await entity.async_remove()
                if generation is not None and generation != self._session_generation:
                    return
                self.unregister_entity(entity)
            for pk in pks:
                self._pending_health.pop(pk, None)
                self._pending_gets.pop(pk, None)
            for pk in pks:
                self._health_map.pop(str(pk), None)
            self._queue = [item for item in self._queue if item.get("pk") not in pks]
            self.set_data("devices", self._entity_map.copy())

    def get_worlty_entity(self, unique_id) -> dict[str, Any]:
        """Return the cached device record for a unique ID."""
        return self._entity_map.get(unique_id)

    async def get_worlty_devices(self) -> dict[Platform, dict[str, dict[str, Any]]]:
        """Return cached devices grouped by platform."""
        return self._entities

    def make_unique_id(self, pk: int, fk: int = 0, did: str = "") -> str:
        """Combine pad, parent, and child identifiers into a unique ID."""
        return (
            f"{self.worlty_pad.device_id}:{pk}:{did}"
            if fk == 0
            else f"{self.worlty_pad.device_id}:{fk}_{pk}:{did}"
        )

    def update_device(self, device: dict[str, Any]) -> None:
        """Update a device and its children while preserving parent links."""
        self._update_or_create_worlty_entity(device)

        for child in device.get("children", []):
            child["parent_unique_id"] = self.make_unique_id(
                device.get("pk"), 0, device.get("did")
            )
            self._update_or_create_worlty_entity(child)

    def _update_or_create_worlty_entity(self, device: dict[str, Any]) -> None:
        """Update cached entities and pass new entries to the platform callback."""
        pk: int = device.get("pk", 0)
        fk: int = device.get("fk", 0)

        unique_id = self.make_unique_id(pk, fk, device.get("did" if fk == 0 else "cid"))
        worlty_entity: WorltyBaseEntity = self.worlty_entity.get(unique_id)

        if worlty_entity is not None:
            self._entity_map.update({unique_id: device})
            if not worlty_entity._removed:
                worlty_entity.update_entity(device)
        else:
            entity = self._entity_map.setdefault(unique_id, device)
            entity.update(device)

            entity_platform: Platform = map_worlty_to_platform(
                entity.get("type"), entity.get("cls")
            )

            if entity_platform is not None:
                entity = self._entities[entity_platform].setdefault(unique_id, device)
                entity.update(device)
                add_entity_callback = self._add_entity_listeners.get(entity_platform)

                if add_entity_callback is not None:
                    add_entity_callback(entity)

    def register_entity(self, worlty_entity: "WorltyBaseEntity"):
        """Register a supported entity in the API by its unique ID."""
        if worlty_entity.worlty_type is not None:
            self.worlty_entity[worlty_entity.worlty_unique_id] = worlty_entity

    def register_add_listener(self, entity_type: Platform, cb: callback):
        """Register a callback for asynchronous entity additions."""
        self._add_entity_listeners[entity_type] = cb

        def remove():
            """Unregister the callback only if it is still the active callback."""
            if self._add_entity_listeners.get(entity_type) is cb:
                self._add_entity_listeners.pop(entity_type, None)

        return remove

    def unregister_entity(self, entity: "WorltyBaseEntity") -> None:
        """Release an entity and re-register devices reported again during removal."""
        if self.worlty_entity.get(entity.worlty_unique_id) is entity:
            self.worlty_entity.pop(entity.worlty_unique_id, None)
            # After HA cleans up a deleted pending entry, restore only newly reported records.
            if (
                entity._removed
                and (device := self._entity_map.get(entity.worlty_unique_id))
                is not None
            ):
                self._update_or_create_worlty_entity(device)

    async def async_get_diagnostics(self) -> dict[str, Any]:
        """Return connection and runtime counts without authentication data."""
        data = self._entry_data() or {}
        tasks = {data.get(key) for key in ("reauth", "health", "task", "loop")}
        tasks.update(self._message_tasks)
        tasks.add(self._reconnect_task)
        return {
            "protocol": "v1",
            "connected": await self.is_connected(),
            "entity_count": sum(len(items) for items in self._entities.values()),
            "pending_commands": len(self._queue),
            "received_device_data": self._received_device_data,
            "received_device_data_count": self._received_device_data_count,
            **self._timings,
            "owned_tasks": sum(task is not None and not task.done() for task in tasks),
            "connection": {
                "entry_id": self._entry.entry_id if self._entry is not None else None,
                "session_generation": self._session_generation,
                "reconnect_attempts": self._reconnect_attempts,
                "authenticated_reconnections": self._reconnect_successes,
                "authentication_rejections": self._authentication_rejections,
                "last_reconnected_at": self._last_reconnected_at,
                "disconnect_count": sum(self._connection_issue_counts.values()),
                "disconnect_reasons": dict(self._connection_issue_counts),
                "last_disconnect": (
                    dict(self._last_connection_issue)
                    if self._last_connection_issue is not None
                    else None
                ),
                "health_probe_attempts": self._health_probe_attempts,
                "health_probe_responses": self._health_probe_responses,
                "last_health_probe": (
                    dict(self._last_health_probe)
                    if self._last_health_probe is not None
                    else None
                ),
            },
        }

    async def loop(self) -> None:
        """Send each batch once within its authenticated connection session."""
        current_task = asyncio.current_task()
        generation = self._session_generation
        writer = self._publish
        reschedule = False
        try:
            await asyncio.sleep(0.05)
            if generation != self._session_generation or writer is not self._publish:
                reschedule = True
                return
            # Detach before sending so new commands cannot replay this batch.
            queued_devices, self._queue = self._queue, []
            if queued_devices:
                sent = await self.publish(
                    {
                        "type": "set",
                        "data": {"devices": queued_devices},
                    }
                )
                if (
                    not sent
                    and generation == self._session_generation
                    and writer is self._publish
                ):
                    self._queue.clear()
            reschedule = True
        except asyncio.CancelledError:
            if generation == self._session_generation and writer is self._publish:
                self._queue.clear()
            raise
        finally:
            entry_data = self._entry_data()
            if entry_data is not None and entry_data.get("loop") is current_task:
                entry_data["loop"] = None
            if reschedule and self._queue:
                self._schedule_queue_flush()

    def queue(self, data) -> None:
        """Merge commands for the same device, keeping the latest value per attribute."""
        if self._disconnect or self.hass.is_stopping:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="classic_connection_closing"
            )
        reconnect_task = self._reconnect_task
        if (
            not self._connected
            or self._publish is None
            or self._publish.is_closing()
            or self._subscribe is None
            or self._subscribe.at_eof()
            or (reconnect_task is not None and not reconnect_task.done())
        ):
            self._queue.clear()
            self._schedule_reconnect()
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="classic_connection_failed"
            )
        payload = {}
        for queued in self._queue:
            if queued.get("pk") == data.get("pk"):
                payload.update(queued.get("payload", {}))
        payload.update(data.get("payload", {}))
        self._queue = [q for q in self._queue if q.get("pk") != data.get("pk")]
        self._queue.append({**data, "payload": payload})

        self._schedule_queue_flush()


class WorltyBaseDevice:
    """Store Worlty device metadata."""

    device_id: str
    device_model: str
    mac_address: str
    fw_version: str
    device_available: bool

    def __init__(self, device_info: dict[str, Any]) -> None:
        """Store pad identity and version from the authentication response."""
        self.device_id = device_info.get("device_id")
        self.device_model = device_info.get("model")
        self.mac_address = device_info.get("mac_address")
        self.manufacturer = MANUFACTURER
        self.fw_version = device_info.get("version")
        self.device_available = True

    def __repr__(self) -> str:
        """Return device identifiers as a diagnostic string."""
        return (
            f"Worlty(model={self.device_model}, id={self.device_id}, "
            f"mac_address={self.mac_address}, fw_version={self.fw_version})"
        )


class WorltyBaseEntity(Entity):
    """Represent a Worlty entity in Home Assistant."""

    coordinator: WorltyLocal
    worlty_is_child: bool
    worlty_parent: int
    worlty_parent_unique_id: str
    worlty_unique_id: str
    worlty_pk: int
    worlty_name: str
    worlty_sub: str
    worlty_type: int
    worlty_class: int
    worlty_state: Any
    worlty_last_changed_time: int
    worlty_attribute: dict[str, Any]
    _loaded: bool
    _update_entity: callback

    def __init__(
        self,
        coordinator: "WorltyLocal",
        entity_info: dict[str, Any],
        entity_update: callback,
    ) -> None:
        """Build a HA entity from device metadata and register it with the API."""
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.worlty_is_child = entity_info.get("fk", 0) > 0
        self.worlty_parent = entity_info.get("fk", 0)
        self.worlty_parent_unique_id = entity_info.get(
            "parent_unique_id", self.worlty_parent
        )
        self.worlty_pk = entity_info.get("pk")

        if not self.worlty_is_child:
            self.worlty_name = entity_info.get("did", "unknown")
            name_parts = self.worlty_name.split("_")
            self.worlty_sub = map_worlty_sub(
                self.hass.config.language,
                name_parts[1] if len(name_parts) > 1 else name_parts[0],
            )
        else:
            parent_sub = (
                self.coordinator.get_worlty_entity(self.worlty_parent_unique_id) or {}
            ).get("did", "unknown")
            parent_parts = parent_sub.split("_")
            parent_sub = parent_parts[1] if len(parent_parts) > 1 else parent_parts[0]
            self.worlty_name = entity_info.get("cid")
            suffix = f" ({map_worlty_sub(self.hass.config.language, parent_sub)})"
            self.worlty_sub = (
                f"{map_worlty_sub(self.hass.config.language, self.worlty_name)}{suffix}"
            )
            self._attr_name = self._child_display_name(None)

        self.worlty_unique_id = coordinator.make_unique_id(
            self.worlty_pk, self.worlty_parent, self.worlty_name
        )
        self.worlty_type = entity_info.get("type")
        self.worlty_class = entity_info.get("cls")
        self.worlty_last_changed_time = entity_info.get("lct")
        self.worlty_attribute = dict(entity_info.get("payload") or {})
        state = map_worlty_state(self.hass.config.language, entity_info.get("stt"))

        self.worlty_state = (
            state
            if self.worlty_type != WorltyBaseType.EVENT.value
            else (
                f"{state}("
                f"{datetime.datetime.fromtimestamp(self.worlty_last_changed_time).strftime('%H:%M:%S')})"
            )
        )

        self._update_entity = entity_update
        self._loaded = False
        self._removed = False
        self._attr_unique_id = self.worlty_unique_id.lower()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.worlty_pad.mac_address)},
        )

        description, key = map_worlty_entity_description(
            self.worlty_type, self.worlty_class, self.worlty_sub
        )

        platform_domain = map_worlty_to_platform(self.worlty_type, self.worlty_class)
        self.entity_id = generate_entity_id(
            f"{platform_domain}.{{}}",
            f"Worlty {key} {self.worlty_sub}",
            hass=self.coordinator.hass,
        )

        self.entity_description = description
        self._update_reported_name(entity_info)
        if is_advanced_entity(platform_domain, self.worlty_name):
            self._attr_entity_category = (
                EntityCategory.DIAGNOSTIC
                if platform_domain == "sensor"
                else EntityCategory.CONFIG
            )
            entry = coordinator._entry
            options = entry.options if entry is not None else {}
            self._attr_entity_registry_enabled_default = (
                options.get(CONF_V1_MODE, V1_MODE_BASIC) == V1_MODE_ADVANCED
            )

        self.coordinator.register_entity(self)
        self.async_on_remove(self._async_finish_pending_removal)
        LOGGER.debug("클래식 패드 엔티티를 초기화했습니다")

    def __repr__(self) -> str:
        """Return device identifiers as a diagnostic string."""
        return (
            f"{'WorltyEntity' if not self.worlty_is_child else 'WorltyChild'}"
            f"(id={self.worlty_unique_id}, name={self.worlty_name}, type={self.worlty_type}, "
            f"class={map_worlty_to_platform(self.worlty_type, self.worlty_class)})"
        )

    @callback
    def _async_finish_pending_removal(self) -> None:
        """Release the API entity reference even when platform addition is aborted."""
        self.coordinator.unregister_entity(self)

    async def add_to_platform_finish(self) -> None:
        """Abort deleted pending entities before HA publishes their initial state."""
        if self._removed:
            # Before internal_added, clear reserved state and abort addition without async_remove.
            self.hass.states.async_remove(self.entity_id)
            self.add_to_platform_abort()
            return
        await super().add_to_platform_finish()

    async def async_added_to_hass(self):
        """Mark HA registration complete and schedule the initial state update."""
        self._loaded = True
        self._update_callback()

    async def async_will_remove_from_hass(self):
        """Unregister the removed entity from the API."""
        self._loaded = False
        self.coordinator.unregister_entity(self)

    def _update_callback(self):
        """Schedule a state update after classic pad data changes."""
        self.schedule_update_ha_state()

    def _child_display_name(self, name: str | None) -> str:
        """Display the parent device name alongside the child item name."""
        parent = self.coordinator.get_worlty_entity(self.worlty_parent_unique_id) or {}
        parent_name = _reported_name(parent.get("name"))
        if not parent_name:
            key = (
                get_worlty_description(parent.get("type"), parent.get("cls"))[0]
                or "device"
            )
            slot = str(parent.get("did") or self.worlty_parent).split("_", 1)[-1]
            parent_name = f"{map_worlty_sub(self.hass.config.language, key)} {slot}"
        name = name or map_worlty_sub(self.hass.config.language, self.worlty_name)
        prefix = f"{parent_name} · "
        return name if name.startswith(prefix) else prefix + name

    def _update_reported_name(self, entity_info: dict[str, Any]) -> bool:
        """Use the firmware name without changing command keys or HA identifiers."""
        if "name" not in entity_info:
            return False
        name = _reported_name(entity_info.get("name"))
        if self.worlty_is_child:
            name = self._child_display_name(name)
        if name == getattr(self, "_attr_name", None):
            return False
        if name is None:
            del self._attr_name
        else:
            self._attr_name = name
        return True

    def update_entity(self, entity_info: dict[str, Any]) -> None:
        """Apply reported names and states and notify HA about loaded entity changes."""
        name_changed = self._update_reported_name(entity_info)
        write_state = name_changed
        if "payload" in entity_info:
            self.worlty_last_changed_time = entity_info.get("lct")
            payload: dict = entity_info.get("payload")

            changed = payload != self.worlty_attribute
            if changed:
                self.worlty_attribute.update(payload)
                if self._update_entity is not None:
                    self._update_entity()

            state = map_worlty_state(self.hass.config.language, entity_info.get("stt"))

            payload_state = map_worlty_state(
                self.hass.config.language, payload.get("stt")
            )

            if (
                changed
                or state != self.worlty_state
                or payload_state != self.worlty_state
            ):
                self.worlty_state = (
                    state
                    if self.worlty_type != WorltyBaseType.EVENT.value
                    else (
                        f"{state}("
                        f"{datetime.datetime.fromtimestamp(self.worlty_last_changed_time).strftime('%H:%M:%S')})"
                    )
                )
                write_state = True
        if self._loaded and write_state:
            self._update_callback()

    async def set_device(self, **kwargs: Any) -> None:
        """Queue commands, mapping child commands to attributes on the parent device."""
        if self._removed:
            raise HomeAssistantError("클래식 패드에서 제거된 기기입니다")
        if self.worlty_is_child:
            payload = {}
            if kwargs.get("stt") is not None:
                payload[self.worlty_name] = kwargs["stt"]
            self.coordinator.queue(
                {
                    "pk": self.worlty_parent,
                    "payload": payload,
                }
            )
        else:
            self.coordinator.queue(
                {
                    "pk": self.worlty_pk,
                    "payload": {**kwargs},
                }
            )

    @property
    def available(self):
        """Return entity availability based on the pad connection state."""
        return (
            self.coordinator.worlty_pad.device_available
            if self.coordinator.worlty_pad is not None
            else self._loaded
        )

    @property
    def should_poll(self) -> bool:
        """Disable polling for this entity."""
        return False
