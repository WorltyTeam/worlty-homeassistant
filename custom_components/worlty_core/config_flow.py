"""Configuration flows for local Worlty device connections."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST, CONF_PORT
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    AUTH_SCOPES,
    CONF_AUTH_SCOPES,
    CONF_BINDING_GENERATION,
    CONF_CLIENT_ID,
    CONF_DEVICE_ID,
    CONF_HA_INSTANCE_ID,
    CONF_PAD_NAME,
    CONF_PROTOCOL,
    CONF_TLS_HOSTNAME,
    CONF_TLS_SPKI_SHA256,
    CONF_TLS_TARGET_ID,
    CONF_V1_MODE,
    DEFAULT_PORT,
    DOMAIN,
    PROTOCOL_V1,
    PROTOCOL_V2,
    V1_MODE_ADVANCED,
    V1_MODE_BASIC,
)
from .errors import (
    WorltyAuthenticationError,
    WorltyConnectionError,
    WorltyIdentityMismatchError,
    WorltyPinMismatchError,
    WorltyRequestError,
)
from .identity import async_get_client_identity
from .naming import config_entry_title, default_pad_name
from .protocol import WorltyLocalClient, async_pair_device, async_probe_device
from .tls import normalize_tls_hostname

_LOGGER = logging.getLogger(__name__)

_DISCOVERY_PROFILE = (
    ("model", "pad-v2"),
    ("pv", "1"),
    ("transport", "wss"),
)
_REAUTH_TRUST_KEYS = (
    CONF_PROTOCOL,
    CONF_DEVICE_ID,
    CONF_TLS_TARGET_ID,
    CONF_TLS_SPKI_SHA256,
    CONF_CLIENT_ID,
    CONF_HA_INSTANCE_ID,
    CONF_BINDING_GENERATION,
    CONF_HOST,
    CONF_PORT,
    CONF_TLS_HOSTNAME,
)


async def _ignore_message(message: dict[str, Any]) -> None:
    return None


def _txt_value(properties: dict[str, Any], key: str) -> str:
    value = properties.get(key, properties.get(key.encode(), ""))
    return value.decode(errors="ignore") if isinstance(value, bytes) else str(value)


def _hardware_id(value: str) -> str:
    """Normalize an advertised MAC; TXT is a hint, not authentication."""
    value = value.replace(":", "").replace("-", "").upper()
    return value if re.fullmatch(r"[0-9A-F]{12}", value) else ""


def _display_name(value: str, fallback: str) -> str:
    name = "".join(character for character in value if character.isprintable()).strip()
    return name[:128] or fallback


def _discovery_name(info: ZeroconfServiceInfo, fallback: str) -> str:
    advertised = _txt_value(info.properties, "name")
    suffix = f".{info.type}"
    if not advertised and info.name.lower().endswith(suffix.lower()):
        advertised = info.name[: -len(suffix)]
    return _display_name(advertised, fallback)


async def async_probe_v1_device(hass, *, host: str, port: int, access_token: str):
    """Load the classic adapter only for identified or explicitly selected pads."""
    from .v1 import (
        WorltyV1AuthenticationError,
        WorltyV1ConnectionError,
    )
    from .v1 import (
        async_probe_device as probe_v1,
    )

    try:
        return await probe_v1(hass, host, port, access_token)
    except WorltyV1AuthenticationError as err:
        raise WorltyAuthenticationError("PAD rejected the access token") from err
    except WorltyV1ConnectionError as err:
        raise WorltyConnectionError("클래식 패드를 인증할 수 없습니다") from err


def _entry_title(name: str, device_id: str) -> str:
    return config_entry_title(name, device_id)


class WorltyCoreConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Discover devices, pin certificate keys, and establish connections."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize discovery information and registration state."""
        self._discovery: dict[str, Any] = {}
        self._connection: dict[str, Any] = {}
        self._registration_task: asyncio.Task[dict[str, Any]] | None = None
        self._registration_proof: dict[str, str] | None = None
        self._resume_generation: str | None = None
        self._pair_receipt_received = False
        self._reauth_entry: ConfigEntry | None = None
        self._reauth_original_data: dict[str, Any] | None = None
        self._pending_rebind = False
        self._protocol = PROTOCOL_V2

    def _flow_name(self) -> str:
        if self._reauth_entry is not None:
            return getattr(self._reauth_entry, "title", "") or "Worlty"
        return self._discovery.get("name") or "Worlty"

    def _set_flow_name(self, name: str) -> None:
        self.context["title_placeholders"] = {"name": name}

    async def async_step_reconfigure(self, user_input=None):
        """Register an existing device using the same approval and identity checks."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None or entry.domain != DOMAIN:
            return self.async_abort(reason="authentication_failed")
        return await self.async_step_reauth(entry.data)

    async def async_step_reauth(self, entry_data):
        """Find the existing entry and begin reauthentication."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is None or self._reauth_entry.domain != DOMAIN:
            return self.async_abort(reason="authentication_failed")
        self._reauth_original_data = dict(self._reauth_entry.data)
        self._set_flow_name(self._flow_name())
        if self._reauth_original_data.get(CONF_PROTOCOL) == PROTOCOL_V1:
            self._protocol = PROTOCOL_V1
            return await self.async_step_token()
        if (
            any(
                not isinstance(self._reauth_original_data.get(key), str)
                or not self._reauth_original_data[key]
                for key in (
                    CONF_HOST,
                    CONF_TLS_HOSTNAME,
                    CONF_DEVICE_ID,
                    CONF_TLS_TARGET_ID,
                    CONF_TLS_SPKI_SHA256,
                    CONF_CLIENT_ID,
                    CONF_HA_INSTANCE_ID,
                )
            )
            or type(self._reauth_original_data.get(CONF_PORT)) is not int
        ):
            return self.async_abort(reason="authentication_failed")
        return await self.async_step_user()

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo):
        """Offer advertised pairing and safely update existing device addresses."""
        if discovery_info.type not in {
            "_worlty._tcp.local.",
            "_worlty-pair._tcp.local.",
        }:
            return self.async_abort(reason="invalid_discovery")
        profile_matches = all(
            _txt_value(discovery_info.properties, key).strip().lower() == expected
            for key, expected in _DISCOVERY_PROFILE
        )
        if not profile_matches:
            # Do not reinterpret corrupt, future-format, or failed hub records as
            # classic pads.
            if discovery_info.type == "_worlty-pair._tcp.local." or any(
                key in discovery_info.properties
                or key.encode() in discovery_info.properties
                for key, _ in _DISCOVERY_PROFILE
            ):
                return self.async_abort(reason="invalid_discovery")
            return await self._async_discover_v1(discovery_info)
        try:
            tls_hostname = normalize_tls_hostname(discovery_info.hostname)
        except WorltyAuthenticationError:
            return self.async_abort(reason="invalid_discovery")
        host = str(discovery_info.host).strip()
        port = discovery_info.port
        if not host or not isinstance(port, int) or not 1 <= port <= 65535:
            return self.async_abort(reason="invalid_discovery")
        entries = [
            entry
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_PROTOCOL, PROTOCOL_V2) == PROTOCOL_V2
        ]
        known = next(
            (
                entry
                for entry in entries
                if entry.data.get(CONF_TLS_HOSTNAME) == tls_hostname
            ),
            None,
        )
        if (
            known is not None
            and known.data.get(CONF_HOST) == host
            and known.data.get(CONF_PORT) == port
        ):
            # Discovery suppresses duplicate UI but does not establish trust.
            # An unchanged configured endpoint needs no reconnection.
            await self.async_set_unique_id(known.data[CONF_DEVICE_ID])
            return self.async_abort(reason="already_configured")
        if known is None and discovery_info.type != "_worlty-pair._tcp.local.":
            return self.async_abort(reason="registration_not_open")
        try:
            candidate = await async_probe_device(
                async_get_clientsession(self.hass),
                host=host,
                port=port,
                tls_hostname=tls_hostname,
            )
        except (WorltyConnectionError, TimeoutError):
            return self.async_abort(reason="cannot_connect")
        except (WorltyAuthenticationError, ValueError):
            return self.async_abort(reason="authentication_failed")
        if known is None:
            known = next(
                (
                    entry
                    for entry in entries
                    if entry.data.get(CONF_DEVICE_ID) == candidate[CONF_DEVICE_ID]
                    or entry.data.get(CONF_TLS_TARGET_ID)
                    == candidate[CONF_TLS_TARGET_ID]
                ),
                None,
            )
        if known is not None:
            if any(
                known.data.get(key) != candidate[key]
                for key in (CONF_DEVICE_ID, CONF_TLS_TARGET_ID, CONF_TLS_SPKI_SHA256)
            ):
                return self.async_abort(reason="pin_mismatch")
            self.hass.config_entries.async_update_entry(
                known,
                data={
                    **known.data,
                    CONF_HOST: host,
                    CONF_PORT: port,
                    CONF_TLS_HOSTNAME: tls_hostname,
                },
            )
            await self.async_set_unique_id(candidate[CONF_DEVICE_ID])
            return self.async_abort(reason="already_configured")
        await self.async_set_unique_id(candidate[CONF_DEVICE_ID])
        self._abort_if_unique_id_configured()
        self._discovery = {
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_TLS_HOSTNAME: tls_hostname,
            **candidate,
            "name": _discovery_name(
                discovery_info, default_pad_name(candidate[CONF_TLS_TARGET_ID])
            ),
        }
        self._set_flow_name(self._discovery["name"])
        return await self.async_step_user()

    async def _async_discover_v1(self, info: ZeroconfServiceInfo):
        """Identify the classic name/id TXT profile without sending a token."""
        mac = _hardware_id(_txt_value(info.properties, "id"))
        host = str(info.host).strip()
        if (
            not mac
            or not _txt_value(info.properties, "name").strip()
            or not host
            or type(info.port) is not int
            or not 1 <= info.port <= 65535
            or info.port == 80
        ):
            return self.async_abort(reason="invalid_discovery")
        await self.async_set_unique_id(f"v1_{mac.lower()}")
        self._abort_if_unique_id_configured()
        self._protocol = PROTOCOL_V1
        self._discovery = {
            CONF_HOST: host,
            CONF_PORT: info.port,
            "mac_address": mac,
            "name": _discovery_name(info, f"Worlty_{mac[-6:].lower()}"),
        }
        self._set_flow_name(self._discovery["name"])
        return await self.async_step_token()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Use discovery directly and offer connection methods only for manual setup."""
        if self._protocol == PROTOCOL_V1:
            return await self.async_step_token(user_input)
        if (
            self._discovery
            or self._reauth_entry is not None
            or self._registration_task is not None
            or user_input is not None
        ):
            return await self.async_step_approval(user_input)
        return self.async_show_menu(step_id="user", menu_options=["approval", "token"])

    async def async_step_token(self, user_input: dict[str, Any] | None = None):
        """Authenticate a classic pad once and retain its protocol and hardware ID."""
        original = self._reauth_original_data or {}
        if original and original.get(CONF_PROTOCOL) != PROTOCOL_V1:
            return self.async_abort(reason="authentication_failed")
        self._protocol = PROTOCOL_V1
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host = self._discovery.get(CONF_HOST) or user_input.get(
                    CONF_HOST, original.get(CONF_HOST)
                )
                port = self._discovery.get(CONF_PORT) or user_input.get(
                    CONF_PORT, original.get(CONF_PORT)
                )
                token = user_input.get(CONF_ACCESS_TOKEN)
                if (
                    not isinstance(host, str)
                    or not host.strip()
                    or type(port) is not int
                    or not 1 <= port <= 65535
                    or not isinstance(token, str)
                    or not token.strip()
                ):
                    raise ValueError("클래식 패드 연결 정보가 올바르지 않습니다")
                candidate = await async_probe_v1_device(
                    self.hass, host=host.strip(), port=port, access_token=token
                )
                expected_mac = self._discovery.get("mac_address") or original.get(
                    "mac_address"
                )
                if (
                    expected_mac
                    and candidate["mac_address"] != expected_mac
                    or original
                    and candidate[CONF_DEVICE_ID] != original[CONF_DEVICE_ID]
                    or original
                    and any(
                        self._reauth_entry.data.get(key) != original.get(key)
                        for key in (
                            CONF_PROTOCOL,
                            CONF_HOST,
                            CONF_PORT,
                            CONF_ACCESS_TOKEN,
                            CONF_DEVICE_ID,
                            "mac_address",
                        )
                    )
                ):
                    errors["base"] = "identity_mismatch"
                else:
                    await self.async_set_unique_id(
                        f"v1_{candidate['mac_address'].lower()}"
                    )
                    name = _display_name(candidate["name"], self._flow_name())
                    data = {
                        CONF_PROTOCOL: PROTOCOL_V1,
                        CONF_HOST: host.strip(),
                        CONF_PORT: port,
                        CONF_ACCESS_TOKEN: token,
                        CONF_DEVICE_ID: candidate["device_id"],
                        "mac_address": candidate["mac_address"],
                        CONF_PAD_NAME: name,
                    }
                    if self._reauth_entry is not None:
                        self.hass.config_entries.async_update_entry(
                            self._reauth_entry, data={**self._reauth_entry.data, **data}
                        )
                        await self.hass.config_entries.async_reload(
                            self._reauth_entry.entry_id
                        )
                        return self.async_abort(reason="reauth_successful")
                    self._abort_if_unique_id_configured()
                    self._set_flow_name(name)
                    return self.async_create_entry(title=name, data=data)
            except WorltyAuthenticationError:
                errors["base"] = "invalid_access_token"
            except (WorltyConnectionError, TimeoutError):
                errors["base"] = "cannot_connect"
            except (ValueError, TypeError, KeyError):
                errors["base"] = "invalid_input"
        schema: dict[Any, Any] = {}
        if not self._discovery:
            schema[vol.Required(CONF_HOST, default=original.get(CONF_HOST, ""))] = str
            port_options = (
                {"default": original[CONF_PORT]} if CONF_PORT in original else {}
            )
            schema[vol.Required(CONF_PORT, **port_options)] = vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            )
        schema[vol.Required(CONF_ACCESS_TOKEN)] = str
        return self.async_show_form(
            step_id="token",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"name": self._flow_name()},
        )

    async def async_step_approval(self, user_input: dict[str, Any] | None = None):
        """Verify identity after discovery confirmation or hostname entry."""
        if (
            self._reauth_original_data
            and self._reauth_original_data.get(CONF_PROTOCOL) == PROTOCOL_V1
        ):
            return self.async_abort(reason="authentication_failed")
        if self._registration_task is not None:
            return await self.async_step_registration()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                original = self._reauth_original_data or {}
                self._connection = {
                    CONF_HOST: str(
                        self._discovery.get(CONF_HOST)
                        or user_input.get(CONF_HOST, original.get(CONF_HOST, ""))
                    ).strip(),
                    CONF_PORT: int(
                        self._discovery.get(CONF_PORT)
                        or user_input.get(
                            CONF_PORT, original.get(CONF_PORT, DEFAULT_PORT)
                        )
                    ),
                    CONF_TLS_HOSTNAME: normalize_tls_hostname(
                        str(
                            self._discovery.get(CONF_TLS_HOSTNAME)
                            or user_input.get(
                                CONF_TLS_HOSTNAME,
                                original.get(
                                    CONF_TLS_HOSTNAME, user_input.get(CONF_HOST, "")
                                ),
                            )
                        )
                    ),
                }
                if (
                    not self._connection[CONF_HOST]
                    or not 1 <= self._connection[CONF_PORT] <= 65535
                ):
                    raise ValueError("invalid PAD identity")
                candidate = await async_probe_device(
                    async_get_clientsession(self.hass),
                    host=self._connection[CONF_HOST],
                    port=self._connection[CONF_PORT],
                    tls_hostname=self._connection[CONF_TLS_HOSTNAME],
                )
                if CONF_DEVICE_ID in self._discovery and any(
                    candidate[key] != self._discovery[key]
                    for key in (
                        CONF_DEVICE_ID,
                        CONF_TLS_TARGET_ID,
                        CONF_TLS_SPKI_SHA256,
                    )
                ):
                    raise WorltyPinMismatchError(
                        "discovered PAD identity changed before confirmation"
                    )
                if self._reauth_entry is not None and any(
                    candidate[key] != original[key]
                    for key in (CONF_DEVICE_ID, CONF_TLS_TARGET_ID)
                ):
                    raise WorltyPinMismatchError(
                        "reauthentication cannot replace the PAD identity"
                    )
                self._connection.update(candidate)
                self._set_flow_name(
                    self._discovery.get("name")
                    or default_pad_name(candidate[CONF_TLS_TARGET_ID])
                )
                await self.async_set_unique_id(self._connection[CONF_DEVICE_ID])
                if self._reauth_entry is None:
                    self._abort_if_unique_id_configured()
                generation = str(user_input.get(CONF_BINDING_GENERATION, "")).strip()
                if generation and (
                    not re.fullmatch(r"[0-9a-f]{16}", generation)
                    or generation == "0000000000000000"
                ):
                    raise ValueError("invalid binding generation")
                rebind = (
                    self._reauth_entry is not None
                    and candidate[CONF_TLS_SPKI_SHA256]
                    != original[CONF_TLS_SPKI_SHA256]
                )
                if rebind and generation:
                    raise ValueError("new TLS key requires a fresh PAD approval")
                self._resume_generation = generation or None
                self._pending_rebind = rebind
            except WorltyPinMismatchError:
                errors["base"] = "pin_mismatch"
            except (WorltyConnectionError, TimeoutError) as err:
                _LOGGER.warning(
                    "Local identity probe failed: %s",
                    type(err.__cause__ or err).__name__,
                )
                errors["base"] = "cannot_connect"
            except (TypeError, ValueError, KeyError, WorltyAuthenticationError):
                errors["base"] = "invalid_input"
            else:
                if self._pending_rebind:
                    return await self.async_step_confirm_rebind()
                return await self.async_step_registration()
        schema: dict[Any, Any] = {}
        show_advanced = self.context.get("show_advanced_options")
        if not self._discovery:
            host_key = (
                vol.Required(CONF_HOST, default=self._reauth_original_data[CONF_HOST])
                if self._reauth_original_data is not None
                else vol.Required(CONF_HOST)
            )
            schema[host_key] = str
            if show_advanced:
                port_default = (self._reauth_original_data or {}).get(
                    CONF_PORT, DEFAULT_PORT
                )
                schema[vol.Required(CONF_PORT, default=port_default)] = vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                )
                if self._reauth_entry is not None:
                    tls_hostname = self._reauth_original_data[CONF_TLS_HOSTNAME]
                    schema[vol.Required(CONF_TLS_HOSTNAME, default=tls_hostname)] = str
                else:
                    schema[vol.Optional(CONF_TLS_HOSTNAME)] = str
        if show_advanced:
            schema[vol.Optional(CONF_BINDING_GENERATION, default="")] = str
        form_schema = vol.Schema(schema)
        if self._reauth_entry is not None:
            form_schema = self.add_suggested_values_to_schema(
                form_schema, self._reauth_entry.data
            )
        return self.async_show_form(
            step_id="approval",
            data_schema=form_schema,
            errors=errors,
            description_placeholders={
                "scopes": ", ".join(AUTH_SCOPES),
                "name": self._flow_name(),
            },
        )

    async def async_step_confirm_rebind(self, user_input: dict[str, Any] | None = None):
        """Require explicit administrator action when the TLS key changes."""
        if not self._pending_rebind or self._reauth_original_data is None:
            return self.async_abort(reason="authentication_failed")
        if user_input is not None and user_input.get("confirm") is True:
            self._pending_rebind = False
            return await self.async_step_registration()
        return self.async_show_form(
            step_id="confirm_rebind",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
            errors={"base": "confirmation_required"} if user_input is not None else {},
            description_placeholders={
                "device_id": self._reauth_original_data[CONF_DEVICE_ID],
                "target_id": self._reauth_original_data[CONF_TLS_TARGET_ID],
                "old_pin": self._reauth_original_data[CONF_TLS_SPKI_SHA256],
                "new_pin": self._connection[CONF_TLS_SPKI_SHA256],
            },
        )

    def _reauth_entry_unchanged(self) -> bool:
        if self._reauth_entry is None:
            return True
        original = self._reauth_original_data
        return original is not None and all(
            self._reauth_entry.data.get(key) == original.get(key)
            for key in _REAUTH_TRUST_KEYS
        )

    async def _register(self) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + 600.0
        async with asyncio.timeout_at(deadline):
            return await self._register_until(deadline)

    async def _register_until(self, deadline: float) -> dict[str, Any]:
        candidate = dict(self._connection)
        original = self._reauth_original_data
        identity = await async_get_client_identity(
            self.hass,
            client_id=original[CONF_CLIENT_ID] if original is not None else None,
        )
        if original is not None and (
            identity.client_id != original[CONF_CLIENT_ID]
            or identity.ha_instance_id != original[CONF_HA_INSTANCE_ID]
        ):
            raise WorltyAuthenticationError("stored HA pairing principal changed")
        session = async_get_clientsession(self.hass)
        connection = dict(
            host=candidate[CONF_HOST],
            port=candidate[CONF_PORT],
            tls_hostname=candidate[CONF_TLS_HOSTNAME],
            expected_device_id=candidate[CONF_DEVICE_ID],
            expected_target_id=candidate[CONF_TLS_TARGET_ID],
            expected_pin=candidate[CONF_TLS_SPKI_SHA256],
        )
        # This caps the legacy WDP registration window.
        # The hub's original window and transport deadline may expire earlier.
        if self._resume_generation is None:
            task = asyncio.current_task()

            def proposal_ready(window_id: str, request_digest: str) -> None:
                # Eager tasks may start before assignment to _registration_task.
                if (
                    task is not None
                    and (
                        self._registration_task is None
                        or task is self._registration_task
                    )
                    and not task.done()
                    and not task.cancelling()
                    and self._resume_generation is None
                ):
                    self._registration_proof = {
                        "window_id": window_id,
                        "request_digest": request_digest,
                    }

            try:
                scopes, generation = await async_pair_device(
                    session,
                    **connection,
                    identity=identity,
                    approval_deadline=deadline,
                    proposal_ready=proposal_ready,
                )
            finally:
                if self._registration_task is None or task is self._registration_task:
                    self._registration_proof = None
            # Keep the hub's durable approval receipt even if later authentication
            # fails.
            self._resume_generation = generation
            self._pair_receipt_received = True
        else:
            # Recover approval saved before the HA entry was created.
            # Access still requires proof of possession of the existing key.
            scopes, generation = AUTH_SCOPES, self._resume_generation
        client = WorltyLocalClient(
            session,
            **connection,
            binding_generation=generation,
            identity=identity,
            message_handler=_ignore_message,
            requested_scopes=scopes,
        )
        try:
            device_id = await client.async_connect()
        finally:
            await client.async_disconnect()
        pad_name = self._discovery.get("name") or default_pad_name(
            candidate[CONF_TLS_TARGET_ID]
        )
        return {
            **candidate,
            CONF_PROTOCOL: PROTOCOL_V2,
            CONF_DEVICE_ID: device_id,
            CONF_PAD_NAME: pad_name,
            CONF_CLIENT_ID: identity.client_id,
            CONF_HA_INSTANCE_ID: identity.ha_instance_id,
            CONF_AUTH_SCOPES: list(scopes),
            CONF_BINDING_GENERATION: generation,
        }

    async def async_step_registration(self, user_input=None):
        """Keep one cancellable approval wait across UI refreshes."""
        if self._pending_rebind:
            return await self.async_step_confirm_rebind()
        if any(
            key not in self._connection
            for key in (
                CONF_HOST,
                CONF_PORT,
                CONF_TLS_HOSTNAME,
                CONF_DEVICE_ID,
                CONF_TLS_TARGET_ID,
                CONF_TLS_SPKI_SHA256,
            )
        ):
            return self.async_abort(reason="invalid_input")
        if not self._reauth_entry_unchanged():
            return self.async_abort(reason="pin_mismatch")
        if self._registration_task is None:
            self._registration_proof = None
            self._registration_task = self.hass.async_create_task(self._register())
        if not self._registration_task.done():
            return self.async_show_progress(
                step_id="registration",
                progress_action="registration",
                progress_task=self._registration_task,
                description_placeholders=(
                    dict(self._registration_proof or {})
                    if not self._registration_task.cancelling()
                    else {}
                ),
            )
        self._registration_proof = None
        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(self, user_input=None):
        """Check registration results and create or update the config entry."""
        assert self._registration_task is not None
        try:
            data = self._registration_task.result()
        except WorltyIdentityMismatchError:
            return self.async_abort(reason="pin_mismatch")
        except WorltyAuthenticationError:
            if self._pair_receipt_received:
                return await self.async_step_retry()
            return self.async_abort(reason="authentication_failed")
        except WorltyRequestError:
            return self.async_abort(reason="authentication_failed")
        except (WorltyConnectionError, TimeoutError) as err:
            _LOGGER.warning(
                "Local registration failed: %s", type(err.__cause__ or err).__name__
            )
            if self._resume_generation is not None:
                return await self.async_step_retry()
            return self.async_abort(reason="cannot_connect")
        except (TypeError, ValueError):
            return self.async_abort(reason="invalid_input")
        if self._reauth_entry is not None:
            original = self._reauth_original_data
            if not self._reauth_entry_unchanged() or any(
                data[key] != original[key]
                for key in (
                    CONF_DEVICE_ID,
                    CONF_TLS_TARGET_ID,
                    CONF_CLIENT_ID,
                    CONF_HA_INSTANCE_ID,
                )
            ):
                return self.async_abort(reason="pin_mismatch")
            self.hass.config_entries.async_update_entry(
                self._reauth_entry,
                data={
                    **self._reauth_entry.data,
                    **{
                        key: data[key]
                        for key in (
                            CONF_TLS_SPKI_SHA256,
                            CONF_BINDING_GENERATION,
                            CONF_AUTH_SCOPES,
                            CONF_HOST,
                            CONF_PORT,
                            CONF_TLS_HOSTNAME,
                        )
                    },
                },
            )
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")
        return self.async_create_entry(
            title=_entry_title(data[CONF_PAD_NAME], data[CONF_DEVICE_ID]), data=data
        )

    async def async_step_retry(self, user_input=None):
        """Retry authentication after approval without proposing ADD again."""
        if user_input is not None:
            self._registration_task = None
            return await self.async_step_registration()
        return self.async_show_form(step_id="retry", data_schema=vol.Schema({}))

    def async_remove(self) -> None:
        """Clear registration proof and cancel pending registration."""
        self._registration_proof = None
        if self._registration_task is not None and not self._registration_task.done():
            self._registration_task.cancel()
        super().async_remove()

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry):
        """Select the options flow for the device protocol."""
        if config_entry.data.get(CONF_PROTOCOL) == PROTOCOL_V1:
            return WorltyClassicOptionsFlow()
        return WorltyCoreOptionsFlow()


class _WorltyOptionsSteps:
    """Configure display options for classic pads."""

    async def async_step_init(self, user_input=None):
        if self.config_entry.data.get(CONF_PROTOCOL) != PROTOCOL_V1:
            return self.async_abort(reason="export_not_enabled")
        errors = {}
        mode = self.config_entry.options.get(CONF_V1_MODE, V1_MODE_BASIC)
        if user_input is not None:
            mode = user_input.get(CONF_V1_MODE)
            if mode in (V1_MODE_BASIC, V1_MODE_ADVANCED):
                return self.async_create_entry(
                    title="", data={**self.config_entry.options, CONF_V1_MODE: mode}
                )
            errors["base"] = "invalid_mode"
            mode = self.config_entry.options.get(CONF_V1_MODE, V1_MODE_BASIC)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_V1_MODE, default=mode): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[V1_MODE_BASIC, V1_MODE_ADVANCED],
                            translation_key=CONF_V1_MODE,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )


class WorltyCoreOptionsFlow(_WorltyOptionsSteps, config_entries.OptionsFlow):
    """Hub options flow with reloads handled by the existing update listener."""


class WorltyClassicOptionsFlow(
    _WorltyOptionsSteps, config_entries.OptionsFlowWithReload
):
    """Classic options flow with HA-managed reloads and no update listener."""
