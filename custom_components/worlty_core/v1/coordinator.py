"""Coordinate classic pad data updates with Home Assistant."""

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_HOST,
    CONF_IP_ADDRESS,
    CONF_PORT,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, LOGGER
from .transport import WorltyV1AuthenticationError, WorltyV1ConnectionError
from .worlty import WorltyLocal


class WorltyDataCoordinator(DataUpdateCoordinator):
    """Connect the classic pad API and cached devices to a HA coordinator."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the local API and HA coordinator from the config entry."""
        self.hass = hass
        self.entry = entry
        self.api = WorltyLocal(
            hass,
            entry.data.get(CONF_HOST, entry.data.get(CONF_IP_ADDRESS)),
            entry.data.get(CONF_PORT),
            entry.data.get(CONF_ACCESS_TOKEN),
        )

        super().__init__(hass, LOGGER, name=DOMAIN)

    async def connect(self):
        """Authenticate the pad and distinguish connection failure from rejection."""
        authenticated, error = await self.api.auth(self.entry)
        if not authenticated:
            if error == "invalid_access_token":
                raise WorltyV1AuthenticationError("클래식 패드가 인증을 거절했습니다")
            raise WorltyV1ConnectionError("클래식 패드 인증을 완료하지 못했습니다")

    async def async_close(self) -> None:
        """Wait for entry background tasks to stop and close the TCP streams."""
        await self.api.disconnect()

    async def _async_update_data(self) -> dict[Platform, dict[str, dict[str, Any]]]:
        """Return the device lists cached by the API for each platform."""
        try:
            return await self.api.get_worlty_devices()
        except Exception as err:
            raise UpdateFailed(err) from err
