"""Coordinators for the Nanit integration.

NanitPushCoordinator: Push-based coordinator that wraps NanitCamera.subscribe().
    Fires async_set_updated_data on every CameraEvent callback (sensor, settings,
    control, status, connection changes). No polling — all data arrives via
    WebSocket push.

    Entity availability uses a grace period so that brief reconnections (e.g.,
    pre-emptive token refresh) do not surface as "Unavailable" in HA.

NanitCloudCoordinator: Polls the Nanit cloud API for motion/sound events every
    CLOUD_POLL_INTERVAL seconds.

NanitSoundLightCoordinator: Push-based coordinator wrapping NanitSoundLight.subscribe().
    Receives state updates from the S&L device's local WebSocket.

NanitDiaryCoordinator: Polls /feed, /sleep/status and /sleep/digest for one
    baby every DIARY_POLL_INTERVAL seconds — manual care logs (bottle/
    nursing/diaper), camera-detected sleep events, live asleep/awake, and
    yesterday's daily sleep stats. See diary_api.py for endpoint provenance.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .aionanit_jr.exceptions import NanitAuthError, NanitConnectionError
from .aionanit_jr.models import Baby, CameraEvent, CameraState, CloudEvent, NetworkInfo

from .aionanit_sl.models import SoundLightEvent, SoundLightEventKind, SoundLightFullState
from .aionanit_sl.sound_light import NanitSoundLight
from .const import (
    CLOUD_POLL_INTERVAL,
    DIARY_LOOKBACK_HOURS,
    DIARY_POLL_INTERVAL,
    DOMAIN,
    NETWORK_POLL_INTERVAL,
    ROLLING_AVERAGE_POLL_INTERVAL,
    ROLLING_AVERAGE_WINDOW_DAYS,
)
from .diary_api import (
    DiaryLogEntry,
    DiarySnapshot,
    NanitDiaryClient,
    RollingAverages,
    aggregate_rolling_averages,
    parse_diary_log_row,
    parse_feed_row,
)
from .sanitize import display_name

if TYPE_CHECKING:
    from .aionanit_jr import NanitCamera

    from . import NanitConfigEntry
    from .hub import NanitHub

_LOGGER = logging.getLogger(__name__)

# How long to wait before marking entities unavailable after a disconnect.
# If the WebSocket reconnects within this window, entities never go unavailable.
_AVAILABILITY_GRACE_SECONDS: float = 30.0


class NanitPushCoordinator(DataUpdateCoordinator[CameraState]):
    """Push-based coordinator that receives state updates from NanitCamera.subscribe().

    No polling is configured — async_set_updated_data() is called by the camera
    callback on every state change. Entity availability is driven by the
    ``connected`` flag which tracks the WebSocket connection state, debounced
    by a grace period so brief reconnections don't flash "Unavailable".
    """

    config_entry: NanitConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NanitConfigEntry,
        camera: NanitCamera,
        baby: Baby,
    ) -> None:
        """Initialize the push coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{camera.uid}",
        )
        self.camera = camera
        self.baby = baby
        self.connected: bool = False
        self._unsubscribe: Callable[[], None] | None = None
        self._availability_timer: CALLBACK_TYPE | None = None

    async def async_setup(self) -> None:
        """Start the camera and subscribe to push events.

        Called once from async_setup_entry after the coordinator is created.
        """
        self._unsubscribe = self.camera.subscribe(self._on_camera_event)
        await self.camera.async_start()
        self.connected = self.camera.connected
        self.async_set_updated_data(self.camera.state)

    @callback
    def _on_camera_event(self, event: CameraEvent) -> None:
        """Handle a push event from NanitCamera.subscribe()."""
        transport_connected = self.camera.connected

        if transport_connected:
            # Connection is up — cancel any pending unavailability timer
            # and mark connected immediately.
            self._cancel_availability_timer()
            if not self.connected:
                _LOGGER.info("Camera %s reconnected", self.camera.uid)
            self.connected = True
        elif self.connected:
            # Connection just dropped — start the grace period.
            # Don't mark unavailable yet; give the transport time to reconnect.
            _LOGGER.debug(
                "Camera %s disconnected (grace period %.0fs): %s",
                self.camera.uid,
                _AVAILABILITY_GRACE_SECONDS,
                event.state.connection.last_error,
            )
            self._start_availability_timer()
        # If already disconnected (self.connected is False) and transport is
        # still disconnected, do nothing — timer is already running or fired.

        self.async_set_updated_data(event.state)

    @callback
    def _on_availability_timeout(self, _now: object) -> None:
        """Grace period expired — mark entities unavailable."""
        self._availability_timer = None
        if not self.camera.connected:
            _LOGGER.warning(
                "Camera %s still disconnected after %.0fs grace period",
                self.camera.uid,
                _AVAILABILITY_GRACE_SECONDS,
            )
            self.connected = False
            self.async_update_listeners()

    def _start_availability_timer(self) -> None:
        """Start (or restart) the grace period timer."""
        self._cancel_availability_timer()
        self._availability_timer = async_call_later(
            self.hass, _AVAILABILITY_GRACE_SECONDS, self._on_availability_timeout
        )

    def _cancel_availability_timer(self) -> None:
        """Cancel the grace period timer if running."""
        if self._availability_timer is not None:
            self._availability_timer()
            self._availability_timer = None

    async def async_shutdown(self) -> None:
        """Stop the camera and unsubscribe."""
        self._cancel_availability_timer()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await self.camera.async_stop()
        await super().async_shutdown()


class NanitCloudCoordinator(DataUpdateCoordinator[list[CloudEvent]]):
    """Polling coordinator for Nanit cloud motion/sound events.

    Polls GET /babies/{uid}/messages every CLOUD_POLL_INTERVAL seconds.
    Entities check event timestamps against a window to determine on/off state.
    """

    config_entry: NanitConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NanitConfigEntry,
        hub: NanitHub,
        baby: Baby,
    ) -> None:
        """Initialize the cloud coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{baby.uid}_cloud",
            update_interval=timedelta(seconds=CLOUD_POLL_INTERVAL),
        )
        self._hub = hub
        self.baby = baby

    async def _async_update_data(self) -> list[CloudEvent]:
        """Fetch cloud events from the Nanit API."""
        try:
            client = self._hub.client
            events = await client.async_get_events(self.baby.uid)
            return events
        except NanitAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except NanitConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cloud_fetch_failed",
                translation_placeholders={"error": str(err)},
            ) from err


class NanitNetworkCoordinator(DataUpdateCoordinator[NetworkInfo | None]):
    """Polling coordinator for camera WiFi network diagnostics.

    Polls GET /babies every NETWORK_POLL_INTERVAL seconds and extracts
    the network info for a single camera.
    """

    config_entry: NanitConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NanitConfigEntry,
        hub: NanitHub,
        baby: Baby,
    ) -> None:
        """Initialize the network coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{baby.camera_uid}_network",
            update_interval=timedelta(seconds=NETWORK_POLL_INTERVAL),
        )
        self._hub = hub
        self.baby = baby

    async def _async_update_data(self) -> NetworkInfo | None:
        """Fetch network info from the Nanit API.

        Also checks if any cameras that failed to connect during setup now
        report as connected in the Nanit cloud. If so, triggers a config entry
        reload so HA re-runs setup and registers the camera's entities.
        """
        try:
            # The hub's tolerant fetch parses camera-less baby rows (mixed
            # accounts), including on published aionanit wheels whose strict
            # parser raises KeyError on them.
            babies = await self._hub.async_get_babies_tolerant()
        except NanitAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except NanitConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cloud_fetch_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        # Check if any previously-failed camera has come back online.
        # camera_connected is sourced from the Nanit cloud's own "connected"
        # field — no extra probe needed (getattr: published aionanit 1.8.7
        # wheels predate the field).
        failed = self._hub.failed_camera_uids
        if failed:
            # One reload attempt per camera per HA run: if setup still fails
            # after the reload, the camera lands back in failed_camera_uids
            # and retrying every poll would bounce the entry (and every
            # healthy camera's stream) forever. Tracked in hass.data so the
            # marker survives the reload itself.
            attempted: set[str] = self.hass.data.setdefault(DOMAIN, {}).setdefault(
                f"auto_reload_attempted_{self.config_entry.entry_id}", set()
            )
            recovered = [
                b
                for b in babies
                if b.camera_uid in failed
                and b.camera_uid not in attempted
                and getattr(b, "camera_connected", None) is True
            ]
            if recovered:
                attempted.update(b.camera_uid for b in recovered)
                names = ", ".join(display_name(b.name, b.uid) for b in recovered)
                _LOGGER.info(
                    "Previously offline camera(s) now connected per Nanit cloud: %s. "
                    "Reloading integration to register entities.",
                    names,
                )
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self.config_entry.entry_id)
                )

        for baby in babies:
            if baby.camera_uid == self.baby.camera_uid:
                return baby.network
        return None


_SL_STORE_VERSION = 1
# Fields from SoundLightFullState that we persist across restarts.
_SL_PERSIST_FIELDS = (
    "brightness",
    "light_enabled",
    "color_r",
    "color_g",
    "sound_on",
    "current_track",
    "volume",
    "power_on",
    "temperature_c",
    "humidity_pct",
)

# Fields that must be float in [0.0, 1.0].
_UNIT_FLOAT_FIELDS = frozenset({"brightness", "color_r", "color_g", "volume"})
# Fields that must be bool.
_BOOL_FIELDS = frozenset({"light_enabled", "sound_on", "power_on"})
# Fields that must be finite float (no range constraint).
_FINITE_FLOAT_FIELDS = frozenset({"temperature_c", "humidity_pct"})


def _clamp_restored_value(field: str, value: Any) -> Any:
    """Validate/clamp a restored value, returning ``None`` if invalid."""
    if field in _UNIT_FLOAT_FIELDS:
        if not isinstance(value, int | float):
            return None
        fval = float(value)
        if not math.isfinite(fval):
            return None
        return max(0.0, min(1.0, fval))

    if field in _BOOL_FIELDS:
        return value if isinstance(value, bool) else None

    if field == "current_track":
        return value if isinstance(value, str) else None

    if field in _FINITE_FLOAT_FIELDS:
        if not isinstance(value, int | float):
            return None
        fval = float(value)
        return fval if math.isfinite(fval) else None

    return None


class NanitSoundLightCoordinator(DataUpdateCoordinator[SoundLightFullState]):
    """Push-based coordinator for the Nanit Sound & Light Machine.

    Wraps NanitSoundLight.subscribe() — receives state updates from
    the S&L device via WebSocket (cloud relay or local).
    No polling — all state is pushed by the device.

    Persists the last known state to HA storage so that entities show
    their previous values on restart (instead of "unknown") until the
    first live update arrives from the device.

    Uses a grace period for disconnections so brief reconnections
    (e.g. during hourly access-token refresh) do not flash entities
    as "Unavailable" in HA.
    """

    config_entry: NanitConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NanitConfigEntry,
        sound_light: NanitSoundLight,
        baby: Baby,
        via_camera_uid: str | None = None,
    ) -> None:
        """Initialize the Sound & Light coordinator.

        via_camera_uid links the S&L device to the baby's camera device in
        the registry; None when the baby has no camera (standalone speaker).
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{sound_light.speaker_uid}_sound_light",
        )
        self.sound_light = sound_light
        self.baby = baby
        self.via_camera_uid = via_camera_uid
        self._unsubscribe: Callable[[], None] | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass,
            _SL_STORE_VERSION,
            f"{DOMAIN}_sl_state_{sound_light.speaker_uid}",
        )
        self._sl_connected: bool = False
        self._availability_timer: CALLBACK_TYPE | None = None
        self._save_timer: CALLBACK_TYPE | None = None
        self._pending_save_state: SoundLightFullState | None = None

    @property
    def connected(self) -> bool:
        """Return debounced connection state (survives brief reconnections)."""
        return self._sl_connected

    async def async_setup(self) -> None:
        """Start the S&L device and subscribe to push events."""
        self._unsubscribe = self.sound_light.subscribe(self._on_sl_event)
        await self.sound_light.async_start()
        self._sl_connected = self.sound_light.connected

        # If the device hasn't sent initial state yet (cloud relay),
        # restore the last known state from disk so entities aren't "unknown".
        state = self.sound_light.state
        if state.power_on is None:
            restored = await self._async_restore_state()
            if restored is not None:
                # Feed restored state into the sound_light instance so
                # entities and coordinator data are consistent.
                self.sound_light.restore_state(restored)
                state = restored
                _LOGGER.debug(
                    "S&L %s: restored saved state (power=%s, track=%s, vol=%s)",
                    self.sound_light.speaker_uid,
                    restored.power_on,
                    restored.current_track,
                    restored.volume,
                )

        self.async_set_updated_data(state)

    async def _async_restore_state(self) -> SoundLightFullState | None:
        """Load persisted S&L state from HA storage."""
        try:
            data = await self._store.async_load()
            if not data or not isinstance(data, dict):
                return None
            kwargs = {}
            for field in _SL_PERSIST_FIELDS:
                if field in data and data[field] is not None:
                    value = data[field]
                    clamped = _clamp_restored_value(field, value)
                    if clamped is not None:
                        kwargs[field] = clamped
            if not kwargs:
                return None
            if data.get("available_tracks"):
                tracks = data["available_tracks"]
                if isinstance(tracks, list) and all(isinstance(t, str) for t in tracks):
                    kwargs["available_tracks"] = tuple(tracks)
            return SoundLightFullState(**kwargs)
        except Exception:
            _LOGGER.debug("Failed to restore S&L state", exc_info=True)
            return None

    async def _async_save_state(self, state: SoundLightFullState) -> None:
        """Persist current S&L state to HA storage."""
        try:
            data = {}
            for field in _SL_PERSIST_FIELDS:
                val = getattr(state, field, None)
                if val is not None:
                    data[field] = val
            # Also save available_tracks
            if state.available_tracks:
                data["available_tracks"] = list(state.available_tracks)
            if data:
                await self._store.async_save(data)
        except Exception:
            _LOGGER.debug("Failed to save S&L state", exc_info=True)

    @callback
    def _on_sl_event(self, event: SoundLightEvent) -> None:
        """Handle a push event from NanitSoundLight.subscribe()."""
        if event.kind == SoundLightEventKind.CONNECTION_CHANGE:
            transport_connected = self.sound_light.connected
            if transport_connected:
                # Connection is up — cancel any pending unavailability timer
                # and mark connected immediately.
                self._cancel_availability_timer()
                if not self._sl_connected:
                    _LOGGER.info(
                        "S&L %s reconnected",
                        self.sound_light.speaker_uid,
                    )
                self._sl_connected = True
            elif self._sl_connected:
                # Connection just dropped — start the grace period.
                # Don't mark unavailable yet; give the transport time to reconnect.
                _LOGGER.debug(
                    "S&L %s disconnected (grace period %.0fs)",
                    self.sound_light.speaker_uid,
                    _AVAILABILITY_GRACE_SECONDS,
                )
                self._start_availability_timer()
            # If already disconnected and transport still disconnected,
            # do nothing — timer is already running or fired.

        self.async_set_updated_data(event.state)

        # Debounce state saves — at most every 5 seconds to avoid
        # overlapping writes from rapid state/sensor updates.
        if event.kind in (
            SoundLightEventKind.STATE_UPDATE,
            SoundLightEventKind.SENSOR_UPDATE,
        ):
            self._schedule_save(event.state)

    @callback
    def _schedule_save(self, state: SoundLightFullState) -> None:
        """Schedule a debounced state save (at most every 5 seconds)."""
        self._pending_save_state = state
        if self._save_timer is not None:
            # Timer already running — it will pick up the latest state
            return
        self._save_timer = async_call_later(self.hass, 5, self._do_save)

    @callback
    def _do_save(self, _now: object) -> None:
        """Execute the debounced state save."""
        self._save_timer = None
        if self._pending_save_state is not None:
            state = self._pending_save_state
            self._pending_save_state = None
            self.hass.async_create_task(self._async_save_state(state))

    @callback
    def _on_availability_timeout(self, _now: object) -> None:
        """Grace period expired — mark S&L entities unavailable."""
        self._availability_timer = None
        if not self.sound_light.connected:
            _LOGGER.warning(
                "S&L %s still disconnected after %.0fs grace period",
                self.sound_light.speaker_uid,
                _AVAILABILITY_GRACE_SECONDS,
            )
            self._sl_connected = False
            self.async_update_listeners()

    def _start_availability_timer(self) -> None:
        """Start (or restart) the grace period timer."""
        self._cancel_availability_timer()
        self._availability_timer = async_call_later(
            self.hass, _AVAILABILITY_GRACE_SECONDS, self._on_availability_timeout
        )

    def _cancel_availability_timer(self) -> None:
        """Cancel the grace period timer if running."""
        if self._availability_timer is not None:
            self._availability_timer()
            self._availability_timer = None

    async def async_shutdown(self) -> None:
        """Stop the S&L device and unsubscribe."""
        self._cancel_availability_timer()
        # Cancel debounced save timer and flush final state
        if self._save_timer is not None:
            self._save_timer()
            self._save_timer = None
        self._pending_save_state = None
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        # Save final state before stopping
        await self._async_save_state(self.sound_light.state)
        await self.sound_light.async_stop()
        await super().async_shutdown()


# Diary entries this run has seen, merged from /calendar every poll plus
# anything just written through async_log_entry. /calendar is the real,
# non-transient read path (confirmed back to the baby's birth month) — this
# cache is now just belt-and-suspenders against any propagation delay
# between a POST and its next appearance there, not the only way to see an
# entry (an earlier design relied on /feed, whose kind="diary_log" rows
# turned out to be transient; see diary_api.py's module docstring).
_LOCAL_ENTRY_CACHE_SIZE = 50


class NanitDiaryCoordinator(DataUpdateCoordinator[DiarySnapshot]):
    """Polling coordinator for one baby's care log (diary) and sleep feed.

    Polls GET /babies/{uid}/calendar (diary history: bottle/nursing/diaper/
    semi_auto_sleep), GET /babies/{uid}/feed (sleep transitions + daily
    summaries), and GET /babies/{uid}/sleep/status (live asleep/awake)
    every DIARY_POLL_INTERVAL seconds. All undocumented; see diary_api.py
    for provenance — every shape there is confirmed live as of 2026-08-25.
    """

    config_entry: NanitConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NanitConfigEntry,
        hub: NanitHub,
        baby: Baby,
    ) -> None:
        """Initialize the diary coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{baby.uid}_diary",
            update_interval=timedelta(seconds=DIARY_POLL_INTERVAL),
        )
        self.baby = baby
        self._client = NanitDiaryClient(hub.client, baby.uid)
        self._local_entries: list[DiaryLogEntry] = []

    async def _async_update_data(self) -> DiarySnapshot:
        """Fetch calendar (diary history) + feed (sleep) + live sleep status, newest first."""
        now = time.time()
        start = now - (DIARY_LOOKBACK_HOURS * 3600)
        try:
            calendar_rows = await self._client.async_get_calendar(int(start), int(now))
            feed_rows = await self._client.async_get_feed(int(start), int(now))
            sleep_status = await self._client.async_get_sleep_status()
        except NanitAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except NanitConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="diary_fetch_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        calendar_entries = filter(None, (parse_diary_log_row(row) for row in calendar_rows))
        # /feed no longer contributes diary entries — its kind="diary_log"
        # rows are transient (gone again within minutes, confirmed at
        # every window size up to 90 days) and /calendar is the real,
        # non-transient read path. /feed still carries sleep events/summaries.
        feed_events = sorted(
            filter(None, (parse_feed_row(row) for row in feed_rows if row.get("kind") != "diary_log")),
            key=lambda e: e.time,
            reverse=True,
        )

        # Merge calendar-sourced entries into the local write cache
        # (calendar wins on a uid collision — it's the closer-to-source
        # copy), then drop anything outside the poll window so a stale
        # local-only entry doesn't linger forever.
        merged: dict[str, DiaryLogEntry] = {e.uid: e for e in self._local_entries}
        for entry in calendar_entries:
            merged[entry.uid] = entry
        self._local_entries = [e for e in merged.values() if e.time >= start][:_LOCAL_ENTRY_CACHE_SIZE]

        diary_entries = tuple(sorted(self._local_entries, key=lambda e: e.time, reverse=True))
        return DiarySnapshot(
            diary_entries=diary_entries,
            feed_events=tuple(feed_events),
            sleep_status=sleep_status,
        )

    async def async_log_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Create a diary entry, cache it locally, and refresh listeners."""
        result = await self._client.async_create_log(entry)
        parsed = parse_diary_log_row(result)
        if parsed is not None:
            self._local_entries = [e for e in self._local_entries if e.uid != parsed.uid]
            self._local_entries.append(parsed)
            self.async_set_updated_data(
                DiarySnapshot(
                    diary_entries=tuple(
                        sorted(self._local_entries, key=lambda e: e.time, reverse=True)
                    ),
                    feed_events=self.data.feed_events if self.data else (),
                    sleep_status=self.data.sleep_status if self.data else None,
                )
            )
        return result

    async def async_delete_entry(self, log_uid: str) -> None:
        """Delete a diary entry, drop it locally, and refresh listeners."""
        await self._client.async_delete_log(log_uid)
        self._local_entries = [e for e in self._local_entries if e.uid != log_uid]
        if self.data is not None:
            self.async_set_updated_data(
                DiarySnapshot(
                    diary_entries=tuple(
                        sorted(self._local_entries, key=lambda e: e.time, reverse=True)
                    ),
                    feed_events=self.data.feed_events,
                    sleep_status=self.data.sleep_status,
                )
            )

    async def async_import_history(self, start: datetime, end: datetime) -> dict[str, int]:
        """Backfill daily statistics for [start, end) — see history_import.py."""
        from .history_import import async_import_history

        return await async_import_history(
            self.hass,
            self._client,
            baby_uid=self.baby.uid,
            baby_name=self.baby.name,
            start=start,
            end=end,
        )


class NanitRollingAverageCoordinator(DataUpdateCoordinator[RollingAverages]):
    """Rolling ROLLING_AVERAGE_WINDOW_DAYS-day average of bottle oz / pees / poos.

    A separate, slower-polling coordinator rather than folding this into
    NanitDiaryCoordinator's 5-minute cycle: a 30-day average doesn't move
    enough between polls to justify pulling a month of /calendar that
    often, and the diary coordinator's own poll window (DIARY_LOOKBACK_HOURS)
    is intentionally much shorter for its own purposes (today/yesterday
    buckets, latest-event sensors).
    """

    config_entry: NanitConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NanitConfigEntry,
        hub: NanitHub,
        baby: Baby,
    ) -> None:
        """Initialize the rolling-average coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{baby.uid}_rolling_avg",
            update_interval=timedelta(seconds=ROLLING_AVERAGE_POLL_INTERVAL),
        )
        self.baby = baby
        self._client = NanitDiaryClient(hub.client, baby.uid)

    async def _async_update_data(self) -> RollingAverages:
        """Fetch the trailing window from /calendar and aggregate it."""
        now = time.time()
        start = now - (ROLLING_AVERAGE_WINDOW_DAYS * 86400)
        try:
            rows = await self._client.async_get_calendar(int(start), int(now))
        except NanitAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except NanitConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="diary_fetch_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        return aggregate_rolling_averages(rows, ROLLING_AVERAGE_WINDOW_DAYS)
