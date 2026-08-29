"""One-time/re-runnable historical backfill into HA long-term statistics.

Nanit's own history lives in GET /babies/{uid}/calendar (diary entries) and
GET /babies/{uid}/feed's kind="summary" rows (daily sleep stats) -- both
confirmed live 2026-08-25, see diary_api.py. Neither is exposed as an HA
entity's own history (a coordinator only ever holds the current poll
window), so this writes daily totals directly into the recorder as
*external* statistics -- the same mechanism integrations use to import
history from a utility/energy provider -- so History/Statistics graphs show
real multi-month trend data instead of only "since this integration
started."

Private/local use only; this module has not been checked for correctness
against any other Nanit account's data shape.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .diary_api import parse_diary_log_row, parse_feed_row

if TYPE_CHECKING:
    from .diary_api import NanitDiaryClient

_LOGGER = logging.getLogger(__name__)

# Fetch a month at a time. /calendar's actual page/row cap (if any) was
# never determined -- chunking keeps every request comfortably inside the
# ranges already proven live (a single 90-day /feed pull returned 4816 rows
# with no sign of truncation) rather than relying on an untested wide call.
_CHUNK_DAYS = 30

_ML_PER_OZ = 29.5735


def _local_day_start(ts: float) -> datetime:
    """Return the start-of-local-day (tz-aware) bucket for an epoch timestamp."""
    local = dt_util.as_local(dt_util.utc_from_timestamp(ts))
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


async def async_import_history(
    hass: HomeAssistant,
    client: NanitDiaryClient,
    *,
    baby_uid: str,
    baby_name: str,
    start: datetime,
    end: datetime,
) -> dict[str, int]:
    """Backfill daily diaper/bottle/sleep statistics for [start, end).

    Returns counts of days written per series, for the service response.
    """
    diaper_daily: dict[datetime, int] = defaultdict(int)
    bottle_oz_daily: dict[datetime, float] = defaultdict(float)
    sleep_min_daily: dict[datetime, float] = defaultdict(float)

    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=_CHUNK_DAYS), end)
        chunk_start_epoch, chunk_end_epoch = int(chunk_start.timestamp()), int(chunk_end.timestamp())

        for row in await client.async_get_calendar(chunk_start_epoch, chunk_end_epoch):
            entry = parse_diary_log_row(row)
            if entry is None:
                continue
            day = _local_day_start(entry.time)
            if entry.type == "DIAPER_CHANGE":
                diaper_daily[day] += 1
            elif entry.type == "BOTTLE_FEED":
                amount_ml = entry.raw.get("feed_amount")
                if isinstance(amount_ml, int | float):
                    bottle_oz_daily[day] += amount_ml / _ML_PER_OZ

        for row in await client.async_get_feed(chunk_start_epoch, chunk_end_epoch, items=5000):
            if row.get("kind") != "summary":
                continue
            event = parse_feed_row(row)
            if event is None:
                continue
            details = row.get("summary_details")
            stats = details.get("stats") if isinstance(details, dict) else None
            total_sleep = stats.get("total_sleep_time") if isinstance(stats, dict) else None
            if isinstance(total_sleep, int | float):
                day = _local_day_start(event.time)
                # A day can carry both a "day" and a "night" summary row;
                # keep the larger so naps don't get overwritten by (or
                # overwrite) the overnight total.
                sleep_min_daily[day] = max(sleep_min_daily[day], total_sleep / 60)

        chunk_start = chunk_end

    _write_series(
        hass,
        statistic_id=f"{DOMAIN}:{baby_uid}_diaper_changes_daily",
        name=f"{baby_name} Diaper Changes (daily)",
        unit="count",
        daily=diaper_daily,
    )
    _write_series(
        hass,
        statistic_id=f"{DOMAIN}:{baby_uid}_bottle_oz_daily",
        name=f"{baby_name} Bottle (daily)",
        unit="fl. oz.",
        daily=bottle_oz_daily,
    )
    _write_series(
        hass,
        statistic_id=f"{DOMAIN}:{baby_uid}_sleep_minutes_daily",
        name=f"{baby_name} Sleep (daily)",
        unit="min",
        daily=sleep_min_daily,
    )

    return {
        "diaper_days": len(diaper_daily),
        "bottle_days": len(bottle_oz_daily),
        "sleep_days": len(sleep_min_daily),
    }


def _write_series(
    hass: HomeAssistant,
    *,
    statistic_id: str,
    name: str,
    unit: str,
    daily: dict[datetime, float],
) -> None:
    """Write one cumulative-sum external statistic series, oldest first.

    `sum` is a running total across the whole imported series (the
    convention external statistics use, matching how HA's own energy
    import works) so the frontend's "change over period" view derives each
    day's real total by differencing consecutive points.
    """
    if not daily:
        return
    metadata = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement=unit,
    )
    running_total = 0.0
    stats: list[StatisticData] = []
    for day in sorted(daily):
        running_total += daily[day]
        stats.append(StatisticData(start=day, sum=running_total))
    async_add_external_statistics(hass, metadata, stats)


def external_statistic_ids(baby_uid: str) -> Iterable[str]:
    """Return the statistic_ids this module writes for one baby (for cleanup/reference)."""
    return (
        f"{DOMAIN}:{baby_uid}_diaper_changes_daily",
        f"{DOMAIN}:{baby_uid}_bottle_oz_daily",
        f"{DOMAIN}:{baby_uid}_sleep_minutes_daily",
    )
