"""``switch.<name>_heating``: on = keep heating (re-arming at each expiry), off = turn it off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BoostTimerConfigEntry
from .entity import BoostTimerEntity
from .planner import Kind, Request


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoostTimerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the heating switch."""
    async_add_entities([HeatingSwitch(entry.runtime_data, "heating")])


class HeatingSwitch(BoostTimerEntity, SwitchEntity):
    """On while heating is requested ("on" or "heat until"), even between re-arms."""

    @property
    def is_on(self) -> bool:
        request = self.controller.request
        return request is not None and request.kind is Kind.HEAT

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.controller
        return {
            "request": c.request.label if c.request else None,
            "presses_today": c.presses_today,
            "last_press": c.last_press.isoformat() if c.last_press else None,
            "session_started_at": (
                c.session_started_at.isoformat() if c.session_started_at else None
            ),
            "uncertain": c.model.uncertain,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Ask for heating. Repeating it (e.g. a thermostat's keep-alive) changes nothing, so it
        neither moves the ``max_duration`` deadline nor cuts a retry backoff short."""
        if self._is_switch_request(Kind.HEAT):
            return
        await self.controller.submit(
            Request.on(dt_util.utcnow(), self.controller.options, source="switch")
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Ask for the timer to be off; repeating it changes nothing."""
        if self._is_switch_request(Kind.OFF):
            return
        await self.controller.submit(Request.off(dt_util.utcnow(), source="switch"))

    def _is_switch_request(self, kind: Kind) -> bool:
        r = self.controller.request
        return r is not None and r.kind is kind and r.source == "switch"
