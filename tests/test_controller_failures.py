import asyncio
from dataclasses import replace
from datetime import timedelta

from homeassistant.components.persistent_notification import _async_get_or_create_notifications
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.boost_timer.planner import Kind, Request

from .conftest import BOT
from .test_controller import advance, now, setup

M = timedelta(minutes=1)


def scripted_bot(hass, fail_on=(), on_call=None):
    """Register button.press that raises on the given 1-based call numbers.

    :returns: the list of calls made (including failed ones).
    """
    hass.states.async_set(BOT, "unknown")
    calls = []

    async def handler(call):
        calls.append(call)
        if on_call:
            on_call(len(calls))
        if len(calls) in fail_on:
            raise HomeAssistantError("bluetooth timeout")

    hass.services.async_register("button", "press", handler)
    return calls


def notification_id(entry):
    return f"boost_timer_{entry.entry_id}_press_failed"


async def test_unavailable_bot_retries_without_uncertain(hass, bot, entry, freezer):
    hass.states.async_set(BOT, "unavailable")
    c = await setup(hass, entry)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    assert len(bot) == 0
    assert c.model.uncertain is False
    assert c.model.setting(now(), c.options) == 0

    hass.states.async_set(BOT, "unknown")
    await advance(hass, freezer, 5 * M)
    assert len(bot) == 4
    assert c.model.uncertain is False


async def test_failure_mid_burst_marks_uncertain(hass, entry, freezer):
    calls = scripted_bot(hass, fail_on={2})
    failed = async_capture_events(hass, "boost_timer_press_failed")
    c = await setup(hass, entry)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    assert len(calls) == 2
    assert c.model.setting(now(), c.options) == 1
    assert c.model.uncertain is True
    assert len(failed) == 1
    assert failed[0].data["press_index"] == 2
    assert failed[0].data["entry_id"] == entry.entry_id


async def test_failure_on_first_press_marks_uncertain_without_model_change(hass, entry, freezer):
    calls = scripted_bot(hass, fail_on={1})
    c = await setup(hass, entry)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert c.model.setting(now(), c.options) == 0
    assert c.model.uncertain is True


async def test_slow_press_stops_burst(hass, entry, freezer):
    def slow_second(n):
        if n == 2:
            freezer.tick(timedelta(seconds=4.5))

    calls = scripted_bot(hass, on_call=slow_second)
    c = await setup(hass, entry)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    assert len(calls) == 2
    assert c.model.setting(now(), c.options) == 2
    assert c.model.uncertain is True


async def test_notification_after_repeated_failures(hass, entry, freezer):
    calls = scripted_bot(hass, fail_on={1, 2})
    c = await setup(hass, entry)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    assert notification_id(entry) not in _async_get_or_create_notifications(hass)

    await advance(hass, freezer, 5 * M)
    assert len(calls) == 2
    assert notification_id(entry) in _async_get_or_create_notifications(hass)
    assert c.request.kind is Kind.HEAT

    await advance(hass, freezer, 5 * M)
    assert len(calls) == 6
    assert notification_id(entry) not in _async_get_or_create_notifications(hass)


async def test_flip_back_presses_nothing(hass, bot, entry, freezer):
    c = await setup(hass, entry)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    assert len(bot) == 4

    await advance(hass, freezer, M)
    await c.submit(Request.off(now()))
    await hass.async_block_till_done()
    assert c.request.kind is Kind.OFF
    assert len(bot) == 4

    await advance(hass, freezer, M)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    await advance(hass, freezer, 10 * M)
    assert len(bot) == 4
    assert c.model.setting(now(), c.options) == 4


async def test_deferred_off_executes_after_interval(hass, bot, entry, freezer):
    start = dt_util.utcnow()
    c = await setup(hass, entry)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    await advance(hass, freezer, M)
    await c.submit(Request.off(now()))
    await hass.async_block_till_done()
    assert len(bot) == 4

    await advance(hass, freezer, start + 5 * M - dt_util.utcnow())
    assert len(bot) == 5
    assert c.model.setting(now(), c.options) == 0


async def test_burst_returning_to_off_does_not_loop(hass, bot, entry, freezer):
    c = await setup(hass, entry)
    c.options = replace(c.options, on_setting=5)  # bypasses validation, as a defence test
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    for _ in range(7):
        await advance(hass, freezer, timedelta(seconds=10))
    assert len(bot) == 5
    assert c.model.uncertain is True


async def test_unexpected_exception_marks_uncertain_and_retries(hass, entry, freezer):
    hass.states.async_set(BOT, "unknown")
    calls = []

    async def handler(call):
        calls.append(call)
        if len(calls) == 2:
            raise TimeoutError

    hass.services.async_register("button", "press", handler)
    failed = async_capture_events(hass, "boost_timer_press_failed")
    c = await setup(hass, entry)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    assert len(calls) == 2
    assert c.model.uncertain is True
    assert len(failed) == 1
    # not stalled: the model reads 1h running, so "On" re-arms when that hour is up
    await advance(hass, freezer, 61 * M)
    assert len(calls) == 6


async def test_set_abandoned_after_retries(hass, bot, entry, freezer):
    hass.states.async_set(BOT, "unavailable")
    c = await setup(hass, entry)
    await c.submit(Request.set(3, now()))
    await hass.async_block_till_done()
    await advance(hass, freezer, 5 * M)
    assert c.request is None
    hass.states.async_set(BOT, "unknown")
    await advance(hass, freezer, 60 * M)
    assert len(bot) == 0


async def test_repeated_switch_turn_on_keeps_deadline_and_backoff(hass, entry, freezer):
    calls = scripted_bot(hass, fail_on=set(range(1, 100)))
    c = await setup(hass, entry)
    c.options = replace(c.options, max_duration=timedelta(minutes=90))
    turn_on = {"entity_id": "switch.radiator_heating"}
    await hass.services.async_call("switch", "turn_on", turn_on, blocking=True)
    await hass.async_block_till_done()
    until = c.request.until
    assert len(calls) == 1
    await advance(hass, freezer, M)
    await hass.services.async_call("switch", "turn_on", turn_on, blocking=True)
    await hass.async_block_till_done()
    assert c.request.until == until
    assert len(calls) == 1
    await advance(hass, freezer, 4 * M)
    assert len(calls) == 2


async def test_startup_waits_for_home_assistant_started(hass, bot, entry, freezer):
    c = await setup(hass, entry)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.tick(timedelta(hours=5))
    hass.set_state(CoreState.not_running)
    await setup(hass, entry)
    assert len(bot) == 4
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert len(bot) == 8


def slow_first_press(hass, freezer, delay):
    """A Bot whose first press takes ``delay`` to land, as when its connection has to be rebuilt."""
    return scripted_bot(hass, on_call=lambda n: freezer.tick(delay) if n == 1 else None)


async def test_timer_expiring_during_slow_first_press_recounts_burst(hass, entry, freezer):
    """Planned as on → off → 1 → 2 → 3, but the first press lands on a timer already off."""
    calls = slow_first_press(hass, freezer, timedelta(seconds=90))
    c = await setup(hass, entry)
    c.model.expires_at = now() + timedelta(seconds=30)
    await c.submit(Request.set(3, now()))
    await hass.async_block_till_done()
    assert len(calls) == 3
    assert c.model.setting(now(), c.options) == 3
    assert c.model.uncertain is False


async def test_off_landing_after_expiry_presses_off_again_after_the_gap(hass, entry, freezer):
    """The late press turned a finished timer back on, so one more press after the gap stops it."""
    calls = slow_first_press(hass, freezer, timedelta(seconds=90))
    c = await setup(hass, entry)
    c.model.expires_at = now() + timedelta(seconds=30)
    await c.submit(Request.off(now()))
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert c.model.setting(now(), c.options) == 1
    assert c.request is not None

    await advance(hass, freezer, timedelta(seconds=7))
    assert len(calls) == 2
    assert c.model.setting(now(), c.options) == 0
    assert c.request is None


async def test_stop_does_not_wait_for_a_press_stuck_connecting(hass, entry):
    hass.states.async_set(BOT, "unknown")
    release = asyncio.Event()

    async def stuck(call):
        await release.wait()

    hass.services.async_register("button", "press", stuck)
    c = await setup(hass, entry)
    await c.submit(Request.set(1, now()))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    async with asyncio.timeout(2):
        await c.async_stop()
    assert c.model.uncertain is True
