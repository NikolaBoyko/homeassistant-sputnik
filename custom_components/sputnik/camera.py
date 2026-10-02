"""Camera platform for the Sputnik integration."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SputnikCoordinator
from .api import SputnikCamera


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sputnik cameras."""
    coordinator: SputnikCoordinator = entry.runtime_data
    entities = [
        SputnikCameraEntity(coordinator, cam)
        for cam in coordinator.data.cameras
        if cam.stream_url
    ]
    async_add_entities(entities)


class SputnikCameraEntity(CoordinatorEntity[SputnikCoordinator], Camera):
    """IP camera entity backed by the profile stream_url."""

    _attr_has_entity_name = True
    _attr_name = "Camera"

    def __init__(self, coordinator: SputnikCoordinator, cam: SputnikCamera) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._cam = cam
        self._attr_unique_id = f"{DOMAIN}_{cam.uuid}_camera"
        if cam.title:
            self._attr_name = cam.title
        # Advertise streaming support so the frontend uses stream_source.
        self._attr_supported_features = CameraEntityFeature.STREAM

    @property
    def device_info(self) -> DeviceInfo:
        address_id = self.coordinator.data.profile.address_id
        return DeviceInfo(
            identifiers={(DOMAIN, f"{address_id}_{self._cam.uuid}")},
            name=self._cam.title or "Intercom",
            manufacturer="Sputnik",
            model="Intercom",
        )

    @property
    def is_recording(self) -> bool:
        return False

    @property
    def is_streaming(self) -> bool:
        return bool(self._cam.stream_url)

    async def stream_source(self) -> str | None:
        """Return the stream URL for the HA stream component."""
        # Sync from coordinator so we always use the freshest URL.
        for cam in self.coordinator.data.cameras:
            if cam.uuid == self._cam.uuid:
                self._cam = cam
                break
        return self._cam.stream_url or None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the thumb/preview URL, if available."""
        thumb = self._cam.thumb
        if not thumb:
            return None
        try:
            session = await self.coordinator.client._get_session()  # noqa: SLF001
            async with session.get(thumb) as resp:
                if resp.ok:
                    return await resp.read()
        except Exception:  # noqa: BLE001
            pass
        return None
