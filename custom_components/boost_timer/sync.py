"""Radiator sync: correct the timer model from a temperature sensor on the radiator (HA layer).

:class:`RadiatorSync` feeds the radiator and room sensors to a
:class:`~.verifier.RadiatorDetector` and passes each detected switch to the controller as a
back-dated :class:`~.model.HeatingObs`. It never presses: any press still comes from the active
request through the planner. Guards: no corrections while a burst runs or just after a press, none
when the ``sync_corrections`` option is off, and at most :data:`~.const.CORRECTION_CAP` a day.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
import logging
import math
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    EventStateReportedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    BLIND_AFTER,
    BLIND_NOTIFY_AFTER,
    CONF_RADIATOR_SENSOR,
    CONF_ROOM_SENSORS,
    CONF_SYNC_CORRECTIONS,
    CORRECTION_CAP,
    DEFAULTS,
    DOMAIN,
    EVENT_CORRECTED,
    NEVER_STARTED_AFTER,
)
from .model import HeatingObs
from .verifier import RadiatorDetector, Reading, SwitchEvent

if TYPE_CHECKING:
    from .controller import BoostTimerController

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
#: How often blindness is checked when no readings arrive.
CHECK_INTERVAL = timedelta(minutes=10)


def _number(hass: HomeAssistant, entity_id: str) -> float | None:
    """Return the entity's state as a finite number, or None if missing, unavailable or not one."""
    state = hass.states.get(entity_id)
    if state is None:
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _local_date(t: datetime) -> date:
    return dt_util.as_local(t).date()


class RadiatorSync:
    """Keep one timer's model in sync with its radiator.

    :ivar detector: the pure detector fed with readings.
    :ivar room: the latest mean of the room sensors that read a number, or None.
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
        self._radiator: str = entry.options.get(CONF_RADIATOR_SENSOR, "")
        self._rooms: list[str] = list(entry.options.get(CONF_ROOM_SENSORS, []))
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.detector"
        )
        self._unsubs: list[CALLBACK_TYPE] = []
        self.room: float | None = None
        self._started_at = dt_util.utcnow()
        self._blind_notified = False
        self._never_started_for: datetime | None = None
        self._heating_seen_at: datetime | None = None  # last reading the detector called heating

    # ---- sensors ----------------------------------------------------------------------------

    async def async_start(self) -> None:
        """Restore the learned detector, follow the sensors, and take a first reading."""
        data = await self._store.async_load()
        if data:
            self.detector = RadiatorDetector.from_dict(data)
        self._started_at = dt_util.utcnow()
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [self._radiator, *self._rooms], self._on_state_change
            )
        )
        # An unchanged value is still a reading: the sensor reports at least hourly.
        self._unsubs.append(
            async_track_state_report_event(self.hass, [self._radiator], self._on_state_report)
        )
        self._unsubs.append(async_track_time_interval(self.hass, self._on_interval, CHECK_INTERVAL))
        state = self.hass.states.get(self._radiator)
        await self.reading(state.last_updated if state else dt_util.utcnow())

    async def async_stop(self) -> None:
        """Stop following the sensors and save what the detector has learned."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(self.detector.to_dict())

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if event.data["entity_id"] == self._radiator and new is not None:
            self.hass.async_create_task(self.reading(new.last_updated), f"{DOMAIN} reading")
        else:
            self.room = self._room_value()
            self._notify()

    @callback
    def _on_state_report(self, event: Event[EventStateReportedData]) -> None:
        self.hass.async_create_task(
            self.reading(event.data["new_state"].last_reported), f"{DOMAIN} reading"
        )

    def _room_value(self) -> float | None:
        values = [v for e in self._rooms if (v := _number(self.hass, e)) is not None]
        return sum(values) / len(values) if values else None

    async def reading(self, at: datetime) -> None:
        """Read the sensors' current states as a reading taken at ``at`` and act on it.

        A radiator or room value that is missing, unavailable or not a number is no reading.
        """
        self.room = self._room_value()
        radiator = _number(self.hass, self._radiator)
        if radiator is None or self.room is None:
            self._notify()
            return
        if self._blind_notified:
            self._blind_notified = False
            persistent_notification.async_dismiss(self.hass, self._blind_notification_id)
        event = self.detector.update(Reading(at, radiator, self.room))
        if self.detector.state is True:
            self._heating_seen_at = at
        now = dt_util.utcnow()
        if event is not None:
            await self.apply(event, now)
        await self._check_never_started(now)
        await self._extend_adopted(now)
        self._store.async_delay_save(self.detector.to_dict, 30)
        self._notify()

    def blind(self, now: datetime) -> bool:
        """Return True when the detector can't be trusted: no radiator reading within
        :data:`~.const.BLIND_AFTER`, or no room value."""
        last = self.detector.last_reading_at
        return self.room is None or last is None or now - last > BLIND_AFTER

    @property
    def state(self) -> bool | None:
        """The detector's view: True heating, False not, None undecided or blind."""
        if self.blind(dt_util.utcnow()) or not self.detector.decided:
            return None
        return self.detector.state

    @property
    def _blind_notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_sync_blind"

    async def _extend_adopted(self, now: datetime) -> None:
        """Extend an adopted hand-press run while the radiator is seen heating near its end."""
        if self.controller.model.adopted and self.state is True:
            await self.controller.extend_adopted(now)

    @callback
    def _on_interval(self, now: datetime) -> None:
        self.hass.async_create_task(self._extend_adopted(now), f"{DOMAIN} extend")
        if self.blind(now) and not self._blind_notified:
            since = self.detector.last_reading_at or self._started_at
            if now - since >= BLIND_NOTIFY_AFTER:
                self._blind_notified = True
                persistent_notification.async_create(
                    self.hass,
                    f"{self.entry.title}: no usable reading from {self._radiator} or the room "
                    "sensors for over a day, so radiator sync is not checking the timer.",
                    title="Boost Timer",
                    notification_id=self._blind_notification_id,
                )
        self._notify()

    async def _check_never_started(self, now: datetime) -> None:
        """Correct a run whose radiator showed no heating by :data:`NEVER_STARTED_AFTER`.

        Not for a run adopted from detected heating, or one the detector saw heating since it began.
        """
        model = self.controller.model
        rs = model.run_started_at
        if rs is None or rs == self._never_started_for or model.adopted or self.blind(now):
            return
        if self._heating_seen_at is not None and self._heating_seen_at >= rs:
            return
        if model.setting(now, self.controller.options) == 0:
            return
        deadline = rs + NEVER_STARTED_AFTER
        last = self.detector.last_reading_at
        if now < deadline or last is None or last < deadline or not self.detector.cold():
            return
        self._never_started_for = rs
        await self.apply(SwitchEvent(False, rs, rs, rs, now, False), now)

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
