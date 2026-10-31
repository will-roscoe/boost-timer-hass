from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.boost_timer.press import BotUnavailable, PressAdapter, PressFailed

from .conftest import BOT


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
