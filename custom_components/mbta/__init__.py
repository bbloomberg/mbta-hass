"""The MBTA integration."""

from __future__ import annotations

import logging
import os

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .coordinator import MbtaCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

MbtaConfigEntry = ConfigEntry[MbtaCoordinator]

# The bundled Lovelace arrival-board card.
CARD_FILENAME = "mbta-arrival-board-card.js"
CARD_URL_PATH = f"/{DOMAIN}_static/{CARD_FILENAME}"
_FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the bundled card as early as possible.

    Doing this in ``async_setup`` (rather than only on config-entry setup) means
    the resource is served and added to the frontend before any dashboard — or
    the Android companion app's webview — first requests it. That avoids the
    first-load race where the card element isn't defined yet, which the
    companion app's service worker can otherwise cache as a failure and never
    recover from until the cache is cleared.
    """
    await _async_register_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MbtaConfigEntry) -> bool:
    """Set up MBTA from a config entry."""
    await _async_register_card(hass)

    coordinator = MbtaCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve and auto-load the bundled arrival-board card (once per HA instance)."""
    if hass.data.get(_FRONTEND_REGISTERED):
        return
    # Serving the file needs the HTTP component; auto-loading it needs the
    # frontend. Both are always present in a real HA instance but may be absent
    # in headless/test environments — skip quietly in that case.
    if getattr(hass, "http", None) is None:
        return
    hass.data[_FRONTEND_REGISTERED] = True

    # Append the integration version so each release gets a fresh URL — this
    # busts stale frontend caches (and any cached failure) in browsers and in
    # the companion app's service worker.
    try:
        integration = await async_get_integration(hass, DOMAIN)
        version = str(integration.version) if integration.version else ""
    except Exception:  # noqa: BLE001
        version = ""
    card_url = f"{CARD_URL_PATH}?v={version}" if version else CARD_URL_PATH

    card_path = os.path.join(os.path.dirname(__file__), "www", CARD_FILENAME)
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL_PATH, card_path, cache_headers=False)]
        )
        frontend.add_extra_js_url(hass, card_url)
    except Exception:  # noqa: BLE001 - never let card setup break the integration
        _LOGGER.warning(
            "Could not register the MBTA arrival-board card; the integration "
            "will still work, but the custom card may need manual installation",
            exc_info=True,
        )


async def async_unload_entry(hass: HomeAssistant, entry: MbtaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: MbtaConfigEntry) -> None:
    """Reload the entry when options (stops, interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)
