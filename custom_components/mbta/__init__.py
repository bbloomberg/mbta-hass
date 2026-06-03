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
from homeassistant.setup import async_when_setup

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
        # Load it as a frontend module (works in YAML and storage dashboards,
        # and covers the very first boot before the resource below is stored).
        frontend.add_extra_js_url(hass, card_url)
        # Also add it as a persistent Lovelace resource once lovelace is ready.
        # Resources load with the dashboard bootstrap — before views render —
        # and persist in storage, so on later boots the card is available even
        # before this integration finishes setting up. That is the reliable
        # path for first paint and the Android companion app. The same versioned
        # URL is reused, so the module is still evaluated only once.
        async_when_setup(hass, "lovelace", _async_when_lovelace_ready(card_url))
    except Exception:  # noqa: BLE001 - never let card setup break the integration
        _LOGGER.warning(
            "Could not register the MBTA arrival-board card; the integration "
            "will still work, but the custom card may need manual installation",
            exc_info=True,
        )


def _async_when_lovelace_ready(card_url: str):
    """Build the async_when_setup callback that registers the Lovelace resource."""

    async def _callback(hass: HomeAssistant, _component: str) -> None:
        await _async_register_lovelace_resource(hass, card_url)

    return _callback


def _lovelace_resources(hass: HomeAssistant):
    """Return the editable (storage-mode) Lovelace resource collection, or None.

    YAML-mode dashboards manage resources themselves and can't be edited
    programmatically; those users are served by the extra-module URL instead.
    """
    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if resources is None and isinstance(lovelace, dict):
        resources = lovelace.get("resources")
    # Storage-mode collections have a ``store``; YAML-mode ones do not.
    if resources is None or getattr(resources, "store", None) is None:
        return None
    return resources


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> None:
    """Add or refresh the bundled card in the Lovelace resource registry."""
    resources = _lovelace_resources(hass)
    if resources is None:
        return
    try:
        if hasattr(resources, "loaded") and not resources.loaded:
            await resources.async_get_info()
        base = url.split("?", 1)[0]
        ours = [
            item
            for item in resources.async_items()
            if str(item.get("url", "")).split("?", 1)[0] == base
        ]
        if ours:
            # Keep a single entry; update it to the current (versioned) URL and
            # drop any duplicates left by earlier versions.
            if ours[0].get("url") != url:
                await resources.async_update_item(
                    ours[0]["id"], {"res_type": "module", "url": url}
                )
            for dup in ours[1:]:
                await resources.async_delete_item(dup["id"])
        else:
            await resources.async_create_item({"res_type": "module", "url": url})
    except Exception:  # noqa: BLE001
        _LOGGER.debug(
            "Could not register the MBTA card as a Lovelace resource", exc_info=True
        )


async def _async_remove_lovelace_resource(hass: HomeAssistant) -> None:
    """Remove the bundled card from the Lovelace resource registry."""
    resources = _lovelace_resources(hass)
    if resources is None:
        return
    try:
        if hasattr(resources, "loaded") and not resources.loaded:
            await resources.async_get_info()
        for item in list(resources.async_items()):
            if str(item.get("url", "")).split("?", 1)[0] == CARD_URL_PATH:
                await resources.async_delete_item(item["id"])
    except Exception:  # noqa: BLE001
        _LOGGER.debug(
            "Could not remove the MBTA card Lovelace resource", exc_info=True
        )


async def async_unload_entry(hass: HomeAssistant, entry: MbtaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: MbtaConfigEntry) -> None:
    """When the last entry is deleted, remove the bundled card resource too."""
    remaining = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if remaining:
        return
    await _async_remove_lovelace_resource(hass)
    hass.data.pop(_FRONTEND_REGISTERED, None)


async def _async_update_listener(hass: HomeAssistant, entry: MbtaConfigEntry) -> None:
    """Reload the entry when options (stops, interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)
