"""Carry out requests against the physical timer (the Home Assistant layer).

:class:`BoostTimerController` owns one timer: its :class:`~.model.TimerModel`, the active
:class:`~.planner.Request`, and the press adapter. It asks :func:`~.planner.next_action` what to
do, presses, saves after every press, and schedules its next wake-up. Entities and actions talk to
it through :meth:`~BoostTimerController.submit` and :meth:`~BoostTimerController.observe`.

Events fired on the bus:

- ``boost_timer_heating_started`` / ``boost_timer_heating_stopped`` with
  ``{entry_id, at, request, source}`` when the timer goes from off to on or back.
- ``boost_timer_press_failed`` with ``{entry_id, at, press_index, reason}`` when a press fails,
  or is so slow that the selection window may have closed mid-burst.

After ``press_retries`` failures in a row while heating is requested, a persistent notification
is raised; it is dismissed by the next successful press.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_BOT, CONF_PRESS_SERVICE, DEFAULTS, DOMAIN
from .model import Observation, Options, TimerModel, UnknownObs
from .planner import Burst, Done, Kind, Request, Wait, next_action
from .press import BotUnavailable, PressAdapter, PressFailed

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
EVENT_STARTED = f"{DOMAIN}_heating_started"
EVENT_STOPPED = f"{DOMAIN}_heating_stopped"
EVENT_PRESS_FAILED = f"{DOMAIN}_press_failed"


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
        self._unsub_started: CALLBACK_TYPE | None = None
        self._stopped = False

        self.model = TimerModel()
        self.request: Request | None = None
        self._presses = 0
        self._presses_date: date | None = None
        self.session_started_at: datetime | None = None
        self._was_heating = False

        self._failures = 0
        self._notified = False
        self._retry_at: datetime | None = None
        # Rate limiting of heating on/off reversals.
        self._applied_on: bool | None = None
        self._last_switch_change: datetime | None = None
        self._deferred_until: datetime | None = None

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
        """Restore saved state; carry on with the active request once Home Assistant has started.

        Pressing waits for startup so the Bot's entity exists, and so setup isn't held up by a
        Bluetooth burst.
        """
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
        self._unsub_started = async_at_started(self.hass, self._on_started)

    @callback
    def _on_started(self, _hass: HomeAssistant) -> None:
        self._unsub_started = None
        self._kick()

    async def async_stop(self) -> None:
        """Stop scheduling; an in-progress run finishes its current burst."""
        self._stopped = True
        self._cancel_wake()
        if self._unsub_started:
            self._unsub_started()
            self._unsub_started = None
        async with self._lock:
            pass

    async def submit(self, request: Request) -> bool:
        """Make ``request`` the active request and start carrying it out.

        :returns: False, changing nothing, when the active request has a higher priority.
        """
        if self.request is not None and self.request.priority > request.priority:
            return False
        self.request = request
        self._rate_limit(request, dt_util.utcnow())
        self._retry_at = None
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

    # ---- rate limiting ----------------------------------------------------------------------

    @staticmethod
    def _onness(request: Request | None) -> bool | None:
        """True for heat, False for off, None for a one-off setting (never rate limited)."""
        if request is None or request.kind is Kind.SET:
            return None
        return request.kind is Kind.HEAT

    def _rate_limit(self, request: Request, now: datetime) -> None:
        """Defer an on/off reversal made within ``min_switch_interval`` of the last one."""
        on = self._onness(request)
        last = self._last_switch_change
        if (
            on is not None
            and self._applied_on is not None
            and on != self._applied_on
            and last is not None
            and now - last < self.options.min_switch_interval
        ):
            self._deferred_until = last + self.options.min_switch_interval
            return
        self._deferred_until = None
        self._apply_switch(on, now)

    def _apply_switch(self, on: bool | None, now: datetime) -> None:
        if on is not None and on != self._applied_on:
            self._applied_on = on
            self._last_switch_change = now

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
                if self._deferred_until is not None:
                    if now < self._deferred_until:
                        self._schedule(Wait(self._deferred_until), now)
                        break
                    self._deferred_until = None
                    self._apply_switch(self._onness(self.request), now)
                if self._retry_at is not None:
                    if now < self._retry_at:
                        self._schedule(Wait(self._retry_at), now)
                        break
                    self._retry_at = None
                action = next_action(self.model, self.request, now, self.options)
                if isinstance(action, Burst):
                    if not await self._burst(action.presses):
                        await self._after_failure()
                    elif self._burst_missed_target():
                        await self._abandon_unreachable(action.presses)
                    continue
                if isinstance(action, Done) and self._request_finishes_when_done(now):
                    self.request = None
                    await self._save()
                self._schedule(action, now)
                break
            self._notify()

    def _request_finishes_when_done(self, now: datetime) -> bool:
        """SET and OFF end once satisfied; heat-until ends only when ``until`` has passed."""
        r = self.request
        if r is None:
            return False
        if r.kind is Kind.HEAT:
            return r.until is not None and now >= r.until
        return True

    async def _after_failure(self) -> None:
        """Back off before retrying; give up on a one-off setting after ``press_retries``."""
        self._retry_at = dt_util.utcnow() + self.options.retry_backoff
        r = self.request
        if r is not None and r.kind is Kind.SET and self._failures >= self.options.press_retries:
            _LOGGER.warning("Giving up on setting %s after %d failures", r.setting, self._failures)
            self.request = None
            await self._save()

    def _burst_missed_target(self) -> bool:
        """True if, once the window closes, the planner would burst again: a loop."""
        idle_at = self.model.idle_at(self.options)
        if idle_at is None:
            return False
        return isinstance(next_action(self.model, self.request, idle_at, self.options), Burst)

    async def _abandon_unreachable(self, presses: int) -> None:
        """Drop a request a burst cannot satisfy, instead of pressing round the cycle forever."""
        now = dt_util.utcnow()
        self.model.observe(UnknownObs(), now, self.options, "executor")
        self._press_failed(presses, "burst did not reach the requested setting; request dropped")
        self.request = None
        await self._save()

    async def _burst(self, presses: int) -> bool:
        """Press ``presses`` times back to back. Returns False if the burst failed.

        An unavailable Bot before the first press changes nothing. Any other failure, or a press
        after the first that took so long the selection window may have closed, marks the model
        uncertain.
        """
        window = self.options.selection_window - self.options.burst_margin
        for i in range(presses):
            started = dt_util.utcnow()
            try:
                await self._press.press()
            except PressFailed as err:
                if not (i == 0 and isinstance(err, BotUnavailable)):
                    self.model.observe(UnknownObs(), dt_util.utcnow(), self.options, "executor")
                    await self._save()
                self._press_failed(i + 1, str(err))
                return False
            now = dt_util.utcnow()
            self.model.apply_press(now, self.options)
            self._count_press(now)
            self._check_heating(now)
            slow = i > 0 and now - started > window
            if slow:
                self.model.observe(UnknownObs(), now, self.options, "executor")
            await self._save()
            self._notify()
            if slow:
                self._press_failed(i + 1, f"press took {(now - started).total_seconds():.1f} s")
                return False
        self._press_succeeded()
        return True

    def _press_failed(self, index: int, reason: str) -> None:
        _LOGGER.warning("Press %d failed: %s", index, reason)
        self._failures += 1
        self.hass.bus.async_fire(
            EVENT_PRESS_FAILED,
            {
                "entry_id": self.entry.entry_id,
                "at": dt_util.utcnow().isoformat(),
                "press_index": index,
                "reason": reason,
            },
        )
        heating = self.request is not None and self.request.kind is Kind.HEAT
        if heating and self._failures >= self.options.press_retries and not self._notified:
            self._notified = True
            persistent_notification.async_create(
                self.hass,
                f"{self.entry.title}: the SwitchBot failed {self._failures} times in a row "
                f"({reason}). Heating is still requested; retrying every "
                f"{int(self.options.retry_backoff.total_seconds() // 60)} min.",
                title="Boost Timer",
                notification_id=self._notification_id,
            )

    def _press_succeeded(self) -> None:
        self._failures = 0
        if self._notified:
            self._notified = False
            persistent_notification.async_dismiss(self.hass, self._notification_id)

    @property
    def _notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_press_failed"

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
        r = self.request
        if r is not None and r.kind is Kind.HEAT and r.until is not None and r.until > now:
            times.append(r.until)
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
