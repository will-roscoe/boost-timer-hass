"""Shared fixtures: a mock SwitchBot switch and a config entry pointing at it."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.boost_timer.const import CONF_BOT, CONF_NAME, DOMAIN

BOT = "switch.bot_5616"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/ in every test."""
    yield


@pytest.fixture
def bot(hass):
    """The SwitchBot as a switch whose turn_on/turn_off calls are recorded, not executed.

    Returns a dict ``{"turn_on": calls, "turn_off": calls}`` of recorded ServiceCall lists.
    """
    hass.states.async_set(BOT, "off")
    return {
        "turn_on": async_mock_service(hass, "switch", "turn_on"),
        "turn_off": async_mock_service(hass, "switch", "turn_off"),
    }


@pytest.fixture
def entry(hass):
    """A config entry for a timer named "Radiator" driven by the mock bot, added to hass."""
    e = MockConfigEntry(domain=DOMAIN, data={CONF_NAME: "Radiator", CONF_BOT: BOT}, options={})
    e.add_to_hass(hass)
    return e
