# Boost Timer

A private Home Assistant integration that drives a dumb pushbutton boost timer (off / 1h / 2h / 3h / 4h,
one button, four LEDs) through a SwitchBot Bot, and keeps a model of what the timer is doing.

Quick presses (under ~5 s apart) cycle off → 1h → 2h → 3h → 4h → off; a press after a gap turns a
running timer off. The integration plans presses from that model and exposes:

- `switch.<name>_heating`: keep the radiator on (re-arming at each expiry) or turn it off
- `select.<name>_setting`: what the LEDs should show; choosing a value presses to it
- `sensor.<name>_expires_at`, `sensor.<name>_heating_until`, `binary_sensor.<name>_heating`,
  `binary_sensor.<name>_uncertain`
- actions `boost_timer.heat_for` (`duration` or `until`) and `boost_timer.sync` (correct the model
  without pressing)

Install by copying `custom_components/boost_timer` into the Home Assistant `config/custom_components`
directory and adding the integration from the UI.

## Development

Needs Python 3.14 (Home Assistant 2026.10).

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```
