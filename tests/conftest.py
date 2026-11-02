"""Shared fixtures: a mock SwitchBot, a config entry pointing at it, and one with radiator sync."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.boost_timer.const import (
    CONF_BOT,
    CONF_NAME,
    CONF_PRESS_GAP,
    CONF_RADIATOR_SENSOR,
    CONF_ROOM_SENSORS,
    DOMAIN,
)

BOT = "button.bot_5616"
#: The Bot as a switch (its real form); only used by tests that do not load the integration.
SWITCH_BOT = "switch.bot_5616"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/ in every test."""
    yield


@pytest.fixture
def bot(hass):
    """The SwitchBot as a button whose presses are recorded, not executed.

    A button rather than a switch, because the integration's own switch platform would replace a
    mocked ``switch.turn_on``. Returns the list of recorded ``button.press`` ServiceCalls.
    """
    hass.states.async_set(BOT, "unknown")
    return async_mock_service(hass, "button", "press")


@pytest.fixture
def entry(hass):
    """A config entry for a timer named "Radiator" driven by the mock bot, added to hass."""
    e = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NAME: "Radiator", CONF_BOT: BOT},
        options={CONF_PRESS_GAP: 0},  # no real waiting between presses
    )
    e.add_to_hass(hass)
    return e


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
