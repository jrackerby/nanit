"""Sensor entities for Nanit."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfFrequency,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .aionanit_jr.models import CameraState, NetworkInfo

from . import NanitConfigEntry
from .aionanit_sl.models import SoundLightFullState
from .coordinator import (
    NanitDiaryCoordinator,
    NanitNetworkCoordinator,
    NanitPushCoordinator,
    NanitRollingAverageCoordinator,
    NanitSoundLightCoordinator,
)
from .diary_api import DiarySnapshot, RollingAverages
from .entity import (
    NanitDiaryEntity,
    NanitEntity,
    NanitNetworkEntity,
    NanitRollingAverageEntity,
    NanitSoundLightEntity,
)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class NanitSensorEntityDescription(SensorEntityDescription):
    """Description for Nanit sensor."""

    value_fn: Callable[[CameraState], float | int | None]


@dataclass(frozen=True, kw_only=True)
class NanitSLSensorEntityDescription(SensorEntityDescription):
    """Description for Nanit Sound & Light sensor."""

    value_fn: Callable[[SoundLightFullState], str | float | int | None]


SENSORS: tuple[NanitSensorEntityDescription, ...] = (
    NanitSensorEntityDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=True,
        value_fn=lambda state: state.sensors.temperature,
    ),
    NanitSensorEntityDescription(
        key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=True,
        suggested_display_precision=1,
        value_fn=lambda state: state.sensors.humidity,
    ),
    NanitSensorEntityDescription(
        key="light",
        device_class=SensorDeviceClass.ILLUMINANCE,
        native_unit_of_measurement=LIGHT_LUX,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.sensors.light,
    ),
)

SL_SENSORS: tuple[NanitSLSensorEntityDescription, ...] = (
    NanitSLSensorEntityDescription(
        key="sl_temperature",
        translation_key="sl_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=True,
        suggested_display_precision=1,
        value_fn=lambda state: (
            round(state.temperature_c, 2) if state.temperature_c is not None else None
        ),
    ),
    NanitSLSensorEntityDescription(
        key="sl_humidity",
        translation_key="sl_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=True,
        suggested_display_precision=1,
        value_fn=lambda state: (
            round(state.humidity_pct, 2) if state.humidity_pct is not None else None
        ),
    ),
    NanitSLSensorEntityDescription(
        key="sl_battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        # The device reports a coarse 5-bucket state of charge, mapped to
        # representative percentages by the transport.
        value_fn=lambda state: state.battery_percent,
    ),
    NanitSLSensorEntityDescription(
        key="sl_firmware",
        translation_key="sl_firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.firmware_version,
    ),
)


@dataclass(frozen=True, kw_only=True)
class NanitNetworkSensorDescription(SensorEntityDescription):
    """Description for network diagnostic sensors."""

    value_fn: Callable[[NetworkInfo], str | int | None]


NETWORK_SENSORS: tuple[NanitNetworkSensorDescription, ...] = (
    NanitNetworkSensorDescription(
        key="wifi_ssid",
        translation_key="wifi_ssid",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda net: net.ssid,
    ),
    NanitNetworkSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda net: net.signal_dbm,
    ),
    NanitNetworkSensorDescription(
        key="wifi_frequency",
        translation_key="wifi_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda net: net.frequency_mhz,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NanitConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nanit sensors for all devices on the account."""
    entities: list[SensorEntity] = []
    for cam_data in entry.runtime_data.cameras.values():
        for description in SENSORS:
            entities.append(NanitSensor(cam_data.push_coordinator, description))

        # Network diagnostic sensors (optional)
        net_coordinator = cam_data.network_coordinator
        if net_coordinator is not None:
            for net_description in NETWORK_SENSORS:
                entities.append(NanitNetworkSensor(net_coordinator, net_description))

    # Sound & Light Machine sensors
    for speaker_data in entry.runtime_data.speakers.values():
        for sl_description in SL_SENSORS:
            entities.append(NanitSLSensor(speaker_data.coordinator, sl_description))
        entities.append(NanitSLConnectionModeSensor(speaker_data.coordinator))
        entities.append(NanitSLWifiSensor(speaker_data.coordinator))

    # Care log / sleep-feed sensors
    for diary_data in entry.runtime_data.diaries.values():
        for diary_description in DIARY_SENSORS:
            entities.append(NanitDiarySensor(diary_data.coordinator, diary_description))
        entities.append(NanitSleepSummarySensor(diary_data.coordinator))
        if diary_data.rolling is not None:
            for rolling_description in ROLLING_AVERAGE_SENSORS:
                entities.append(NanitRollingAverageSensor(diary_data.rolling, rolling_description))

    async_add_entities(entities)


class NanitSensor(NanitEntity, SensorEntity):
    """Nanit Sensor."""

    entity_description: NanitSensorEntityDescription

    def __init__(
        self,
        coordinator: NanitPushCoordinator,
        description: NanitSensorEntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.camera.uid}_{description.key}"

    @property
    def native_value(self) -> float | int | None:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


class NanitSLSensor(NanitSoundLightEntity, SensorEntity):
    """Nanit Sound & Light Machine Sensor (temperature, humidity)."""

    entity_description: NanitSLSensorEntityDescription

    def __init__(
        self,
        coordinator: NanitSoundLightCoordinator,
        description: NanitSLSensorEntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.sound_light.speaker_uid}_{description.key}"

    @property
    def native_value(self) -> str | float | int | None:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


class NanitSLWifiSensor(NanitSoundLightEntity, SensorEntity):
    """WiFi signal-strength sensor for the S&L, SSID/BSSID/channel as attrs.

    Diagnostic and registry-disabled by default, matching the pattern from
    nanit-sound-light: RSSI updates every poll cycle, so keeping it off by
    default avoids recorder churn for users who don't care.
    """

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "sl_wifi_signal"

    def __init__(
        self,
        coordinator: NanitSoundLightCoordinator,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.sound_light.speaker_uid}_sl_wifi_signal"

    @property
    def native_value(self) -> int | None:
        """Return the WiFi RSSI in dBm."""
        if self.coordinator.data is None:
            return None
        result: int | None = self.coordinator.data.wifi_rssi
        return result

    @property
    def extra_state_attributes(self) -> dict[str, str | int | None]:
        """Return SSID / BSSID / channel as attributes."""
        state = self.coordinator.data
        if state is None:
            return {}
        return {
            "ssid": state.wifi_ssid,
            "bssid": state.wifi_bssid,
            "channel": state.wifi_channel,
        }


class NanitSLConnectionModeSensor(NanitSoundLightEntity, SensorEntity):
    """Diagnostic sensor showing S&L connection type: local, cloud, or unavailable."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "sl_connection_mode"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["local", "cloud", "unavailable"]  # noqa: RUF012

    def __init__(
        self,
        coordinator: NanitSoundLightCoordinator,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.sound_light.speaker_uid}_sl_connection_mode"

    @property
    def available(self) -> bool:
        """Always available so it can report the unavailable connection state."""
        return self.coordinator.last_update_success and self.coordinator.data is not None

    @property
    def native_value(self) -> str:
        """Return the current connection mode."""
        result: str = self.coordinator.sound_light.connection_mode
        return result


class NanitNetworkSensor(NanitNetworkEntity, SensorEntity):
    """Diagnostic sensor for camera WiFi network information."""

    entity_description: NanitNetworkSensorDescription

    def __init__(
        self,
        coordinator: NanitNetworkCoordinator,
        description: NanitNetworkSensorDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.baby.camera_uid}_{description.key}"

    @property
    def native_value(self) -> str | int | None:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


# --- Care log / sleep-feed sensors ---
# Field provenance and confidence: see diary_api.py.

_ML_PER_OZ = 29.5735
_DIAPER_CHANGE_TYPES = ("pee", "poo", "mixed", "dry")


def _local_midnight_epoch() -> float:
    """Epoch seconds for the start of today in HA's configured timezone."""
    return dt_util.start_of_local_day().timestamp()


def _epoch_to_dt(epoch: float | None):
    """Convert epoch seconds to an aware UTC datetime, or None."""
    return dt_util.utc_from_timestamp(epoch) if epoch is not None else None


def _diaper_breakdown(snapshot: DiarySnapshot, since: float) -> dict[str, int]:
    """Count today's diaper changes by change_type."""
    counts = dict.fromkeys(_DIAPER_CHANGE_TYPES, 0)
    for entry in snapshot.diary_entries:
        if entry.type != "DIAPER_CHANGE" or entry.time < since:
            continue
        change_type = entry.raw.get("change_type")
        if isinstance(change_type, str) and change_type.lower() in counts:
            counts[change_type.lower()] += 1
    return counts


@dataclass(frozen=True, kw_only=True)
class NanitDiarySensorEntityDescription(SensorEntityDescription):
    """Description for a care-log/sleep-feed sensor."""

    value_fn: Callable[[DiarySnapshot], object]
    attrs_fn: Callable[[DiarySnapshot], dict] | None = None


DIARY_SENSORS: tuple[NanitDiarySensorEntityDescription, ...] = (
    NanitDiarySensorEntityDescription(
        key="last_diaper_change",
        translation_key="last_diaper_change",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: _epoch_to_dt(s.latest("DIAPER_CHANGE").time if s.latest("DIAPER_CHANGE") else None),
        attrs_fn=lambda s: (
            {
                "change_type": (s.latest("DIAPER_CHANGE").raw.get("change_type")),
                "comment": s.latest("DIAPER_CHANGE").comment,
            }
            if s.latest("DIAPER_CHANGE")
            else {}
        ),
    ),
    NanitDiarySensorEntityDescription(
        key="diaper_changes_today",
        translation_key="diaper_changes_today",
        value_fn=lambda s: s.count_since("DIAPER_CHANGE", _local_midnight_epoch()),
        attrs_fn=lambda s: _diaper_breakdown(s, _local_midnight_epoch()),
    ),
    NanitDiarySensorEntityDescription(
        key="last_bottle_feed",
        translation_key="last_bottle_feed",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: _epoch_to_dt(s.latest("BOTTLE_FEED").time if s.latest("BOTTLE_FEED") else None),
        attrs_fn=lambda s: (
            {
                "amount_oz": round(
                    (s.latest("BOTTLE_FEED").raw.get("feed_amount") or 0) / _ML_PER_OZ, 2
                ),
                "comment": s.latest("BOTTLE_FEED").comment,
            }
            if s.latest("BOTTLE_FEED")
            else {}
        ),
    ),
    NanitDiarySensorEntityDescription(
        key="bottle_oz_today",
        translation_key="bottle_oz_today",
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.FLUID_OUNCES,
        suggested_display_precision=1,
        value_fn=lambda s: round(
            s.sum_since("BOTTLE_FEED", "feed_amount", _local_midnight_epoch()) / _ML_PER_OZ, 2
        ),
    ),
    NanitDiarySensorEntityDescription(
        key="last_nursing",
        translation_key="last_nursing",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: _epoch_to_dt(s.latest("NURSING").time if s.latest("NURSING") else None),
        attrs_fn=lambda s: (
            {
                "left_minutes": round((s.latest("NURSING").raw.get("left_duration") or 0) / 60, 1),
                "right_minutes": round((s.latest("NURSING").raw.get("right_duration") or 0) / 60, 1),
                "comment": s.latest("NURSING").comment,
            }
            if s.latest("NURSING")
            else {}
        ),
    ),
    NanitDiarySensorEntityDescription(
        key="last_sleep_transition",
        translation_key="last_sleep_transition",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: _epoch_to_dt(
            s.latest_sleep_transition().time if s.latest_sleep_transition() else None
        ),
        attrs_fn=lambda s: (
            {
                "asleep": s.latest_sleep_transition().key in ("FELL_ASLEEP", "PUT_IN_BED"),
                "event": s.latest_sleep_transition().key,
            }
            if s.latest_sleep_transition()
            else {}
        ),
    ),
)


class NanitDiarySensor(NanitDiaryEntity, SensorEntity):
    """Care-log/sleep-feed sensor, derived from the diary coordinator's snapshot."""

    entity_description: NanitDiarySensorEntityDescription

    def __init__(
        self,
        coordinator: NanitDiaryCoordinator,
        description: NanitDiarySensorEntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.baby.uid}_diary_{description.key}"

    @property
    def native_value(self):
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict:
        """Return type-specific attributes (change_type, amounts, durations, comment)."""
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return {}
        return self.entity_description.attrs_fn(self.coordinator.data)


class NanitSleepSummarySensor(NanitDiaryEntity, SensorEntity):
    """Most recent daily sleep-summary report from Nanit's own sleep scoring.

    Sourced from /feed's kind="summary" rows -- confirmed live 2026-08-25,
    already fetched every poll, no separate endpoint needed (a dedicated
    /sleep/digest endpoint also exists but 401s with the HA integration's
    own token, see diary_api.py). State is total sleep time for the most
    recent complete day/night period; the rest of Nanit's own stats block
    is exposed as attributes since several (sleep_score.score in particular)
    are frequently null and not safe to assume populated.
    """

    _attr_translation_key = "sleep_summary"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: NanitDiaryCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.baby.uid}_diary_sleep_summary"

    @staticmethod
    def _stats(snapshot: DiarySnapshot) -> dict:
        summary = snapshot.latest_summary()
        if summary is None:
            return {}
        details = summary.raw.get("summary_details")
        stats = details.get("stats") if isinstance(details, dict) else None
        return stats if isinstance(stats, dict) else {}

    @property
    def native_value(self) -> float | None:
        """Return total sleep time (minutes) for the latest summary period."""
        if self.coordinator.data is None:
            return None
        seconds = self._stats(self.coordinator.data).get("total_sleep_time")
        if not isinstance(seconds, int | float):
            return None
        return round(seconds / 60, 1)

    @property
    def extra_state_attributes(self) -> dict:
        """Return the period this covers plus Nanit's other daily stats, unconverted."""
        if self.coordinator.data is None:
            return {}
        stats = self._stats(self.coordinator.data)
        if not stats:
            return {}
        score = stats.get("sleep_score")
        return {
            "period_date": stats.get("period_date"),
            "period_part": stats.get("period_part"),
            "total_awake_time_min": round(stats["total_awake_time"] / 60, 1)
            if isinstance(stats.get("total_awake_time"), int | float)
            else None,
            "times_woke_up": stats.get("times_woke_up"),
            "sleep_sessions": stats.get("sleep_sessions"),
            "longest_sleep_min": round(stats["longest_sleep"] / 60, 1)
            if isinstance(stats.get("longest_sleep"), int | float)
            else None,
            "sleep_score": score.get("score") if isinstance(score, dict) else None,
        }


# --- 30-day rolling average sensors ---


@dataclass(frozen=True, kw_only=True)
class NanitRollingAverageSensorDescription(SensorEntityDescription):
    """Description for a rolling-average sensor."""

    value_fn: Callable[[RollingAverages], float]


ROLLING_AVERAGE_SENSORS: tuple[NanitRollingAverageSensorDescription, ...] = (
    NanitRollingAverageSensorDescription(
        key="avg_bottle_oz_30d",
        translation_key="avg_bottle_oz_30d",
        # No device_class: HA's VOLUME device class only permits
        # total_increasing/total state classes, not measurement -- and
        # measurement is the correct semantics for a rolling average
        # (a computed point-in-time stat, not a cumulative meter reading).
        native_unit_of_measurement=UnitOfVolume.FLUID_OUNCES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.bottle_oz_avg_per_day,
    ),
    NanitRollingAverageSensorDescription(
        key="avg_pees_30d",
        translation_key="avg_pees_30d",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.pees_avg_per_day,
    ),
    NanitRollingAverageSensorDescription(
        key="avg_poos_30d",
        translation_key="avg_poos_30d",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.poos_avg_per_day,
    ),
)


class NanitRollingAverageSensor(NanitRollingAverageEntity, SensorEntity):
    """30-day rolling-average sensor, from NanitRollingAverageCoordinator."""

    entity_description: NanitRollingAverageSensorDescription

    def __init__(
        self,
        coordinator: NanitRollingAverageCoordinator,
        description: NanitRollingAverageSensorDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.baby.uid}_diary_{description.key}"

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict:
        """Return the averaging window, so the number is never read without its basis."""
        if self.coordinator.data is None:
            return {}
        return {"window_days": self.coordinator.data.window_days}
