"""Replay the radiator sensor recorded on 2 Nov 2026 (times UTC).

Known switches that day: off 05:09 (the timer ran out), on about 05:29 (pressed by hand), off about
10:37 (pressed by Home Assistant), on 11:36.
"""

from datetime import datetime
import json
from pathlib import Path

import pytest

from custom_components.boost_timer.verifier import SENSOR_STEP, RadiatorDetector, Reading

FIXTURE = Path(__file__).parent / "fixtures" / "radiator_2026-11-02.json"
DAY = "2026-11-02T"


def t(hhmm):
    return datetime.fromisoformat(f"{DAY}{hhmm}:00+00:00")


def q(x):
    return round(x / SENSOR_STEP) * SENSOR_STEP


def replay(scale=1.0, room_shift=0.0, detector=None):
    data = json.loads(FIXTURE.read_text())
    room = [(datetime.fromisoformat(a), v) for a, v in data["room"]]
    d = detector or RadiatorDetector()
    events, last = [], None
    for at, value in ((datetime.fromisoformat(a), v) for a, v in data["radiator"]):
        r = next((v for ra, v in reversed(room) if ra <= at), room[0][1])
        rad = q(r + room_shift + scale * (value - r))
        if last is not None and abs(rad - last) < SENSOR_STEP - 1e-9:
            continue
        last = rad
        if (e := d.update(Reading(at, rad, r + room_shift))) is not None:
            events.append(e)
    return events


def minutes(a, b):
    return abs((a - b).total_seconds()) / 60


def test_replay_finds_the_four_switches():
    events = [e for e in replay() if not e.initial]
    assert [e.heating for e in events] == [False, True, False, True]
    for e, detected, truth in zip(
        events,
        ["05:26", "05:39", "10:59", "11:55"],
        ["05:09", "05:29", "10:37", "11:36"],
        strict=True,
    ):
        assert minutes(e.detected_at, t(detected)) <= 1
        assert minutes(e.at, t(truth)) <= 10
        assert e.lo <= e.at <= e.hi


def test_replay_initial_event_is_heating():
    first = replay()[0]
    assert first.initial and first.heating
    assert first.detected_at <= t("04:45")


@pytest.mark.parametrize(
    ("scale", "room_shift"), [(0.5, 0), (0.3, 0), (1.0, 8), (1.0, -12), (0.3, -10)]
)
def test_replay_robust_to_lower_power_and_room_shift(scale, room_shift):
    base = [e for e in replay() if not e.initial]
    other = [e for e in replay(scale, room_shift) if not e.initial]
    assert [e.heating for e in other] == [e.heating for e in base]
    for a, b in zip(base, other, strict=True):
        assert minutes(a.detected_at, b.detected_at) <= 15


def test_replay_back_dates_offs_along_the_learned_curve():
    """With cooling learned from the recording, both offs are back-dated along the curve."""
    learned = RadiatorDetector()
    replay(detector=learned)
    events = replay(detector=RadiatorDetector.from_dict(learned.to_dict()))
    offs = [e for e in events if not e.heating and not e.initial]
    assert len(offs) == 2
    for e, truth in zip(offs, ["05:09", "10:37"], strict=True):
        assert minutes(e.at, t(truth)) <= 5
    # The curve placed at least one inside its window (the other was clamped to the window start).
    assert any(e.lo < e.at < e.hi for e in offs)
