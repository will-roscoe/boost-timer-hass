from datetime import timedelta

from homeassistant.components.persistent_notification import _async_get_or_create_notifications
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.boost_timer.planner import Request
from custom_components.boost_timer.sync import RadiatorSync
from custom_components.boost_timer.verifier import SwitchEvent

from .test_controller import advance, now, setup

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
