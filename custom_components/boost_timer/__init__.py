"""Boost Timer: drive a dumb pushbutton boost timer through a SwitchBot Bot."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .controller import BoostTimerController
from .services import async_register_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type BoostTimerConfigEntry = ConfigEntry[BoostTimerController]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions shared by all timers."""
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BoostTimerConfigEntry) -> bool:
    """Set up one boost timer from its config entry and resume its saved request."""
    controller = BoostTimerController(hass, entry)
    entry.runtime_data = controller
    await controller.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BoostTimerConfigEntry) -> bool:
    """Unload the timer's platforms and stop its controller."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok
