"""Data update coordinator for the MBTA integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Alert, Departure, MbtaApiClient, MbtaApiError, MbtaAuthError
from .const import (
    CONF_API_KEY,
    CONF_SCAN_INTERVAL,
    CONF_STOP_ID,
    CONF_STOP_IDS,
    CONF_STOPS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def stop_entry_ids(stop: dict) -> list[str]:
    """Underlying MBTA stop ids for a configured stop.

    A configured stop may bundle several real stop ids — e.g. the two
    directions of a bus stop, which MBTA models as separate ids with no parent
    station. Falls back to the single primary id for older/simple entries.
    """
    ids = stop.get(CONF_STOP_IDS)
    if ids:
        return list(ids)
    return [stop[CONF_STOP_ID]]


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
        """Every underlying MBTA stop id across all configured stops."""
        return sorted({sid for s in self.stops for sid in stop_entry_ids(s)})

    async def _async_update_data(self) -> MbtaData:
        try:
            raw_predictions = await self.client.async_get_predictions(self.stop_ids)
            raw_alerts = await self.client.async_get_alerts(self.stop_ids)
        except MbtaAuthError as err:
            raise UpdateFailed(f"Authentication error: {err}") from err
        except MbtaApiError as err:
            raise UpdateFailed(str(err)) from err

        predictions: dict[str, list[Departure]] = {}
        alerts: dict[str, list[Alert]] = {}
        for stop in self.stops:
            primary = stop[CONF_STOP_ID]
            ids = stop_entry_ids(stop)

            departures: list[Departure] = []
            for sid in ids:
                departures.extend(raw_predictions.get(sid, []))
            departures.sort(key=lambda d: (d.time is None, d.time or dt_util.utcnow()))
            predictions[primary] = departures

            merged: list[Alert] = []
            seen: set[str] = set()
            for sid in ids:
                for alert in raw_alerts.get(sid, []):
                    if alert.alert_id not in seen:
                        seen.add(alert.alert_id)
                        merged.append(alert)
            alerts[primary] = merged

        return MbtaData(predictions=predictions, alerts=alerts)
