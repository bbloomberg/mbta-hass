"""Data update coordinator for the MBTA integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Alert, Departure, MbtaApiClient, MbtaApiError, MbtaAuthError
from .const import (
    CONF_API_KEY,
    CONF_SCAN_INTERVAL,
    CONF_STOPS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class MbtaData:
    """Coordinator payload: predictions and alerts keyed by stop id."""

    predictions: dict[str, list[Departure]]
    alerts: dict[str, list[Alert]]


class MbtaCoordinator(DataUpdateCoordinator[MbtaData]):
    """Fetch predictions and alerts for all configured stops in one pass."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        interval = entry.options.get(CONF_SCAN_INTERVAL)
        update_interval = (
            timedelta(seconds=interval)
            if interval
            else DEFAULT_SCAN_INTERVAL
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=update_interval,
        )
        self.client = MbtaApiClient(
            async_get_clientsession(hass),
            api_key=entry.data.get(CONF_API_KEY),
        )

    @property
    def stops(self) -> list[dict[str, str]]:
        """Configured stops as ``[{stop_id, stop_name}]`` (options override data)."""
        return self.entry.options.get(CONF_STOPS) or self.entry.data.get(CONF_STOPS, [])

    @property
    def stop_ids(self) -> list[str]:
        return [s["stop_id"] for s in self.stops]

    async def _async_update_data(self) -> MbtaData:
        stop_ids = self.stop_ids
        try:
            predictions = await self.client.async_get_predictions(stop_ids)
            alerts = await self.client.async_get_alerts(stop_ids)
        except MbtaAuthError as err:
            raise UpdateFailed(f"Authentication error: {err}") from err
        except MbtaApiError as err:
            raise UpdateFailed(str(err)) from err
        return MbtaData(predictions=predictions, alerts=alerts)
