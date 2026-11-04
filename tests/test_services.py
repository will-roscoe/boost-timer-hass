from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.boost_timer.const import CONF_BOT, CONF_NAME, DOMAIN

from .test_controller import advance, now, setup
from .test_entities import secs, state

M = timedelta(minutes=1)


async def call(hass, service, data):
    await hass.services.async_call(DOMAIN, service, data, blocking=True)
    await hass.async_block_till_done()


async def test_heat_for_90min_from_off(hass, bot, entry, freezer):
    await setup(hass, entry)
    await call(hass, "heat_for", {"duration": {"minutes": 90}})
    assert len(bot) == 2
    assert state(hass, "sensor.radiator_heating_until") == secs(now() + 90 * M)
    assert state(hass, "switch.radiator_heating") == "on"


async def test_heat_until_time(hass, bot, entry, freezer):
    await setup(hass, entry)
    await call(hass, "heat_for", {"until": (now() + 55 * M).isoformat()})
    assert len(bot) == 1


async def test_heat_for_requires_one_of(hass, bot, entry):
    await setup(hass, entry)
    with pytest.raises(vol.Invalid):
        await call(hass, "heat_for", {})
    with pytest.raises(vol.Invalid):
        await call(hass, "heat_for", {"duration": {"minutes": 5}, "until": now().isoformat()})


async def test_sync_sets_model_without_presses(hass, bot, entry, freezer):
    await setup(hass, entry)
    await call(hass, "sync", {"setting": 3})
    assert len(bot) == 0
    assert state(hass, "select.radiator_setting") == "3h"


async def test_sync_with_remaining(hass, bot, entry, freezer):
    c = await setup(hass, entry)
    await call(hass, "sync", {"setting": 2, "remaining": {"minutes": 70}})
    assert c.model.expires_at == now() + 70 * M


async def test_sync_rejects_setting_above_steps(hass, bot, entry):
    await setup(hass, entry)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "sync", {"setting": 5})
    assert err.value.translation_key == "setting_out_of_range"


async def test_sync_off_during_on_rearms_once(hass, bot, entry, freezer):
    await setup(hass, entry)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.radiator_heating"}, blocking=True
    )
    await hass.async_block_till_done()
    assert len(bot) == 4
    await advance(hass, freezer, M)  # Will notices the LEDs are off a minute later
    await call(hass, "sync", {"setting": 0})
    assert len(bot) == 8
    await advance(hass, freezer, timedelta(seconds=10))
    assert len(bot) == 8


async def test_entry_id_required_with_two_entries(hass, bot, entry):
    hass.states.async_set("button.other_bot", "unknown")
    other = MockConfigEntry(domain=DOMAIN, data={CONF_NAME: "Other", CONF_BOT: "button.other_bot"})
    other.add_to_hass(hass)
    await setup(hass, entry)  # loading the integration sets up both entries
    assert other.state is ConfigEntryState.LOADED
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "sync", {"setting": 1})
    assert err.value.translation_key == "entry_required"
    await call(hass, "sync", {"setting": 1, "config_entry_id": other.entry_id})
    assert other.runtime_data.model.setting(now(), other.runtime_data.options) == 1
    assert entry.runtime_data.model.setting(now(), entry.runtime_data.options) == 0


async def test_heat_for_55min_keeps_request_until_end(hass, bot, entry, freezer):
    await setup(hass, entry)
    await call(hass, "heat_for", {"duration": {"minutes": 55}})
    assert len(bot) == 1
    assert state(hass, "switch.radiator_heating") == "on"
    assert state(hass, "sensor.radiator_heating_until") == secs(now() + 55 * M)
    await advance(hass, freezer, 56 * M)
    assert state(hass, "switch.radiator_heating") == "off"


async def test_sync_after_heat_for_replans(hass, bot, entry, freezer):
    await setup(hass, entry)
    await call(hass, "heat_for", {"duration": {"minutes": 55}})
    assert len(bot) == 1
    await advance(hass, freezer, M)
    await call(hass, "sync", {"setting": 0})
    assert len(bot) == 2
