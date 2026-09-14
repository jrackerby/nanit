"""The night light must never assert an on/off it has not read (#20).

WHAT THESE PROVE, AND WHAT THEY DO NOT. They exercise this module's own
state logic against a stubbed Home Assistant (see ha_stubs). LAW.md §16:
green on a stub authorises nothing about a running instance -- what HA does
with these entities still has to be observed there.

ONLY `test_does_not_restore_state` ENCODES THE DELTA. It is the one that
fails against the code before the fix; the rest pass either way, because
the restore ran in `async_added_to_hass` and none of them drive that hook.
They are a blast-radius net -- they prove the removal did not disturb the
values that ARE device-backed -- and should not be read as evidence the
defect is fixed. Verified 2026-09-14 by running the file against master:
1 failed, 4 passed.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import nanit.light as light


def _state(night_light=None, brightness=None):
    """A CameraState-shaped double: only the fields this platform reads."""
    return SimpleNamespace(
        control=SimpleNamespace(night_light=night_light),
        settings=SimpleNamespace(night_light_brightness=brightness),
    )


def _entity(state=None):
    coordinator = SimpleNamespace(data=state)
    camera = SimpleNamespace(uid="cam-uid")
    return light.NanitNightLight(coordinator, camera)


def test_does_not_restore_state() -> None:
    """The defect was a RestoreEntity restore of an unbacked value.

    `GET_CONTROL` answers without `night_light` (TOOLS.md, measured), so a
    restore republished the last run's on/off indefinitely. Guard both the
    base class and the hook, so neither returns quietly.
    """
    base_names = [c.__name__ for c in light.NanitNightLight.__mro__]
    assert "RestoreEntity" not in base_names, base_names
    assert "async_added_to_hass" not in vars(light.NanitNightLight)
    source = inspect.getsource(light.NanitNightLight)
    assert "async_get_last_state" not in source


def test_is_on_is_unknown_before_the_camera_reports() -> None:
    """The subject of #20: no control value means unknown, not a boolean."""
    assert _entity().is_on is None
    assert _entity(_state(night_light=None)).is_on is None


def test_is_on_follows_control_once_it_arrives() -> None:
    """A value the camera did report is reported through unchanged."""
    assert _entity(_state(night_light=light.NightLightState.ON)).is_on is True
    assert _entity(_state(night_light=light.NightLightState.OFF)).is_on is False


def test_brightness_survives_an_unknown_on_off() -> None:
    """Brightness is device-backed and must not be collateral damage.

    `night_light_brightness` comes from `GET_SETTINGS`, which IS populated at
    startup and survives a restart -- the distinction TOOLS.md draws against
    the control block. Presence is asserted, not the scaled figure: the
    conversion is HA's own and is stubbed here, so a number would only be
    testing the stub.
    """
    entity = _entity(_state(night_light=None, brightness=42))
    assert entity.is_on is None
    assert entity.brightness is not None


def test_a_zero_brightness_is_not_reported() -> None:
    """Zero is the device's 'nothing set' and must not scale into a value."""
    assert _entity(_state(night_light=None, brightness=0)).brightness is None
