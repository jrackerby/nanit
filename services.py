"""Service handlers for Nanit care-log entries.

Registered once per HA run (idempotent across config entries — accounts
almost always have exactly one). Each call targets a baby's "Care Log"
device and resolves it to that baby's NanitDiaryCoordinator via the device
registry, then the owning config entry's runtime_data.

Write endpoint confidence: see diary_api.py's module docstring. Run one
supervised smoke test (create -> verify via GET -> delete) before relying
on this for real logging.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .diary_api import build_bottle_entry, build_diaper_entry, build_nursing_entry

ATTR_DEVICE_ID = "device_id"
ATTR_COMMENT = "comment"
ATTR_TIME = "time"

SERVICE_LOG_DIAPER_CHANGE = "log_diaper_change"
SERVICE_LOG_BOTTLE_FEED = "log_bottle_feed"
SERVICE_LOG_NURSING = "log_nursing"
SERVICE_DELETE_DIARY_LOG = "delete_diary_log"
SERVICE_IMPORT_HISTORY = "import_history"

# Default backfill horizon when start_date isn't given. Not the baby's real
# birthdate (that needs its own /babies fetch) -- wide enough to cover it
# for this household without adding another network round trip; harmless
# to run further back than data exists; empty days just don't get written.
_DEFAULT_IMPORT_DAYS = 400

_BASE_FIELDS = {
    vol.Required(ATTR_DEVICE_ID): cv.string,
    vol.Optional(ATTR_COMMENT): cv.string,
    vol.Optional(ATTR_TIME): cv.datetime,
}

LOG_DIAPER_CHANGE_SCHEMA = vol.Schema(
    {**_BASE_FIELDS, vol.Required("change_type"): vol.In(("pee", "poo", "mixed", "dry"))}
)

LOG_BOTTLE_FEED_SCHEMA = vol.Schema({**_BASE_FIELDS, vol.Required("amount_oz"): vol.Coerce(float)})

LOG_NURSING_SCHEMA = vol.Schema(
    {
        **_BASE_FIELDS,
        vol.Optional("left_minutes", default=0): vol.Coerce(float),
        vol.Optional("right_minutes", default=0): vol.Coerce(float),
    }
)

DELETE_DIARY_LOG_SCHEMA = vol.Schema(
    {vol.Required(ATTR_DEVICE_ID): cv.string, vol.Required("log_uid"): cv.string}
)

IMPORT_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional("start_date"): cv.date,
        vol.Optional("end_date"): cv.date,
    }
)


def _resolve_diary(hass: HomeAssistant, device_id: str):
    """Resolve a service call's target device_id to its DiaryData."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Unknown device_id: {device_id}")
    baby_uid = next(
        (
            identifier[1][: -len("_diary")]
            for identifier in device.identifiers
            if identifier[0] == DOMAIN and identifier[1].endswith("_diary")
        ),
        None,
    )
    if baby_uid is None:
        raise ServiceValidationError(f"{device_id} is not a Nanit Care Log device")
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        data = getattr(entry, "runtime_data", None)
        if data is not None and baby_uid in data.diaries:
            return data.diaries[baby_uid]
    raise ServiceValidationError(f"No active Nanit care log for device {device_id}")


def _epoch(call_time: Any) -> int:
    """Convert an optional service-call datetime to epoch seconds, defaulting to now."""
    if call_time is None:
        return int(time.time())
    return int(call_time.timestamp())


async def _async_create_log(hass: HomeAssistant, device_id: str, entry: dict[str, Any]) -> None:
    """Look up the target baby's coordinator and create one diary entry."""
    diary = _resolve_diary(hass, device_id)
    try:
        await diary.coordinator.async_log_entry(entry)
    except Exception as err:
        raise HomeAssistantError(f"Failed to log Nanit diary entry: {err}") from err


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register Nanit's care-log services once per HA run."""
    if hass.services.has_service(DOMAIN, SERVICE_LOG_DIAPER_CHANGE):
        return

    async def _log_diaper_change(call: ServiceCall) -> None:
        entry = build_diaper_entry(
            time=_epoch(call.data.get(ATTR_TIME)),
            change_type=call.data["change_type"],
            comment=call.data.get(ATTR_COMMENT),
        )
        await _async_create_log(hass, call.data[ATTR_DEVICE_ID], entry)

    async def _log_bottle_feed(call: ServiceCall) -> None:
        entry = build_bottle_entry(
            time=_epoch(call.data.get(ATTR_TIME)),
            amount_ml=call.data["amount_oz"] * 29.5735,
            comment=call.data.get(ATTR_COMMENT),
        )
        await _async_create_log(hass, call.data[ATTR_DEVICE_ID], entry)

    async def _log_nursing(call: ServiceCall) -> None:
        entry = build_nursing_entry(
            time=_epoch(call.data.get(ATTR_TIME)),
            left_duration_s=int(call.data.get("left_minutes", 0) * 60),
            right_duration_s=int(call.data.get("right_minutes", 0) * 60),
            comment=call.data.get(ATTR_COMMENT),
        )
        await _async_create_log(hass, call.data[ATTR_DEVICE_ID], entry)

    async def _delete_diary_log(call: ServiceCall) -> None:
        diary = _resolve_diary(hass, call.data[ATTR_DEVICE_ID])
        try:
            await diary.coordinator.async_delete_entry(call.data["log_uid"])
        except Exception as err:
            raise HomeAssistantError(f"Failed to delete Nanit diary log: {err}") from err

    async def _import_history(call: ServiceCall) -> ServiceResponse:
        diary = _resolve_diary(hass, call.data[ATTR_DEVICE_ID])
        start_date = call.data.get("start_date")
        end_date = call.data.get("end_date")
        start = (
            dt_util.start_of_local_day(start_date)
            if start_date is not None
            else dt_util.start_of_local_day() - timedelta(days=_DEFAULT_IMPORT_DAYS)
        )
        end = dt_util.start_of_local_day(end_date) if end_date is not None else dt_util.now()
        try:
            counts = await diary.coordinator.async_import_history(start, end)
        except Exception as err:
            raise HomeAssistantError(f"Failed to import Nanit history: {err}") from err
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            **counts,
        }

    hass.services.async_register(
        DOMAIN, SERVICE_LOG_DIAPER_CHANGE, _log_diaper_change, schema=LOG_DIAPER_CHANGE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LOG_BOTTLE_FEED, _log_bottle_feed, schema=LOG_BOTTLE_FEED_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_LOG_NURSING, _log_nursing, schema=LOG_NURSING_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_DIARY_LOG, _delete_diary_log, schema=DELETE_DIARY_LOG_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_HISTORY,
        _import_history,
        schema=IMPORT_HISTORY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
