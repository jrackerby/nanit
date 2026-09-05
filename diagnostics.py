"""Diagnostics support for Nanit."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import NanitConfigEntry

TO_REDACT = {
    "email",
    "password",
    "access_token",
    "refresh_token",
    "mfa_token",
    "mfa_code",
    "baby_name",
    "baby_uid",
    "camera_uid",
    "camera_ip",
    "camera_ips",
    "speaker_ip",
    "speaker_ips",
    "speaker_uid",
    "speaker_uid_map",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NanitConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    # Sections are keyed by index, not by device uid: async_redact_data
    # redacts values under matching key names but never dict keys, so
    # uid-keyed sections would leak the very uids TO_REDACT lists.
    cameras_diag: dict[str, Any] = {}

    for index, cam_data in enumerate(entry.runtime_data.cameras.values()):
        push = cam_data.push_coordinator
        cloud = cam_data.cloud_coordinator

        cam_diag: dict[str, Any] = {
            "baby_name": cam_data.baby.name,
            "baby_uid": cam_data.baby.uid,
            "push_coordinator": {
                "last_update_success": push.last_update_success,
                "last_exception": (str(push.last_exception) if push.last_exception else None),
                "connected": push.connected,
                "data": asdict(push.data) if push.data is not None else None,
            },
        }

        if cloud is not None:
            cam_diag["cloud_coordinator"] = {
                "last_update_success": cloud.last_update_success,
                "last_exception": (str(cloud.last_exception) if cloud.last_exception else None),
                "data": ([asdict(e) for e in cloud.data] if cloud.data is not None else None),
            }

        cameras_diag[f"camera_{index}"] = cam_diag

    speakers_diag: dict[str, Any] = {}
    for index, speaker_data in enumerate(entry.runtime_data.speakers.values()):
        coordinator = speaker_data.coordinator
        speakers_diag[f"speaker_{index}"] = {
            "baby_name": speaker_data.baby.name,
            "baby_uid": speaker_data.baby.uid,
            "connected": coordinator.connected,
            "connection_mode": speaker_data.sound_light.connection_mode,
            "last_update_success": coordinator.last_update_success,
            "data": asdict(coordinator.data) if coordinator.data is not None else None,
        }

    # Counts and health only — never comments, timestamps or amounts. This is
    # a child's care record; diagnostics bundles get uploaded for support.
    diaries_diag: dict[str, Any] = {}
    for index, diary_data in enumerate(entry.runtime_data.diaries.values()):
        coordinator = diary_data.coordinator
        snapshot = coordinator.data
        diaries_diag[f"diary_{index}"] = {
            "baby_name": diary_data.baby.name,
            "baby_uid": diary_data.baby.uid,
            "last_update_success": coordinator.last_update_success,
            "last_exception": (
                str(coordinator.last_exception) if coordinator.last_exception else None
            ),
            "diary_entry_count": len(snapshot.diary_entries) if snapshot else None,
            "diary_entry_types": (sorted({e.type for e in snapshot.diary_entries}) if snapshot else None),
            "feed_event_count": len(snapshot.feed_events) if snapshot else None,
            "is_asleep": snapshot.is_asleep() if snapshot else None,
            "rolling_average": (
                {
                    "last_update_success": diary_data.rolling.last_update_success,
                    "window_days": diary_data.rolling.data.window_days if diary_data.rolling.data else None,
                }
                if diary_data.rolling is not None
                else None
            ),
        }

    return {
        "config_entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "config_entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "camera_count": len(cameras_diag),
        "cameras": async_redact_data(cameras_diag, TO_REDACT),
        "speaker_count": len(speakers_diag),
        "speakers": async_redact_data(speakers_diag, TO_REDACT),
        "diary_count": len(diaries_diag),
        "diaries": async_redact_data(diaries_diag, TO_REDACT),
    }
