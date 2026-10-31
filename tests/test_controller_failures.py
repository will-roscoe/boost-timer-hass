from datetime import timedelta

from homeassistant.components.persistent_notification import _async_get_or_create_notifications
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
