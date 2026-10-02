"""Switch platform for the Sputnik integration (momentary door open)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, HomeAssistantError, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SputnikCoordinator
from .api import SputnikCamera

# How long (seconds) the switch stays "on" before auto-reset.
DOOR_OPEN_SECONDS = 2


@dataclass
class _DoorMeta:
    camera: SputnikCamera
    address_id: str


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sputnik door switches."""
    coordinator: SputnikCoordinator = entry.runtime_data
    address_id = coordinator.data.profile.address_id
    entities = [
        SputnikDoorSwitch(coordinator, _DoorMeta(camera=cam, address_id=address_id))
        for cam in coordinator.data.cameras
        if cam.device_uuid
    ]
    async_add_entities(entities)


class SputnikDoorSwitch(CoordinatorEntity[SputnikCoordinator], SwitchEntity):
    """Momentary switch that opens the intercom door."""

    _attr_should_poll = False
    _attr_icon = "mdi:door-open"
    _attr_has_entity_name = True

    def __init__(self, coordinator: SputnikCoordinator, meta: _DoorMeta) -> None:
        super().__init__(coordinator)
        self._meta = meta
        cam = meta.camera
        self._attr_name = "Open door"
        self._attr_unique_id = f"{DOMAIN}_{meta.address_id}_{cam.uuid}_door"
        self._attr_is_on = False
        self._device_uuid = cam.device_uuid

    @property
    def device_info(self) -> DeviceInfo:
        cam = self._meta.camera
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._meta.address_id}_{cam.uuid}")},
            name=cam.title or "Intercom",
            manufacturer="Sputnik",
            model="Intercom",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        # Don't reset is_on from coordinator updates — it manages itself.
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Open the door."""
        try:
            await self.coordinator.client.async_open_door(
                self._meta.address_id, self._device_uuid
            )
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Failed to open door: {err}") from err
        self._attr_is_on = True
        self.async_write_ha_state()
        self.async_on_remove(
            async_call_later(self.hass, DOOR_OPEN_SECONDS, self._auto_reset)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """No-op — door cannot be locked via this switch."""

    @callback
    def _auto_reset(self, _now: datetime) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
