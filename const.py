"""Constants for the Nanit integration."""

import logging

from homeassistant.const import Platform

DOMAIN = "nanit"
LOGGER = logging.getLogger(__package__)

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.LIGHT,
    Platform.SELECT,
    Platform.MEDIA_PLAYER,
    Platform.CAMERA,
]

# Cloud event detection window (seconds)
CLOUD_EVENT_WINDOW = 300

# Cloud event poll interval (seconds)
CLOUD_POLL_INTERVAL = 30

# Network info poll interval (seconds)
NETWORK_POLL_INTERVAL = 300

# Diary/feed (care log + sleep) poll interval (seconds)
DIARY_POLL_INTERVAL = 300

# How far back to fetch /diary/logs and /feed on every poll. Wide enough to
# cover "today" and "yesterday" stat buckets across any timezone offset, and
# to still show a last-event sensor after an overnight gap with no activity.
DIARY_LOOKBACK_HOURS = 72

# Rolling 30-day average poll interval (seconds). A 30-day average doesn't
# move meaningfully within a day, let alone every 5 minutes -- polling it
# on DIARY_POLL_INTERVAL's cadence would just be extra load on Nanit's API
# for no real freshness gain.
ROLLING_AVERAGE_POLL_INTERVAL = 21600  # 6 hours
ROLLING_AVERAGE_WINDOW_DAYS = 30

# Config Keys
CONF_MFA_CODE = "mfa_code"
CONF_MFA_TOKEN = "mfa_token"
# Legacy (dropped in config entry v2.2): referenced only to scrub the key
# from entries created by older versions.
CONF_STORE_CREDENTIALS = "store_credentials"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_BABY_UID = "baby_uid"
CONF_CAMERA_UID = "camera_uid"
CONF_BABY_NAME = "baby_name"
CONF_CAMERA_IP = "camera_ip"
CONF_CAMERA_IPS = "camera_ips"
CONF_SPEAKER_UID = "speaker_uid"
CONF_SPEAKER_IP = "speaker_ip"
CONF_SPEAKER_IPS = "speaker_ips"

# Default sound list (used when API doesn't return available_sounds)
DEFAULT_SOUND_MACHINE_SOUNDS = (
    "white_noise",
    "birds",
    "waves",
    "wind",
    "rain",
    "water_stream",
    "fan",
    "heartbeat",
    "dryer",
    "vacuum",
)
