"""Carry out requests against the physical timer (the Home Assistant layer).

:class:`BoostTimerController` owns one timer: its :class:`~.model.TimerModel`, the active
:class:`~.planner.Request`, and the press adapter. It asks :func:`~.planner.next_action` what to
do, presses, saves after every press, and schedules its next wake-up. Entities and actions talk to
it through :meth:`~BoostTimerController.submit` and :meth:`~BoostTimerController.observe`.

Events fired on the bus:

- ``boost_timer_heating_started`` / ``boost_timer_heating_stopped`` with
  ``{entry_id, at, request, source}`` when the timer goes from off to on or back.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_BOT, CONF_PRESS_SERVICE, DEFAULTS, DOMAIN
from .model import Observation, Options, TimerModel
from .planner import Burst, Done, Kind, Request, Wait, next_action
from .press import PressAdapter, PressFailed

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
EVENT_STARTED = f"{DOMAIN}_heating_started"
EVENT_STOPPED = f"{DOMAIN}_heating_stopped"


def _local_date(t: datetime) -> date:
    return dt_util.as_local(t).date()


class BoostTimerController:
    """One boost timer: model, active request, presses and persistence."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.options = Options.from_mapping(entry.options)
        self._press = PressAdapter(
            hass,
            entry.data[CONF_BOT],
            entry.options.get(CONF_PRESS_SERVICE, DEFAULTS[CONF_PRESS_SERVICE]),
        )
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        self._unsub_wake: CALLBACK_TYPE | None = None
        self._stopped = False

        self.model = TimerModel()
        self.request: Request | None = None
        self._presses = 0
        self._presses_date: date | None = None
        self.session_started_at: datetime | None = None
        self._was_heating = False

    # ---- public API -------------------------------------------------------------------------

    @property
    def presses_today(self) -> int:
        """Presses sent since local midnight."""
        if self._presses_date != _local_date(dt_util.utcnow()):
            return 0
        return self._presses

    @property
    def last_press(self) -> datetime | None:
        """When the last press landed."""
        return self.model.last_press_at

    def is_heating(self, now: datetime | None = None) -> bool:
        """Return True if the model says the radiator is powered."""
        return self.model.setting(now or dt_util.utcnow(), self.options) > 0

    async def async_start(self) -> None:
        """Restore saved state and carry on with the active request."""
        data = await self._store.async_load()
        if data:
            self.model = TimerModel.from_dict(data.get("model", {}))
            req = data.get("request")
            self.request = Request.from_dict(req) if req else None
            self._presses = int(data.get("presses", 0))
            pd = data.get("presses_date")
            self._presses_date = date.fromisoformat(pd) if pd else None
            ss = data.get("session_started_at")
            self.session_started_at = datetime.fromisoformat(ss) if ss else None
        self._was_heating = self.session_started_at is not None
        await self._run()

    async def async_stop(self) -> None:
        """Stop scheduling; an in-progress run finishes its current press."""
        self._stopped = True
        self._cancel_wake()
        async with self._lock:
            pass

    async def submit(self, request: Request) -> bool:
        """Make ``request`` the active request and start carrying it out.

        :returns: False, changing nothing, when the active request has a higher priority.
        """
        if self.request is not None and self.request.priority > request.priority:
            return False
        self.request = request
        await self._save()
        self._notify()
        self._kick()
        return True

    async def observe(self, obs: Observation, source: str) -> None:
        """Correct the model from an observation (e.g. a sync), then re-plan."""
        self.model.observe(obs, dt_util.utcnow(), self.options, source)
        await self._save()
        self._notify()
        self._kick()

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Call ``cb`` after every change; returns a function that removes it."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    # ---- execution --------------------------------------------------------------------------

    @callback
    def _kick(self) -> None:
        if not self._stopped:
            self.hass.async_create_task(self._run(), f"{DOMAIN} run")

    async def _run(self) -> None:
        async with self._lock:
            if self._stopped:
                return
            while True:
                now = dt_util.utcnow()
                self.model.tick(now, self.options)
                self._check_heating(now)
                action = next_action(self.model, self.request, now, self.options)
                if isinstance(action, Burst):
                    if not await self._burst(action.presses):
                        break
                    continue
                if isinstance(action, Done) and self._request_finishes_when_done():
                    self.request = None
                    await self._save()
                self._schedule(action, now)
                break
            self._notify()

    def _request_finishes_when_done(self) -> bool:
        r = self.request
        return r is not None and (r.kind is not Kind.HEAT or r.until is not None)

    async def _burst(self, presses: int) -> bool:
        """Press ``presses`` times back to back. Returns False if a press failed."""
        for _ in range(presses):
            try:
                await self._press.press()
            except PressFailed as err:
                _LOGGER.warning("Press failed: %s", err)
                return False
            now = dt_util.utcnow()
            self.model.apply_press(now, self.options)
            self._count_press(now)
            self._check_heating(now)
            await self._save()
            self._notify()
        return True

    def _count_press(self, now: datetime) -> None:
        today = _local_date(now)
        if self._presses_date != today:
            self._presses, self._presses_date = 0, today
        self._presses += 1

    def _check_heating(self, now: datetime) -> None:
        heating = self.is_heating(now)
        if heating == self._was_heating:
            return
        self._was_heating = heating
        self.session_started_at = now if heating else None
        self.hass.bus.async_fire(
            EVENT_STARTED if heating else EVENT_STOPPED,
            {
                "entry_id": self.entry.entry_id,
                "at": now.isoformat(),
                "request": self.request.label if self.request else None,
                "source": self.request.source if self.request else None,
            },
        )

    def _schedule(self, action: Wait | Done, now: datetime) -> None:
        times = [t for t in (self.model.next_change(now, self.options),) if t]
        if isinstance(action, Wait):
            times.append(action.until)
        self._cancel_wake()
        if times and not self._stopped:
            self._unsub_wake = async_track_point_in_utc_time(self.hass, self._on_wake, min(times))

    @callback
    def _on_wake(self, _now: datetime) -> None:
        self._unsub_wake = None
        self._kick()

    def _cancel_wake(self) -> None:
        if self._unsub_wake:
            self._unsub_wake()
            self._unsub_wake = None

    # ---- persistence and listeners ----------------------------------------------------------

    async def _save(self) -> None:
        await self._store.async_save(
            {
                "model": self.model.to_dict(),
                "request": self.request.to_dict() if self.request else None,
                "presses": self._presses,
                "presses_date": self._presses_date.isoformat() if self._presses_date else None,
                "session_started_at": (
                    self.session_started_at.isoformat() if self.session_started_at else None
                ),
            }
        )

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()
