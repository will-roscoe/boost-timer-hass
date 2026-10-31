"""``select.<name>_setting``: the setting the LEDs show; choosing one presses to it once."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BoostTimerConfigEntry
from .controller import BoostTimerController
from .entity import BoostTimerEntity
from .planner import Request


def setting_label(n: int, step: timedelta) -> str:
    """Return ``"off"`` for 0, else the run length of ``n`` steps, e.g. ``"2h"`` or ``"90min"``."""
    if n == 0:
        return "off"
    minutes = int(n * step.total_seconds() // 60)
    return f"{minutes // 60}h" if minutes % 60 == 0 else f"{minutes}min"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoostTimerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the setting select."""
    async_add_entities([SettingSelect(entry.runtime_data, "setting")])


class SettingSelect(BoostTimerEntity, SelectEntity):
    """The displayed setting, counting down by itself; selecting one is a one-off press."""

    def __init__(self, controller: BoostTimerController, key: str) -> None:
        super().__init__(controller, key)
        opts = controller.options
        self._attr_options = [setting_label(n, opts.step) for n in range(opts.steps + 1)]

    @property
    def current_option(self) -> str:
        c = self.controller
        return self._attr_options[c.model.setting(dt_util.utcnow(), c.options)]

    async def async_select_option(self, option: str) -> None:
        n = self._attr_options.index(option)
        await self.controller.submit(Request.set(n, dt_util.utcnow(), source="select"))
