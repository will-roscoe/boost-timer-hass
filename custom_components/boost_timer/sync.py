"""Radiator sync: correct the timer model from a temperature sensor on the radiator (HA layer).

:class:`RadiatorSync` feeds the radiator and room sensors to a
:class:`~.verifier.RadiatorDetector` and passes each detected switch to the controller as a
back-dated :class:`~.model.HeatingObs`. It never presses: any press still comes from the active
request through the planner. Guards: no corrections while a burst runs or just after a press, none
when the ``sync_corrections`` option is off, and at most :data:`~.const.CORRECTION_CAP` a day.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import CONF_SYNC_CORRECTIONS, CORRECTION_CAP, DEFAULTS, DOMAIN, EVENT_CORRECTED
from .model import HeatingObs
from .verifier import RadiatorDetector, SwitchEvent

if TYPE_CHECKING:
    from .controller import BoostTimerController

_LOGGER = logging.getLogger(__name__)


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _local_date(t: datetime) -> date:
    return dt_util.as_local(t).date()


class RadiatorSync:
    """Keep one timer's model in sync with its radiator.

    :ivar detector: the pure detector fed with readings.
    :ivar last_correction: details of the last correction (``kind``, ``at``, ``lo``, ``hi``,
        ``detected_at``, ``old_expires_at``, ``new_expires_at``), or None.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        controller: BoostTimerController,
        detector: RadiatorDetector | None = None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.controller = controller
        self.detector = detector or RadiatorDetector()
        self.last_correction: dict[str, Any] | None = None
        self._corrections = 0
        self._corrections_date: date | None = None
        self._frozen_date: date | None = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def corrections_today(self) -> int:
        """Corrections made since local midnight."""
        if self._corrections_date != _local_date(dt_util.utcnow()):
            return 0
        return self._corrections

    @property
    def frozen(self) -> bool:
        """True once today's correction cap was reached; clears at local midnight."""
        return self._frozen_date == _local_date(dt_util.utcnow())

    @property
    def _corrections_on(self) -> bool:
        return bool(self.entry.options.get(CONF_SYNC_CORRECTIONS, DEFAULTS[CONF_SYNC_CORRECTIONS]))

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Call ``cb`` after every change; returns a function that removes it."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    async def apply(self, event: SwitchEvent, now: datetime) -> str | None:
        """Pass a detected switch to the model.

        :returns: what it did: ``"agreement"`` (nothing to correct), ``"adopted"`` (a hand press),
            ``"ended_early"``, ``"never_started"`` or ``"uncertain"`` (stopped, but the time is too
            close to a half step to round); None when not applied (the detector's first event, a
            burst running or a press just landed, corrections switched off, or frozen).
        """
        if event.initial or self.controller.busy(now) or not self._corrections_on or self.frozen:
            return None
        model = self.controller.model
        old_expires, run_started = model.expires_at, model.run_started_at
        changed = await self.controller.observe(
            HeatingObs(event.heating, event.at, event.lo, event.hi), source="verifier"
        )
        if not changed:
            return "agreement"
        if event.heating:
            kind = "adopted"
        elif model.uncertain:
            kind = "uncertain"
        elif model.expires_at is None and event.at == run_started:
            kind = "never_started"
        else:
            kind = "ended_early"
        self._record(kind, event, old_expires, model.expires_at, now)
        return kind

    def _record(
        self,
        kind: str,
        event: SwitchEvent,
        old: datetime | None,
        new: datetime | None,
        now: datetime,
    ) -> None:
        today = _local_date(now)
        if self._corrections_date != today:
            self._corrections, self._corrections_date = 0, today
        self._corrections += 1
        self.last_correction = {
            "kind": kind,
            "at": _iso(event.at),
            "lo": _iso(event.lo),
            "hi": _iso(event.hi),
            "detected_at": _iso(event.detected_at),
            "old_expires_at": _iso(old),
            "new_expires_at": _iso(new),
        }
        self.hass.bus.async_fire(
            EVENT_CORRECTED, {"entry_id": self.entry.entry_id, **self.last_correction}
        )
        _LOGGER.info("%s: radiator sync %s: %s", self.entry.title, kind, self.last_correction)
        if self._corrections >= CORRECTION_CAP:
            self._frozen_date = today
            _LOGGER.warning(
                "%s: %d radiator sync corrections today; corrections stop until midnight",
                self.entry.title,
                self._corrections,
            )
            persistent_notification.async_create(
                self.hass,
                f"{self.entry.title}: radiator sync corrected the timer {self._corrections} times "
                "today, so it has stopped correcting until midnight. Check the radiator sensor.",
                title="Boost Timer",
                notification_id=f"{DOMAIN}_{self.entry.entry_id}_sync_frozen",
            )
        self._notify()
