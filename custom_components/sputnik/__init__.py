"""The Sputnik (sputnik.axmor.com) integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import ApiError, AuthError, SputnikApiClient
from .const import DEFAULT_APP_NAME, DEFAULT_APP_VERSION, DEFAULT_BASE_URL, DOMAIN
from .coordinator import SputnikCoordinator

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.CAMERA]

type SputnikConfigEntry = ConfigEntry[SputnikCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SputnikConfigEntry) -> bool:
    """Set up Sputnik from a config entry."""
    data = entry.data
    client = SputnikApiClient(
        base_url=data.get("base_url", DEFAULT_BASE_URL),
        jwt=data.get("jwt", ""),
        app_version=data.get("app_version", DEFAULT_APP_VERSION),
        app_name=data.get("app_name", DEFAULT_APP_NAME),
        phone=data.get("phone", ""),
    )

    coordinator = SputnikCoordinator(hass, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except AuthError as err:
        await client.close()
        raise ConfigEntryAuthFailed("Invalid credentials") from err
    except ApiError as err:
        await client.close()
        raise ConfigEntryNotReady(f"Cannot connect to Sputnik API: {err}") from err

    # Store coordinator in entry.runtime_data (modern HA pattern).
    entry.runtime_data = coordinator
    entry.async_on_unload(client.close)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SputnikConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
