"""Raw REST client for Nanit's undocumented diary-log and sleep endpoints.

Not part of the aionanit package -- these were never publicly documented and
aionanit has no methods for them. Mirrors hub.py's own raw-fetch pattern:
reuses the authenticated rest_client/token_manager the aionanit client
already manages, adds nothing to the dependency surface.

Every shape below is CONFIRMED LIVE via two mitmproxy captures against the
account's own Nanit app, 2026-08-25, superseding an
earlier decompile-based synthesis from 2026-07-26.

  * GET /calendar?start=&end= is the real history read path -- confirmed
    returning entries back to 2025-09 (the baby's birth month) in a second
    capture that specifically scrolled the app's history view, after the
    first capture's assumption (diary entries transiently visible in
    /feed) turned out to be a red herring: /feed's kind="diary_log" rows
    disappear again within minutes and there is no window size at which
    they reappear (checked 7/30/90 days, all zero). /calendar is what the
    app actually pages through for history; it is the primary diary read
    path here, not /feed.
  * GET /feed still matters for kind="event" (camera/sleep transitions) and
    kind="summary" (a real daily sleep report under summary_details.stats)
    -- both distinct from diary entries and both behave normally at any
    window size.
  * GET /sleep/status (live asleep/awake) -- confirmed live.
  * POST/DELETE /babies/{uid}/diary/logs -- confirmed live, flat unwrapped
    body, lowercase type strings ("diaper_change", "bottle_feed").
  * GET /sleep/digest -- probed with the HA integration's own live token
    (same headers the app itself sent) and got 401 "not authorized" while
    /babies and /sleep/status succeeded with that same token in the same
    session. Not implemented -- the app and the HA integration appear to
    hold separately-scoped sessions and this endpoint isn't granted to the
    integration's. Superseded anyway: /feed's kind="summary" rows carry
    the same daily stats and are already being fetched.
  * A third diary entry type, "semi_auto_sleep" (camera-assisted sleep
    session: start_source, end_source, duration, net_duration, end_time),
    appears in /calendar but isn't modelled by DiaryLogEntry's known-type
    handling anywhere yet -- it parses fine (type/time/uid/comment are all
    present) but nothing currently reads its extra fields.
  * PUT /diary/logs/{uid} (edit) and NAP entries -- not exercised by the
    app in capture (nap logging goes through a separate /diary/timers
    start/stop flow, not implemented here) and not implemented; do not
    assume this shape without a fresh capture.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import aiohttp

from .aionanit_jr.exceptions import NanitAuthError, NanitConnectionError

if TYPE_CHECKING:
    from .aionanit_jr.client import NanitClient

_LOGGER = logging.getLogger(__name__)

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)

DiaperChangeType = Literal["dry", "mixed", "pee", "poo"]

# /feed "event" keys that mean asleep vs. awake (confirmed live, 2026-08-25).
SLEEP_ASLEEP_KEYS = frozenset({"FELL_ASLEEP", "PUT_IN_BED"})
SLEEP_AWAKE_KEYS = frozenset({"WOKE_UP", "REMOVED", "LEAVING"})
SLEEP_TRANSITION_KEYS = SLEEP_ASLEEP_KEYS | SLEEP_AWAKE_KEYS

# /diary/logs and /feed's kind="diary_log" rows use these on the wire.
DIARY_TYPE_BOTTLE = "bottle_feed"
DIARY_TYPE_NURSING = "nursing"
DIARY_TYPE_DIAPER = "diaper_change"

_ML_PER_OZ = 29.5735


def _to_epoch(value: Any) -> float | None:
    """Coerce a raw time value (int/float/numeric str) to epoch seconds."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


@dataclass(frozen=True, kw_only=True)
class DiaryLogEntry:
    """One manual care-log entry (bottle/nursing/diaper), sourced from /feed."""

    uid: str
    type: str  # normalized uppercase: BOTTLE_FEED / NURSING / DIAPER_CHANGE
    time: float
    comment: str | None
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True, kw_only=True)
class FeedEvent:
    """One /feed row: a camera/sleep transition (kind='event'), not a diary log."""

    kind: str
    key: str | None  # event key, uppercase, only set when kind == "event"
    time: float
    raw: dict[str, Any] = field(repr=False)


def parse_diary_log_row(row: dict[str, Any]) -> DiaryLogEntry | None:
    """Parse one diary-log-shaped row (from /feed's kind="diary_log" or a write response)."""
    uid = row.get("uid")
    raw_type = row.get("type")
    time = _to_epoch(row.get("time"))
    if not isinstance(uid, str) or not isinstance(raw_type, str) or time is None:
        return None
    comment = row.get("comment")
    return DiaryLogEntry(
        uid=uid,
        type=raw_type.upper(),
        time=time,
        comment=comment if isinstance(comment, str) and comment else None,
        raw=row,
    )


def parse_feed_row(row: dict[str, Any]) -> FeedEvent | None:
    """Parse one /feed row; None if it doesn't look like a real entry."""
    kind = row.get("kind")
    time = _to_epoch(row.get("time") or row.get("ts"))
    if not isinstance(kind, str) or time is None:
        return None
    key = row.get("key")
    return FeedEvent(kind=kind, key=key.upper() if isinstance(key, str) else None, time=time, raw=row)


@dataclass(frozen=True, kw_only=True)
class DiarySnapshot:
    """Parsed, time-descending view of a baby's feed, plus live sleep status/digest."""

    diary_entries: tuple[DiaryLogEntry, ...]
    feed_events: tuple[FeedEvent, ...]
    sleep_status: dict[str, Any] | None  # raw GET /sleep/status body, or None if it failed

    def latest(self, entry_type: str) -> DiaryLogEntry | None:
        """Most recent diary entry of a given type (e.g. "BOTTLE_FEED")."""
        return next((e for e in self.diary_entries if e.type == entry_type), None)

    def count_since(self, entry_type: str, since: float) -> int:
        """Count diary entries of a type at or after an epoch cutoff."""
        return sum(1 for e in self.diary_entries if e.type == entry_type and e.time >= since)

    def sum_since(self, entry_type: str, field_name: str, since: float) -> float:
        """Sum a numeric raw field across entries of a type since an epoch cutoff."""
        total = 0.0
        for e in self.diary_entries:
            if e.type != entry_type or e.time < since:
                continue
            value = e.raw.get(field_name)
            if isinstance(value, int | float) and not isinstance(value, bool):
                total += float(value)
        return total

    def latest_sleep_transition(self) -> FeedEvent | None:
        """Most recent asleep/awake transition from /feed, if any."""
        return next(
            (e for e in self.feed_events if e.kind == "event" and e.key in SLEEP_TRANSITION_KEYS),
            None,
        )

    def is_asleep(self) -> bool | None:
        """Whether the baby is currently asleep.

        Prefers the dedicated /sleep/status endpoint (authoritative, includes
        non-camera sources); falls back to the latest /feed transition if
        that call failed this poll.
        """
        if self.sleep_status is not None:
            status = self.sleep_status.get("status")
            if isinstance(status, str):
                return status.upper() == "ASLEEP"
        transition = self.latest_sleep_transition()
        if transition is None:
            return None
        return transition.key in SLEEP_ASLEEP_KEYS

    def sleep_transitions_since(self, since: float) -> tuple[FeedEvent, ...]:
        """Asleep/awake transitions at or after an epoch cutoff, oldest first."""
        rows = [
            e
            for e in self.feed_events
            if e.kind == "event" and e.key in SLEEP_TRANSITION_KEYS and e.time >= since
        ]
        rows.sort(key=lambda e: e.time)
        return tuple(rows)

    def latest_summary(self) -> FeedEvent | None:
        """Most recent daily sleep-summary row (kind="summary"), day or night part.

        Confirmed live 2026-08-25: /feed's summary rows carry a real daily
        report under raw["summary_details"]["stats"] -- total_sleep_time,
        total_awake_time, times_woke_up, sleep_sessions,
        sleep_sessions_details, longest_sleep, period_date, period_part
        ("day"/"night"), sleep_score (an object, .score often null).
        """
        return next((e for e in self.feed_events if e.kind == "summary"), None)


@dataclass(frozen=True, kw_only=True)
class RollingAverages:
    """Average-per-day over a fixed trailing window, from /calendar."""

    window_days: int
    bottle_oz_avg_per_day: float
    pees_avg_per_day: float
    poos_avg_per_day: float


def aggregate_rolling_averages(rows: list[dict[str, Any]], window_days: int) -> RollingAverages:
    """Aggregate raw /calendar rows into per-day averages over window_days.

    Divides by the fixed window, not by days-that-had-data, so a sparse
    early history (e.g. the first few days after this platform started
    logging) reads as a low average rather than an inflated one.
    """
    bottle_oz_total = 0.0
    pee_count = 0
    poo_count = 0
    for row in rows:
        entry = parse_diary_log_row(row)
        if entry is None:
            continue
        if entry.type == "BOTTLE_FEED":
            amount_ml = entry.raw.get("feed_amount")
            if isinstance(amount_ml, int | float) and not isinstance(amount_ml, bool):
                bottle_oz_total += amount_ml / _ML_PER_OZ
        elif entry.type == "DIAPER_CHANGE":
            change_type = entry.raw.get("change_type")
            if change_type == "pee":
                pee_count += 1
            elif change_type == "poo":
                poo_count += 1
            elif change_type == "mixed":
                # Counts toward both -- a mixed change is one pee AND one poo.
                pee_count += 1
                poo_count += 1

    return RollingAverages(
        window_days=window_days,
        bottle_oz_avg_per_day=round(bottle_oz_total / window_days, 2),
        pees_avg_per_day=round(pee_count / window_days, 2),
        poos_avg_per_day=round(poo_count / window_days, 2),
    )


class NanitDiaryClient:
    """Talks to /babies/{uid}/feed, /diary/logs, and /sleep/* for one baby."""

    def __init__(self, client: NanitClient, baby_uid: str) -> None:
        """Initialize with an already-authenticated aionanit NanitClient."""
        self._client = client
        self._baby_uid = baby_uid

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Make an authenticated request, raising aionanit's own exception types."""
        from .aionanit_jr.rest import NANIT_API_HEADERS

        tm = self._client.token_manager
        if tm is None:
            raise NanitAuthError("Not authenticated — call async_login first")
        access_token = await tm.async_get_access_token()
        rest = self._client.rest_client
        url = f"{rest.base_url}{path}"
        try:
            async with rest.session.request(
                method,
                url,
                headers={**NANIT_API_HEADERS, "Authorization": access_token},
                params=params,
                json=json_body,
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise NanitAuthError("Access token invalid")
                if resp.status in (204, 404) and method != "GET":
                    return None
                resp.raise_for_status()
                # Content-Length is absent on plenty of real, non-empty
                # bodies (HTTP/2 and chunked responses routinely omit it —
                # api.nanit.com does for /feed), so it cannot gate whether
                # to parse. Read the body and let an empty/non-JSON one
                # fail decode instead of trusting a header that may not be
                # there at all.
                text = await resp.text()
                if not text:
                    return None
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return None
        except (TimeoutError, aiohttp.ClientError) as err:
            raise NanitConnectionError(str(err)) from err

    async def async_get_feed(
        self, start: int, end: int, items: int = 1000
    ) -> list[dict[str, Any]]:
        """GET /babies/{uid}/feed?start=&end=&items= — sleep timeline + care-log entries.

        Confirmed live. Relevant `kind` values: "event" (raw camera/sleep
        transitions — FELL_ASLEEP/WOKE_UP/PUT_IN_BED/REMOVED/LEAVING, plus
        camera MOTION/SOUND/VISIT) and "diary_log" (manual bottle/nursing/
        diaper entries, same shape as the /diary/logs write response).
        """
        body = await self._async_request(
            "GET",
            f"/babies/{self._baby_uid}/feed",
            params={"start": start, "end": end, "items": items},
        )
        rows = body.get("feed", []) if isinstance(body, dict) else []
        return [row for row in rows if isinstance(row, dict)]

    async def async_get_calendar(self, start: int, end: int) -> list[dict[str, Any]]:
        """GET /babies/{uid}/calendar?start=&end= — the real diary history read path.

        Confirmed live, returning entries back to the baby's birth month in
        a wide-range capture. Same row shape as the /diary/logs write
        response (uid/type/time/comment plus type-specific fields), no
        `kind` wrapper needed since every row here is a diary-shaped entry.
        Unlike /feed, entries do not disappear from this endpoint.
        """
        body = await self._async_request(
            "GET",
            f"/babies/{self._baby_uid}/calendar",
            params={"start": start, "end": end},
        )
        rows = body.get("calendar", []) if isinstance(body, dict) else []
        return [row for row in rows if isinstance(row, dict)]

    async def async_get_sleep_status(self) -> dict[str, Any] | None:
        """GET /babies/{uid}/sleep/status — live asleep/awake, confirmed live.

        Response: {"baby_uid", "status" ("ASLEEP"/other), "time", "source",
        "last_sd_event_uid", "last_diary_event_uid", "event_key"}.
        """
        body = await self._async_request("GET", f"/babies/{self._baby_uid}/sleep/status")
        return body if isinstance(body, dict) else None

    async def async_create_log(self, entry: dict[str, Any]) -> dict[str, Any]:
        """POST a new diary entry. Confirmed live: flat body, echoes back under "diary_log"."""
        body = await self._async_request(
            "POST", f"/babies/{self._baby_uid}/diary/logs", json_body=entry
        )
        return body.get("diary_log", {}) if isinstance(body, dict) else {}

    async def async_delete_log(self, log_uid: str) -> None:
        """DELETE a diary entry by uid. Confirmed live."""
        await self._async_request("DELETE", f"/babies/{self._baby_uid}/diary/logs/{log_uid}")


def build_bottle_entry(*, time: int, amount_ml: float, comment: str | None = None) -> dict[str, Any]:
    """Build a bottle-feed diary entry. feed_amount is in millilitres."""
    return {"type": DIARY_TYPE_BOTTLE, "time": time, "feed_amount": amount_ml, "comment": comment or ""}


def build_nursing_entry(
    *, time: int, left_duration_s: int, right_duration_s: int, comment: str | None = None
) -> dict[str, Any]:
    """Build a nursing diary entry. Durations are in seconds.

    Field names (left_duration/right_duration) are carried over from an
    earlier, uncaptured-this-round mitmproxy trace (May 2026) rather than
    today's capture, which only exercised bottle and diaper. Verify before
    relying on it.
    """
    return {
        "type": DIARY_TYPE_NURSING,
        "time": time,
        "left_duration": left_duration_s,
        "right_duration": right_duration_s,
        "comment": comment or "",
    }


def build_diaper_entry(
    *, time: int, change_type: DiaperChangeType, comment: str | None = None
) -> dict[str, Any]:
    """Build a diaper-change diary entry. Confirmed live, exact shape."""
    return {
        "type": DIARY_TYPE_DIAPER,
        "time": time,
        "change_type": change_type,
        "comment": comment or "",
    }
