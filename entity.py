"""Base entities for Nanit."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    NanitCloudCoordinator,
    NanitDiaryCoordinator,
    NanitNetworkCoordinator,
    NanitPushCoordinator,
    NanitRollingAverageCoordinator,
    NanitSoundLightCoordinator,
)
from .sanitize import display_name


def via_camera_device_id(coordinator, camera_uid: str | None) -> str | None:
    """The registry id of the baby's camera device, or None if there isn't one.

    WHY A LOOKUP AND NOT A TUPLE. `via_device_id` takes a device REGISTRY id;
    the identifier tuple that used to go to the deprecated `via_device` is not
    one, and the two fail differently. `via_device` resolved the tuple itself
    and, finding nothing, logged and left the device unparented.
    `via_device_id` raises DeviceInfoError on an id the registry does not
    hold, so "the camera is not registered yet" has to be answered here rather
    than handed to the registry to discover.

    Both misses are real and neither is an error:
      * a standalone Sound & Light Machine has no camera on the account at
        all, which is why hub.py nulls `via_camera_uid` for a speaker whose
        camera did not register this run; and
      * on a first-ever setup the camera platform may not have created the
        device before the diary entities are added.
    Returning None drops the parent link for this add and nothing else --
    the same outcome `via_device` produced, minus the deprecation.
    """
    if not camera_uid:
        return None
    device = dr.async_get(coordinator.hass).async_get_device_by_identifier(
        (DOMAIN, camera_uid), coordinator.config_entry.entry_id
    )
    return device.id if device is not None else None


class NanitEntity(CoordinatorEntity[NanitPushCoordinator]):
    """Base entity for Nanit — backed by the push coordinator."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.camera.uid)},
            name=display_name(self.coordinator.baby.name, self.coordinator.baby.uid),
            manufacturer="Nanit",
        )

    @property
    def available(self) -> bool:
        """Return True when the coordinator has data and camera is connected.

        Follows the Shelly pattern: both last_update_success and the WS
        connection flag must be True.
        """
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and self.coordinator.connected
        )


class NanitCloudEntity(CoordinatorEntity[NanitCloudCoordinator]):
    """Base entity for Nanit cloud-polled data."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.baby.camera_uid)},
            name=display_name(self.coordinator.baby.name, self.coordinator.baby.uid),
            manufacturer="Nanit",
        )


class NanitSoundLightEntity(CoordinatorEntity[NanitSoundLightCoordinator]):
    """Base entity for the Nanit Sound & Light Machine — backed by the push coordinator."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info — its own device, keyed by the speaker's uid.

        Linked to the baby's camera via via_device_id only when a camera
        exists on the account (standalone speakers have none).
        """
        baby = self.coordinator.baby
        info = DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.sound_light.speaker_uid)},
            name=f"{display_name(baby.name, baby.uid)} Sound & Light",
            manufacturer="Nanit",
            model="Sound & Light Machine",
        )
        if via_id := via_camera_device_id(
            self.coordinator, self.coordinator.via_camera_uid
        ):
            info["via_device_id"] = via_id
        return info

    @property
    def available(self) -> bool:
        """Return True when the coordinator has data and the device is reachable.

        Mirrors the camera entities (and HA quality-scale guidance): when we
        can't talk to the device, entities go unavailable rather than showing
        stale values as live. The coordinator's `connected` flag is debounced
        by a grace period, so brief reconnects don't flash "Unavailable". The
        connectivity binary sensor and connection-mode sensor override this
        so they can keep reporting the disconnected state.
        """
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and self.coordinator.connected
        )


class NanitNetworkEntity(CoordinatorEntity[NanitNetworkCoordinator]):
    """Base entity for network diagnostic sensors — backed by the network coordinator."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.baby.camera_uid)},
            name=display_name(self.coordinator.baby.name, self.coordinator.baby.uid),
            manufacturer="Nanit",
        )


class NanitDiaryEntity(CoordinatorEntity[NanitDiaryCoordinator]):
    """Base entity for care-log/sleep entities — backed by the diary coordinator.

    Its own device (not the camera's) since a diary entry can exist without a
    working camera, and multiple caregivers log to it independently of any
    single device. via_device_id links it under the baby's camera when one exists.
    """

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        baby = self.coordinator.baby
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{baby.uid}_diary")},
            name=f"{display_name(baby.name, baby.uid)} Care Log",
            manufacturer="Nanit",
        )
        if via_id := via_camera_device_id(self.coordinator, baby.camera_uid):
            info["via_device_id"] = via_id
        return info


class NanitRollingAverageEntity(CoordinatorEntity[NanitRollingAverageCoordinator]):
    """Base entity for 30-day-average entities — same Care Log device as NanitDiaryEntity."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info — same identifiers as NanitDiaryEntity, same device."""
        baby = self.coordinator.baby
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{baby.uid}_diary")},
            name=f"{display_name(baby.name, baby.uid)} Care Log",
            manufacturer="Nanit",
        )
        if via_id := via_camera_device_id(self.coordinator, baby.camera_uid):
            info["via_device_id"] = via_id
        return info
