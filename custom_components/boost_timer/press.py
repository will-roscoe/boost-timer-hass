"""Press the SwitchBot once: the only place that knows how the Bot is driven.

Hardware quirks (a Bot that ignores ``turn_on`` while HA thinks it is already on, a lost first
command after the Bot sleeps) are handled here, so the planner and controller never change.
"""

from __future__ import annotations

import asyncio

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

#: Seconds before a press call that has not returned counts as failed. Only a guard against a call
#: that never returns: the SwitchBot library retries a failing connection itself and raises when it
#: gives up, and a first press after the Bot's 8.5 s link timeout has taken over 2 minutes.
PRESS_TIMEOUT = 300


class PressFailed(Exception):
    """The press service call failed; the press probably did not happen."""


class BotUnavailable(PressFailed):
    """The Bot entity is missing, unavailable or unknown, so nothing was sent."""


class BotUnreachable(BotUnavailable):
    """The SwitchBot library gave up connecting to the Bot, so nothing was sent."""


class PressAdapter:
    """Send one press to a SwitchBot exposed as a ``switch`` (press mode) or a ``button``."""

    def __init__(self, hass: HomeAssistant, entity_id: str, service: str) -> None:
        """
        :param entity_id: the Bot's entity, ``switch.*`` or ``button.*``.
        :param service: for a ``switch``: ``"turn_on"``, ``"turn_off"`` or ``"alternate"`` (the
            opposite of its current state); ignored for a ``button``.
        """
        self._hass = hass
        self._entity_id = entity_id
        self._service = service

    async def press(self) -> None:
        """Press once and return when Home Assistant reports the call done.

        :raises BotUnavailable: the entity is missing or ``unavailable`` (a switch also when
            ``unknown``).
        :raises BotUnreachable: the SwitchBot library could not connect to the Bot.
        :raises PressFailed: the service call raised anything else, or took over
            :data:`PRESS_TIMEOUT`.
        """
        state = self._hass.states.get(self._entity_id)
        domain = self._entity_id.split(".", 1)[0]
        # A button's state is the time it was last pressed, so "unknown" just means "never".
        blocked = (STATE_UNAVAILABLE,) if domain == "button" else (STATE_UNAVAILABLE, STATE_UNKNOWN)
        if state is None or state.state in blocked:
            raise BotUnavailable(f"{self._entity_id} is {state.state if state else 'missing'}")
        if domain == "button":
            service = "press"
        elif self._service == "alternate":
            service = "turn_off" if state.state == STATE_ON else "turn_on"
        else:
            service = self._service
        try:
            async with asyncio.timeout(PRESS_TIMEOUT):
                await self._hass.services.async_call(
                    domain, service, {"entity_id": self._entity_id}, blocking=True
                )
        except Exception as err:  # noqa: BLE001 - any error means "probably not pressed"
            # Matched by name: bleak_retry_connector is the SwitchBot integration's dependency.
            if type(err).__name__ == "BleakNotFoundError":
                raise BotUnreachable(str(err)) from err
            raise PressFailed(str(err) or type(err).__name__) from err
