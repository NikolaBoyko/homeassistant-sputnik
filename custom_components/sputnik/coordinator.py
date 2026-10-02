"""DataUpdateCoordinator for the Sputnik integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthError, SputnikApiClient, SputnikCamera, SputnikProfile
from .const import DOMAIN, SCAN_INTERVAL_SECONDS, _LOGGER


@dataclass
class SputnikData:
    """Coordinator payload."""

    profile: SputnikProfile
    cameras: list[SputnikCamera] = field(default_factory=list)


class SputnikCoordinator(DataUpdateCoordinator[SputnikData]):
    """Poll profile and cameras."""

    def __init__(self, hass: HomeAssistant, client: SputnikApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.client = client

    async def _async_update_data(self) -> SputnikData:
        try:
            profile = await self.client.async_get_profile()
            # Fetch cameras separately if the profile didn't embed them.
            cameras = profile.cameras
            if not cameras and profile.address_id:
                cameras = await self.client.async_get_cameras(profile.address_id)
        except AuthError as err:
            # Bubble up so HA triggers a re-auth flow.
            raise err
        except Exception as err:
            raise UpdateFailed(f"Error communicating with Sputnik API: {err}") from err
        return SputnikData(profile=profile, cameras=cameras)
