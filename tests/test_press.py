from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.boost_timer.press import (
    PRESS_TIMEOUT,
    BotUnavailable,
    PressAdapter,
    PressFailed,
)

from .conftest import SWITCH_BOT as BOT


@pytest.fixture
def bot(hass):
    """The Bot as a switch whose turn_on/turn_off calls are recorded."""
    hass.states.async_set(BOT, "off")
    return {
        "turn_on": async_mock_service(hass, "switch", "turn_on"),
        "turn_off": async_mock_service(hass, "switch", "turn_off"),
    }


async def test_turn_on_mode_calls_turn_on(hass, bot):
    await PressAdapter(hass, BOT, "turn_on").press()
    assert [c.data["entity_id"] for c in bot["turn_on"]] == [BOT]
    assert bot["turn_off"] == []


async def test_turn_off_mode_calls_turn_off(hass, bot):
    await PressAdapter(hass, BOT, "turn_off").press()
    assert len(bot["turn_off"]) == 1
    assert bot["turn_on"] == []


async def test_alternate_mode(hass, bot):
    hass.states.async_set(BOT, "on")
    await PressAdapter(hass, BOT, "alternate").press()
    assert len(bot["turn_off"]) == 1
    hass.states.async_set(BOT, "off")
    await PressAdapter(hass, BOT, "alternate").press()
    assert len(bot["turn_on"]) == 1


async def test_never_pressed_button_still_presses(hass):
    hass.states.async_set("button.bot", "unknown")
    calls = async_mock_service(hass, "button", "press")
    await PressAdapter(hass, "button.bot", "turn_on").press()
    assert len(calls) == 1


async def test_unavailable_button_raises(hass):
    hass.states.async_set("button.bot", "unavailable")
    with pytest.raises(BotUnavailable):
        await PressAdapter(hass, "button.bot", "turn_on").press()


async def test_button_entity_uses_press(hass):
    hass.states.async_set("button.bot", "2026-10-30T12:00:00+00:00")
    calls = async_mock_service(hass, "button", "press")
    await PressAdapter(hass, "button.bot", "turn_on").press()
    assert [c.data["entity_id"] for c in calls] == ["button.bot"]


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
async def test_unavailable_raises_bot_unavailable(hass, bot, state):
    hass.states.async_set(BOT, state)
    with pytest.raises(BotUnavailable):
        await PressAdapter(hass, BOT, "turn_on").press()
    assert bot["turn_on"] == []


async def test_missing_entity_raises_bot_unavailable(hass):
    with pytest.raises(BotUnavailable):
        await PressAdapter(hass, "switch.nope", "turn_on").press()


async def test_service_error_raises_press_failed(hass):
    hass.states.async_set(BOT, "off")

    async def fail(call):
        raise HomeAssistantError("bluetooth timeout")

    hass.services.async_register("switch", "turn_on", fail)
    with pytest.raises(PressFailed) as err:
        await PressAdapter(hass, BOT, "turn_on").press()
    assert not isinstance(err.value, BotUnavailable)


async def test_unexpected_exception_raises_press_failed(hass):
    hass.states.async_set(BOT, "off")

    async def fail(call):
        raise TimeoutError

    hass.services.async_register("switch", "turn_on", fail)
    with pytest.raises(PressFailed):
        await PressAdapter(hass, BOT, "turn_on").press()


def test_timeout_outlasts_the_slowest_seen_reconnect():
    """The Bot's library retries a dropped connection itself; 80 s and over 2 min were measured."""
    assert PRESS_TIMEOUT >= 180
