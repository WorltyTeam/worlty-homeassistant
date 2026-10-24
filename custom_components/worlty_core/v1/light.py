"""Support classic pad lights."""

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.color import value_to_brightness
from homeassistant.util.percentage import percentage_to_ordered_list_item

from .const import DOMAIN
from .coordinator import WorltyDataCoordinator
from .worlty import WorltyBaseEntity


def _coerce_int(value: Any, default: int | None = 0) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_brightness(value: int) -> int:
    return min(max(value, 1), 255)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add initial entities and register a callback for newly discovered devices."""
    coordinator: WorltyDataCoordinator = hass.data[DOMAIN][config_entry.entry_id]["api"]
    entities = []
    if coordinator.data is not None:
        entities = [
            WorltyLight(coordinator.api, entity)
            for entity in coordinator.data.get(Platform.LIGHT, {}).values()
            if entity.get("hide") is not True
        ]

    async_add_entities(entities)

    @callback
    def async_add_entity(entity: dict[str, Any]):
        """Add a newly discovered device if it is eligible for display."""
        if entity.get("hide") is not True:
            async_add_entities([WorltyLight(coordinator.api, entity)])

    config_entry.async_on_unload(
        coordinator.api.register_add_listener(Platform.LIGHT, async_add_entity)
    )


class WorltyLight(WorltyBaseEntity, LightEntity):
    """Represent classic pad lights."""

    def __init__(self, worlty_coordinator, worlty_entity_info) -> None:
        """Initialize the common entity and its device attributes."""
        super().__init__(worlty_coordinator, worlty_entity_info, self._update_entity)
        self._update_entity()
        self._color_mode: ColorMode = ColorMode.ONOFF
        self.wt_last_bri: int = 0
        self.wt_level_max: int = 0
        self.dim_level = []

    def _sync_dimming_levels(self) -> int:
        self.wt_level_max = _coerce_int(self.worlty_attribute.get("lvm"), 0)
        if self.wt_level_max > 1:
            if len(self.dim_level) != self.wt_level_max:
                self.dim_level = [i for i in range(1, self.wt_level_max + 1)]
        else:
            self.dim_level = []
        return self.wt_level_max

    def _turn_on_brightness(self, requested: Any = None) -> int:
        bri = _coerce_int(requested, 0)
        if bri is not None and bri > 0:
            return _clamp_brightness(bri)

        if self.wt_last_bri > 0:
            return _clamp_brightness(self.wt_last_bri)

        bri = _coerce_int(self.worlty_attribute.get("bri"), 0)
        if bri is not None and bri > 0:
            return _clamp_brightness(bri)

        level_max = self._sync_dimming_levels()
        level = _coerce_int(self.worlty_attribute.get("lv"), 0)
        if level is not None and level_max > 1 and 0 < level <= level_max:
            return _clamp_brightness(value_to_brightness((1, level_max), level))

        return 255

    @callback
    def _update_entity(self) -> None:
        """Use the common entity state without additional computed attributes."""

    @property
    def is_on(self) -> bool:
        """Return the power state reported by the device."""
        return self.worlty_attribute.get("stt", False)

    @property
    def brightness(self) -> int | None:
        """Convert device brightness to the Home Assistant range of 0 to 255."""
        bri = _coerce_int(self.worlty_attribute.get("bri"), None)

        level_max = self._sync_dimming_levels()
        level = _coerce_int(self.worlty_attribute.get("lv"), 0)

        if level is not None and level_max > 1 and 0 < level <= level_max:
            bri = value_to_brightness((1, level_max), level)
        if bri is not None and bri > 0:
            self.wt_last_bri = bri
        return bri

    @property
    def color_mode(self) -> ColorMode:
        """Return the Home Assistant color mode used to control the light."""
        return self._color_mode

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Return color modes that match the dimming capability."""
        modes = {ColorMode.ONOFF}
        sc = _coerce_int(self.worlty_attribute.get("sc"), 0)
        if (sc & (1 << 1)) != 0:
            modes = {ColorMode.BRIGHTNESS}
            self._color_mode = ColorMode.BRIGHTNESS
        if (sc & (1 << 2)) != 0:
            modes = {ColorMode.BRIGHTNESS}
            self._color_mode = ColorMode.BRIGHTNESS
        return modes

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Queue a command to turn on the device."""
        level_max = self._sync_dimming_levels()
        sc = _coerce_int(self.worlty_attribute.get("sc"), 0)
        state = self._turn_on_brightness(kwargs.get(ATTR_BRIGHTNESS))
        handled = False

        if level_max > 1 and (sc & (1 << 1)) != 0:
            state_pct = round(state / 255 * 100)
            await self.set_device(
                stt=True, lv=percentage_to_ordered_list_item(self.dim_level, state_pct)
            )
            handled = True
        if (sc & (1 << 2)) != 0:
            await self.set_device(stt=True, bri=state)
            handled = True
        if not handled:
            await self.set_device(stt=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Queue a command to turn off the device."""
        await self.set_device(stt=False)
