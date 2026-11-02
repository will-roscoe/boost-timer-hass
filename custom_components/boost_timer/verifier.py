"""Detect when a radiator starts and stops heating from a temperature sensor on it (pure Python).

A radiator that is off cools toward the room as ``d(gap)/dt = −c · gap^n`` (gap = radiator −
room; Newton's law of cooling is ``n = 1``, radiator ratings use about 1.3).
:class:`RadiatorDetector` checks each reading against where the radiator would be had it been off
since an earlier reading: clearly above that means heating. It learns ``c`` and ``n`` from this
radiator's own cooling, so it needs no temperature thresholds: the only temperature constant is the
sensor's reporting step.

Usage::

    detector = RadiatorDetector()
    for reading in readings:
        if (event := detector.update(reading)) is not None:
            ...  # event.heating switched at about event.at, somewhere in [event.lo, event.hi]
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Any

#: The radiator sensor's reporting step in °C: changes smaller than this are noise.
SENSOR_STEP = 0.2
#: Starting cooling constant (per minute, ``n = 1``), slower than any real radiator. Too slow only
#: delays detection; too fast would invent heating.
C_PRIOR = 0.005
#: Readings older than this are dropped from the buffer.
BUFFER_SPAN = timedelta(hours=6)
#: Cooling stretches whose estimates are kept; decisions use the slowest of them.
STRETCHES_KEPT = 5
#: ``(gap, rate)`` pairs kept for fitting ``n``.
PAIRS_KEPT = 200
#: Learning looks back at most this far for a clear fall.
LEARN_WINDOW = timedelta(minutes=15)
#: Physically plausible range of the cooling exponent.
N_RANGE = (1.0, 2.0)


@dataclass(frozen=True)
class Reading:
    """One radiator reading with the room temperature at that time (°C)."""

    at: datetime
    radiator: float
    room: float


@dataclass(frozen=True)
class SwitchEvent:
    """The radiator started (``heating``) or stopped heating at about ``at``.

    :ivar at: best estimate of the switch, within ``[lo, hi]``.
    :ivar lo: the last reading before the switch was seen.
    :ivar hi: the reading that showed it (also ``detected_at``).
    :ivar initial: the detector had no state before, or was just restored from storage: this sets
        its state and is not a switch.
    """

    heating: bool
    at: datetime
    lo: datetime
    hi: datetime
    detected_at: datetime
    initial: bool


def off_gap(gap0: float, minutes: float, c: float, n: float) -> float:
    """Return the gap after cooling for ``minutes`` from ``gap0`` under ``d(gap)/dt = −c·gap^n``."""
    if gap0 <= 0:
        return gap0
    if n == 1:
        return gap0 * math.exp(-c * minutes)
    return (gap0 ** (1 - n) + (n - 1) * c * minutes) ** (1 / (1 - n))


def cooling_minutes(gap_from: float, gap_to: float, c: float, n: float) -> float:
    """Return the minutes cooling takes from ``gap_from`` down to ``gap_to`` (both > 0)."""
    if n == 1:
        return math.log(gap_from / gap_to) / c
    return (gap_to ** (1 - n) - gap_from ** (1 - n)) / ((n - 1) * c)


def fit_exponent(pairs: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    """Fit ``rate = c · gap^n`` to ``(gap, rate)`` pairs by least squares in log space.

    :returns: ``(c, n)``, or None with fewer than 8 usable pairs, gaps spanning less than 2x, or
        ``n`` outside :data:`N_RANGE`.
    """
    pts = [(math.log(g), math.log(r)) for g, r in pairs if g > 0 and r > 0]
    if len(pts) < 8:
        return None
    xs = [x for x, _ in pts]
    if max(xs) - min(xs) < math.log(2):
        return None
    mx = sum(xs) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    n = sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x in xs)
    if not N_RANGE[0] <= n <= N_RANGE[1]:
        return None
    return math.exp(my - n * mx), n


def _c_from(gap0: float, gap1: float, minutes: float, n: float) -> float:
    if n == 1:
        return math.log(gap0 / gap1) / minutes
    return (gap1 ** (1 - n) - gap0 ** (1 - n)) / ((n - 1) * minutes)


def _minutes(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60


class RadiatorDetector:
    """Turn radiator readings into heating / not-heating switches with back-dated times.

    :ivar state: True heating, False not, None undecided (no decision yet).
    :ivar n: the cooling exponent in use (1 until enough cooling has been seen to fit it).
    """

    def __init__(self, step: float = SENSOR_STEP, c_prior: float = C_PRIOR) -> None:
        """
        :param step: the sensor's reporting step in °C.
        :param c_prior: cooling constant used before any cooling has been learned.
        """
        self.step = step
        self.c_prior = c_prior
        self.state: bool | None = None
        self.n = 1.0
        self._stretches: list[float] = []  # latest c estimate per cooling stretch, newest last
        self._new_stretch = False
        self._since: datetime | None = None  # when the current state was detected
        self._restored = False  # restored from storage, no decision made since
        self._pairs: list[tuple[float, float]] = []
        self._buf: list[Reading] = []
        self._last_at: datetime | None = None

    @property
    def last_reading_at(self) -> datetime | None:
        """When the last accepted reading was taken."""
        return self._last_at

    @property
    def _c_decide(self) -> float:
        return min(self._stretches) if self._stretches else self.c_prior

    def update(self, reading: Reading) -> SwitchEvent | None:
        """Add a reading; return a :class:`SwitchEvent` if it shows heating started or stopped.

        A reading not newer than the last one is ignored.
        """
        if self._last_at is not None and reading.at <= self._last_at:
            return None
        self._last_at = reading.at
        self._buf.append(reading)
        cutoff = reading.at - BUFFER_SPAN
        while self._buf and self._buf[0].at < cutoff:
            self._buf.pop(0)
        event = self._decide(reading)
        if self.state is False:
            self._learn(reading)
        return event

    def _decide(self, r: Reading) -> SwitchEvent | None:
        gap = r.radiator - r.room
        c = self._c_decide
        for i in range(len(self._buf) - 2, -1, -1):
            ref = self._buf[i]
            gap0 = ref.radiator - r.room
            if gap0 <= 0:
                continue
            dt = _minutes(ref.at, r.at)
            predicted = off_gap(gap0, dt, c, self.n)
            if gap0 - predicted >= 2 * self.step:
                heating = gap - predicted > self.step
                if heating and self.state is False and not self._restored:
                    # Turning on from a known off needs the radiator to rise against the room from
                    # its lowest point since the reference, so a cold sensor that reads a little
                    # above the room average is not taken for heating. With no trusted state (fresh
                    # or just restored) a radiator far enough above the room to decide is heating.
                    low = min(b.radiator - b.room for b in self._buf[i:-1])
                    heating = gap - low >= 2 * self.step
                break
        else:
            return None
        restored, self._restored = self._restored, False
        if heating == self.state:
            return None
        initial = self.state is None or restored
        event = self._event(heating, initial, r)
        self.state = heating
        self._since = r.at
        self._new_stretch = not heating
        return event

    def _event(self, heating: bool, initial: bool, r: Reading) -> SwitchEvent:
        """Build the event, bracketing the switch from the radiator's turning point.

        For an off, ``lo`` is the last reading still at the peak (within a step of the highest
        gap since the last switch); for an on, the last reading at the low point. An off is then
        back-dated along the learned cooling curve from the peak gap to this reading's gap.
        """
        earlier = [b for b in self._buf[:-1] if self._since is None or b.at >= self._since]
        if not earlier:
            return SwitchEvent(heating, r.at, r.at, r.at, r.at, initial)
        gaps = [(b, b.radiator - r.room) for b in earlier]
        if heating:
            turn = min(g for _, g in gaps)
            lo = max(b.at for b, g in gaps if g <= turn + self.step)
        else:
            turn = max(g for _, g in gaps)
            lo = max(b.at for b, g in gaps if g >= turn - self.step)
        hi = r.at
        at = lo
        g_hi = r.radiator - r.room
        if not heating and self._stretches and turn > g_hi > 0:
            back = cooling_minutes(turn, g_hi, self._stretches[-1], self.n)
            at = min(max(hi - timedelta(minutes=back), lo), hi)
        return SwitchEvent(heating, at, lo, hi, r.at, initial)

    def _learn(self, r: Reading) -> None:
        gap1 = r.radiator - r.room
        if gap1 <= self.step:
            return
        for ref in reversed(self._buf[:-1]):
            dt = _minutes(ref.at, r.at)
            if dt > LEARN_WINDOW.total_seconds() / 60:
                return
            if ref.radiator - r.radiator >= 5 * self.step:
                gap0 = ref.radiator - r.room
                c = _c_from(gap0, gap1, dt, self.n)
                if c > 0:
                    if self._new_stretch or not self._stretches:
                        self._stretches = [*self._stretches, c][-STRETCHES_KEPT:]
                        self._new_stretch = False
                    else:
                        self._stretches[-1] = c
                self._pairs.append((math.sqrt(gap0 * gap1), (gap0 - gap1) / dt))
                self._pairs = self._pairs[-PAIRS_KEPT:]
                self._refit()
                return

    def _refit(self) -> None:
        fit = fit_exponent(self._pairs)
        if fit is None or abs(fit[1] - self.n) < 0.05:
            return
        self.n = fit[1]
        self._stretches = [fit[0]]

    def to_dict(self) -> dict[str, Any]:
        """Return the learned cooling and current state for storage (not the reading buffer)."""
        return {
            "state": self.state,
            "n": self.n,
            "stretches": list(self._stretches),
            "pairs": [list(p) for p in self._pairs],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> RadiatorDetector:
        """Rebuild a detector saved by :meth:`to_dict`, with an empty reading buffer.

        Its first event is marked ``initial``: it has nothing from before the restore to compare.
        """
        det = cls()
        det.state = d.get("state")
        det.n = float(d.get("n", 1.0))
        det._stretches = [float(c) for c in d.get("stretches", [])]
        det._pairs = [(float(g), float(r)) for g, r in d.get("pairs", [])]
        det._restored = True
        return det
