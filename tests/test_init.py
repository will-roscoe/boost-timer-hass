import json
from pathlib import Path

from homeassistant.config_entries import ConfigEntryState


async def test_setup_and_unload(hass, bot, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


def test_listed_as_a_device_integration():
    """A helper integration is listed under Helpers, not Integrations; each entry is a device."""
    manifest = json.loads(
        (Path(__file__).parent.parent / "custom_components/boost_timer/manifest.json").read_text()
    )
    assert manifest["integration_type"] == "device"
