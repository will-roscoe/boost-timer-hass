from datetime import timedelta

from homeassistant.helpers import entity_registry as er

from custom_components.boost_timer.model import UnknownObs
from custom_components.boost_timer.planner import Request

from .test_controller import advance, now, setup

M = timedelta(minutes=1)
H = timedelta(hours=1)

IDS = [
    "switch.radiator_heating",
    "select.radiator_setting",
    "sensor.radiator_expires_at",
    "sensor.radiator_heating_until",
    "binary_sensor.radiator_heating",
    "binary_sensor.radiator_uncertain",
]


def state(hass, entity_id):
    return hass.states.get(entity_id).state


def secs(t):
    """A timestamp as a timestamp sensor's state shows it (whole seconds)."""
    return t.replace(microsecond=0).isoformat()


async def test_entities_created(hass, bot, entry):
    await setup(hass, entry)
    for entity_id in IDS:
        assert hass.states.get(entity_id) is not None, entity_id
    reg = er.async_get(hass)
    assert reg.async_get("binary_sensor.radiator_uncertain").entity_category == "diagnostic"
    assert hass.states.get("select.radiator_setting").attributes["options"] == [
        "off",
        "1h",
        "2h",
        "3h",
        "4h",
    ]


async def test_switch_turn_on_presses_and_shows_on(hass, bot, entry, freezer):
    c = await setup(hass, entry)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.radiator_heating"}, blocking=True
    )
    await hass.async_block_till_done()
    assert len(bot) == 4
    assert state(hass, "switch.radiator_heating") == "on"
    attrs = hass.states.get("switch.radiator_heating").attributes
    assert attrs["request"] == "on"
    assert attrs["presses_today"] == 4
    assert attrs["uncertain"] is False
    assert state(hass, "binary_sensor.radiator_heating") == "on"
    assert state(hass, "select.radiator_setting") == "4h"
    assert state(hass, "sensor.radiator_expires_at") == secs(c.model.expires_at)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.radiator_heating"}, blocking=True
    )
    await hass.async_block_till_done()
    assert state(hass, "switch.radiator_heating") == "off"


async def test_select_option_2h_from_off(hass, bot, entry, freezer):
    await setup(hass, entry)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.radiator_setting", "option": "2h"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert len(bot) == 2
    assert state(hass, "select.radiator_setting") == "2h"
    assert state(hass, "switch.radiator_heating") == "off"


async def test_select_follows_countdown(hass, bot, entry, freezer):
    await setup(hass, entry)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.radiator_setting", "option": "2h"},
        blocking=True,
    )
    await hass.async_block_till_done()
    await advance(hass, freezer, 61 * M)
    assert state(hass, "select.radiator_setting") == "1h"
    assert len(bot) == 2
    await advance(hass, freezer, H)
    assert state(hass, "select.radiator_setting") == "off"
    assert state(hass, "binary_sensor.radiator_heating") == "off"
    assert state(hass, "sensor.radiator_expires_at") == "unknown"


async def test_heating_until_shows_request_end(hass, bot, entry, freezer):
    c = await setup(hass, entry)
    await c.submit(Request.heat_until(now() + 90 * M, now()))
    await hass.async_block_till_done()
    assert state(hass, "sensor.radiator_heating_until") == secs(now() + 90 * M)


async def test_uncertain_binary_sensor_reflects_model(hass, bot, entry):
    c = await setup(hass, entry)
    assert state(hass, "binary_sensor.radiator_uncertain") == "off"
    await c.observe(UnknownObs(), "test")
    await hass.async_block_till_done()
    assert state(hass, "binary_sensor.radiator_uncertain") == "on"
