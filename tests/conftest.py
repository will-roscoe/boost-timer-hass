"""Shared fixtures: a mock SwitchBot switch and a config entry pointing at it."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.boost_timer.const import CONF_BOT, CONF_NAME, CONF_PRESS_GAP, DOMAIN

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
