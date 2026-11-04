from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.boost_timer.const import (
    CONF_BOT,
    CONF_BURST_MARGIN,
    CONF_END_TOLERANCE,
    CONF_MAX_DURATION,
    CONF_MIN_SWITCH_INTERVAL,
    CONF_NAME,
    CONF_ON_SETTING,
    CONF_PRESS_GAP,
    CONF_PRESS_RETRIES,
    CONF_PRESS_SERVICE,
    CONF_RADIATOR_SENSOR,
    CONF_REARM_EARLY,
    CONF_ROOM_SENSORS,
    CONF_SELECTION_WINDOW,
    CONF_STEP,
    CONF_STEPS,
    CONF_SYNC_CORRECTIONS,
    DEFAULTS,
    DOMAIN,
)

from .conftest import BOT, RAD, ROOM
from .test_controller import setup


def form_defaults(schema):
    """The form's default values, skipping fields with none (the optional sensors)."""
    return {str(k): k.default() for k in schema if k.default is not vol.UNDEFINED}


async def test_user_flow_creates_entry(hass, bot):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Radiator", CONF_BOT: BOT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Radiator"
    assert result["data"] == {CONF_NAME: "Radiator", CONF_BOT: BOT}
    assert result["result"].unique_id == BOT


async def test_duplicate_bot_aborts(hass, bot):
    MockConfigEntry(domain=DOMAIN, unique_id=BOT, data={CONF_NAME: "R", CONF_BOT: BOT}).add_to_hass(
        hass
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Radiator", CONF_BOT: BOT}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_shows_defaults(hass, bot, entry):
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    defaults = form_defaults(schema)
    for key in (
        CONF_SELECTION_WINDOW,
        CONF_BURST_MARGIN,
        CONF_PRESS_RETRIES,
        CONF_ON_SETTING,
        CONF_REARM_EARLY,
        CONF_END_TOLERANCE,
        CONF_MAX_DURATION,
        CONF_MIN_SWITCH_INTERVAL,
        CONF_STEP,
        CONF_STEPS,
        CONF_PRESS_SERVICE,
    ):
        assert defaults[key] == DEFAULTS[key], key
    assert defaults[CONF_PRESS_GAP] == 0  # set by the entry fixture
    assert DEFAULTS[CONF_PRESS_GAP] == 3


async def test_options_flow_saves_and_controller_uses_them(hass, bot, entry):
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**DEFAULTS, CONF_ON_SETTING: 2}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_ON_SETTING] == 2

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.radiator_heating"}, blocking=True
    )
    await hass.async_block_till_done()
    assert len(bot) == 2


async def test_options_flow_rejects_on_setting_above_steps(hass, bot, entry):
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**DEFAULTS, CONF_ON_SETTING: 5}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ON_SETTING: "on_setting_above_steps"}


async def test_options_flow_rejects_press_gap_outside_the_window(hass, bot, entry):
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    defaults = form_defaults(result["data_schema"].schema)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**defaults, CONF_PRESS_GAP: 4}
    )
    assert result["errors"] == {CONF_PRESS_GAP: "press_gap_too_long"}


async def submit_options(hass, entry, **changes):
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {**form_defaults(result["data_schema"].schema), **changes}
    )


def temperature_sensors(hass):
    for entity_id in (RAD, ROOM):
        hass.states.async_set(entity_id, "20.0", {"device_class": "temperature"})


async def test_options_flow_accepts_sensors(hass, bot, entry):
    temperature_sensors(hass)
    result = await submit_options(
        hass, entry, **{CONF_RADIATOR_SENSOR: RAD, CONF_ROOM_SENSORS: [ROOM]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_RADIATOR_SENSOR] == RAD
    assert entry.options[CONF_ROOM_SENSORS] == [ROOM]
    assert entry.runtime_data.sync is not None
    assert entry.options[CONF_SYNC_CORRECTIONS] is True


async def test_options_flow_requires_room_sensor(hass, bot, entry):
    temperature_sensors(hass)
    result = await submit_options(hass, entry, **{CONF_RADIATOR_SENSOR: RAD})
    assert result["errors"] == {CONF_ROOM_SENSORS: "room_sensor_required"}


async def test_options_flow_rejects_radiator_as_room(hass, bot, entry):
    temperature_sensors(hass)
    result = await submit_options(
        hass, entry, **{CONF_RADIATOR_SENSOR: RAD, CONF_ROOM_SENSORS: [ROOM, RAD]}
    )
    assert result["errors"] == {CONF_ROOM_SENSORS: "radiator_in_room_sensors"}
