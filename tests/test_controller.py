from datetime import datetime, timedelta

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.boost_timer.planner import PRIORITY_AUTOMATIC, Request

from .conftest import BOT

H = timedelta(hours=1)


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def restart(hass, entry):
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def advance(hass, freezer, delta):
    freezer.tick(delta)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


def now():
    return dt_util.utcnow()


async def test_on_from_off_presses_four(hass, bot, entry, freezer):
    c = await setup(hass, entry)
    assert await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    assert len(bot) == 4
    assert c.model.setting(now(), c.options) == 4
    assert c.presses_today == 4
    assert c.last_press == now()


async def test_state_saved_after_each_press(hass, entry):
    hass.states.async_set(BOT, "unknown")
    calls = []

    async def flaky(call):
        calls.append(call)
        if len(calls) == 3:
            raise HomeAssistantError("bluetooth timeout")

    hass.services.async_register("button", "press", flaky)
    c = await setup(hass, entry)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    assert len(calls) == 3

    c = await restart(hass, entry)
    assert c.model.setting(now(), c.options) == 2
    assert c.request is not None and c.request.label == "on"


async def test_rearm_at_expiry(hass, bot, entry, freezer):
    c = await setup(hass, entry)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    assert len(bot) == 4
    await advance(hass, freezer, 4 * H - timedelta(seconds=1))
    assert len(bot) == 4
    await advance(hass, freezer, timedelta(seconds=2))
    assert len(bot) == 8
    assert c.model.setting(now(), c.options) == 4


async def test_restore_after_restart_with_expiry_while_down(hass, bot, entry, freezer):
    c = await setup(hass, entry)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.tick(5 * H)
    c = await setup(hass, entry)
    assert len(bot) == 8
    assert c.model.setting(now(), c.options) == 4


async def test_set_request_is_one_off(hass, bot, entry, freezer):
    c = await setup(hass, entry)
    await c.submit(Request.set(2, now()))
    await hass.async_block_till_done()
    assert len(bot) == 2
    assert c.request is None
    await advance(hass, freezer, 3 * H)
    assert len(bot) == 2
    assert c.model.setting(now(), c.options) == 0


async def test_heating_events(hass, bot, entry, freezer):
    started = async_capture_events(hass, "boost_timer_heating_started")
    stopped = async_capture_events(hass, "boost_timer_heating_stopped")
    c = await setup(hass, entry)
    await c.submit(Request.set(1, now()))
    await hass.async_block_till_done()
    assert len(started) == 1
    assert started[0].data["entry_id"] == entry.entry_id
    assert started[0].data["request"] == "manual"
    assert c.session_started_at == now()
    await advance(hass, freezer, H + timedelta(seconds=1))
    assert len(stopped) == 1
    assert stopped[0].data["request"] is None
    assert c.session_started_at is None


async def test_presses_today_resets_at_local_midnight(hass, bot, entry, freezer):
    local_late = datetime(2026, 10, 30, 23, 50, tzinfo=dt_util.get_default_time_zone())
    freezer.move_to(local_late)
    c = await setup(hass, entry)
    await c.submit(Request.set(2, now()))
    await hass.async_block_till_done()
    assert c.presses_today == 2
    await advance(hass, freezer, timedelta(minutes=20))
    assert c.presses_today == 0
    await c.submit(Request.off(now()))
    await hass.async_block_till_done()
    assert c.presses_today == 1


async def test_automatic_request_rejected_while_manual_active(hass, bot, entry):
    c = await setup(hass, entry)
    await c.submit(Request.on(now(), c.options))
    await hass.async_block_till_done()
    assert not await c.submit(Request.off(now(), source="thermostat", priority=PRIORITY_AUTOMATIC))
    assert c.request.label == "on"
    assert await c.submit(Request.off(now()))


async def test_listeners_called_on_change(hass, bot, entry):
    c = await setup(hass, entry)
    seen = []
    remove = c.add_listener(lambda: seen.append(1))
    await c.submit(Request.set(1, now()))
    await hass.async_block_till_done()
    assert seen
    remove()
    count = len(seen)
    await c.submit(Request.off(now()))
    await hass.async_block_till_done()
    assert len(seen) == count
