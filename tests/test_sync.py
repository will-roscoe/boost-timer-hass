from datetime import timedelta

from homeassistant.components.persistent_notification import _async_get_or_create_notifications
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.boost_timer.const import (
    CONF_BOT,
    CONF_NAME,
    CONF_PRESS_GAP,
    CONF_RADIATOR_SENSOR,
    CONF_ROOM_SENSORS,
    DOMAIN,
)
from custom_components.boost_timer.planner import Request
from custom_components.boost_timer.sync import RadiatorSync
from custom_components.boost_timer.verifier import SwitchEvent

from .conftest import BOT
from .radiator_curves import series
from .test_controller import advance, now, restart, setup

M = timedelta(minutes=1)


def event(heating, at, lo=None, hi=None, detected=None, initial=False):
    return SwitchEvent(heating, at, lo or at, hi or at, detected or hi or at, initial)


async def sync_for(hass, entry):
    c = await setup(hass, entry)
    s = RadiatorSync(hass, entry, c)
    return c, s


async def test_ended_early_corrects_fires_event_and_rearms(hass, bot, entry, freezer):
    c, s = await sync_for(hass, entry)
    corrected = async_capture_events(hass, "boost_timer_corrected")
    t = now()
    await c.submit(Request.on(t, c.options))
    await hass.async_block_till_done()
    assert len(bot) == 4
    await advance(hass, freezer, 140 * M)
    kind = await s.apply(event(False, t + 120 * M, t + 112 * M, t + 130 * M), now())
    await hass.async_block_till_done()
    assert kind == "ended_early"
    assert len(corrected) == 1
    assert corrected[0].data["kind"] == "ended_early"
    assert len(bot) == 8  # the HEAT request re-armed from the corrected (off) model
    assert s.corrections_today == 1


async def test_natural_end_is_agreement_no_event(hass, bot, entry, freezer):
    c, s = await sync_for(hass, entry)
    corrected = async_capture_events(hass, "boost_timer_corrected")
    t = now()
    await c.submit(Request.set(1, t))
    await hass.async_block_till_done()
    await advance(hass, freezer, 80 * M)
    kind = await s.apply(event(False, t + 61 * M, t + 55 * M, t + 75 * M), now())
    assert kind == "agreement"
    assert corrected == []
    assert s.corrections_today == 0


async def test_suppressed_while_busy(hass, bot, entry, freezer):
    c, s = await sync_for(hass, entry)
    t = now()
    await c.submit(Request.set(2, t))
    await hass.async_block_till_done()
    await advance(hass, freezer, 2 * M)
    expires = c.model.expires_at
    assert await s.apply(event(False, t, t, t), now()) is None
    assert c.model.expires_at == expires


async def test_initial_event_ignored(hass, bot, entry):
    c, s = await sync_for(hass, entry)
    assert await s.apply(event(True, now() - 30 * M, initial=True), now()) is None
    assert c.model.expires_at is None


async def test_hand_press_adopted_without_pressing(hass, bot, entry, freezer):
    c, s = await sync_for(hass, entry)
    kind = await s.apply(event(True, now() - 20 * M), now())
    await hass.async_block_till_done()
    assert kind == "adopted"
    assert len(bot) == 0
    assert c.request is None
    assert hass.states.get("binary_sensor.radiator_heating").state == "on"
    assert hass.states.get("switch.radiator_heating").state == "off"


async def test_cap_freezes_and_notifies(hass, bot, entry, freezer):
    c, s = await sync_for(hass, entry)
    kinds = []
    for _ in range(3):
        start = now() - 20 * M
        kinds.append(await s.apply(event(True, start), now()))
        kinds.append(await s.apply(event(False, start + 10 * M), now()))
        await advance(hass, freezer, 60 * M)
    assert kinds == ["adopted", "ended_early"] * 3
    assert s.frozen is True
    assert await s.apply(event(True, now() - 20 * M), now()) is None
    assert f"boost_timer_{entry.entry_id}_sync_frozen" in _async_get_or_create_notifications(hass)


async def test_corrections_option_off_applies_nothing(hass, bot, entry):
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "sync_corrections": False}
    )
    c, s = await sync_for(hass, entry)
    assert await s.apply(event(True, now() - 20 * M), now()) is None
    assert c.model.expires_at is None


# ---- following the sensors ----------------------------------------------------------------

RAD, ROOM, ROOM2 = "sensor.radiator", "sensor.room", "sensor.room_2"


@pytest.fixture
def sync_entry(hass):
    """The "Radiator" timer with radiator sync on: one radiator and two room sensors."""
    hass.states.async_set(ROOM, "20.0")
    hass.states.async_set(ROOM2, "20.0")
    hass.states.async_set(RAD, "20.0")
    e = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NAME: "Radiator", CONF_BOT: BOT},
        options={CONF_PRESS_GAP: 0, CONF_RADIATOR_SENSOR: RAD, CONF_ROOM_SENSORS: [ROOM, ROOM2]},
    )
    e.add_to_hass(hass)
    return e


async def feed(hass, freezer, readings):
    for r in readings:
        freezer.move_to(r.at)
        hass.states.async_set(RAD, f"{r.radiator:.2f}")
        await hass.async_block_till_done()


async def test_setup_without_sensor_is_v1(hass, bot, entry):
    c = await setup(hass, entry)
    assert c.sync is None
    assert hass.states.get("binary_sensor.radiator_verified_heating") is None


async def test_end_to_end_cooling_corrects_model(hass, bot, sync_entry, freezer):
    c = await setup(hass, sync_entry)
    corrected = async_capture_events(hass, "boost_timer_corrected")
    t = now()
    await c.submit(Request.on(t, c.options))
    await hass.async_block_till_done()
    rs = c.model.run_started_at
    # Cooling stops short of 45 min after the re-arm, which would then (rightly) never start.
    await feed(hass, freezer, series(t, 20.0, [(True, 125), (False, 40)]))
    assert [e.data["kind"] for e in corrected] == ["ended_early"]
    assert corrected[0].data["new_expires_at"] == (rs + 120 * M).isoformat()


async def test_unavailable_and_non_numeric_readings_ignored(hass, bot, sync_entry, freezer):
    c = await setup(hass, sync_entry)
    before = c.sync.detector.last_reading_at
    for value in ("unavailable", "unknown", "abc"):
        freezer.tick(M)
        hass.states.async_set(RAD, value)
        await hass.async_block_till_done()
    assert c.sync.detector.last_reading_at == before


async def test_room_average_skips_unavailable_and_all_unavailable_is_blind(
    hass, bot, sync_entry, freezer
):
    c = await setup(hass, sync_entry)
    hass.states.async_set(ROOM, "18.0")
    hass.states.async_set(ROOM2, "unavailable")
    freezer.tick(M)
    hass.states.async_set(RAD, "30.0")
    await hass.async_block_till_done()
    assert c.sync.room == pytest.approx(18.0)
    assert not c.sync.blind(now())
    hass.states.async_set(ROOM, "unknown")
    await hass.async_block_till_done()
    assert c.sync.room is None
    assert c.sync.blind(now())
    assert c.sync.state is None


async def test_never_started_after_deadline_rearms_on_heat(hass, bot, sync_entry, freezer):
    c = await setup(hass, sync_entry)
    t = now()
    await c.submit(Request.on(t, c.options))
    await hass.async_block_till_done()
    assert len(bot) == 4
    for i in range(1, 6):  # flat at room temperature, alternating so HA records a change
        freezer.move_to(t + 10 * i * M)
        hass.states.async_set(RAD, "20.2" if i % 2 else "20.0")
        await hass.async_block_till_done()
    assert c.sync.last_correction["kind"] == "never_started"
    assert len(bot) == 8


async def test_never_started_waits_through_silence(hass, bot, sync_entry, freezer):
    c = await setup(hass, sync_entry)
    t = now()
    await c.submit(Request.on(t, c.options))
    await hass.async_block_till_done()
    await advance(hass, freezer, 90 * M)
    assert c.sync.last_correction is None
    assert len(bot) == 4


async def test_blind_after_an_hour_and_notified_after_a_day(hass, bot, sync_entry, freezer):
    c = await setup(hass, sync_entry)
    notification = f"boost_timer_{sync_entry.entry_id}_sync_blind"
    await advance(hass, freezer, 61 * M)
    assert c.sync.blind(now())
    assert c.sync.state is None
    for _ in range(25):
        await advance(hass, freezer, 60 * M)
    assert notification in _async_get_or_create_notifications(hass)
    hass.states.async_set(RAD, "21.0")
    await hass.async_block_till_done()
    assert notification not in _async_get_or_create_notifications(hass)
    assert not c.sync.blind(now())


async def test_detector_survives_reload(hass, bot, sync_entry, freezer):
    c = await setup(hass, sync_entry)
    await feed(hass, freezer, series(now(), 20.0, [(True, 120), (False, 90), (True, 60)]))
    learned = c.sync.detector.to_dict()
    assert learned["stretches"]
    c = await restart(hass, sync_entry)
    assert c.sync.detector.to_dict() == learned
    count = c.sync.corrections_today
    freezer.tick(M)
    hass.states.async_set(RAD, "35.0")
    await hass.async_block_till_done()
    assert c.sync.corrections_today == count
