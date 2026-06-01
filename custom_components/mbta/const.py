"""Constants for the MBTA integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "mbta"

# Config / options keys
CONF_API_KEY: Final = "api_key"
CONF_STOPS: Final = "stops"
CONF_STOP_ID: Final = "stop_id"
CONF_STOP_NAME: Final = "stop_name"
CONF_ROUTE_TYPE: Final = "route_type"
CONF_ROUTE_ID: Final = "route_id"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_MAX_DEPARTURES: Final = "max_departures"

# Defaults
DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=60)
DEFAULT_MAX_DEPARTURES: Final = 5
MIN_SCAN_INTERVAL_SECONDS: Final = 20

# MBTA API
MBTA_BASE_URL: Final = "https://api-v3.mbta.com"

# route_type values from the GTFS spec, as used by the MBTA V3 API.
ROUTE_TYPE_LIGHT_RAIL: Final = 0
ROUTE_TYPE_HEAVY_RAIL: Final = 1
ROUTE_TYPE_COMMUTER_RAIL: Final = 2
ROUTE_TYPE_BUS: Final = 3
ROUTE_TYPE_FERRY: Final = 4

ROUTE_TYPE_NAMES: Final[dict[int, str]] = {
    ROUTE_TYPE_LIGHT_RAIL: "Light Rail (Green/Mattapan)",
    ROUTE_TYPE_HEAVY_RAIL: "Subway (Red/Orange/Blue)",
    ROUTE_TYPE_COMMUTER_RAIL: "Commuter Rail",
    ROUTE_TYPE_BUS: "Bus",
    ROUTE_TYPE_FERRY: "Ferry",
}

# Alert effects that we treat as service disruptions worth surfacing.
DISRUPTION_EFFECTS: Final[frozenset[str]] = frozenset(
    {
        "DELAY",
        "SUSPENSION",
        "SHUTTLE",
        "CANCELLATION",
        "DETOUR",
        "SERVICE_CHANGE",
        "SNOW_ROUTE",
        "STOP_CLOSURE",
        "STATION_CLOSURE",
        "TRACK_CHANGE",
        "SCHEDULE_CHANGE",
        "REDUCED_SERVICE",
        "ADDITIONAL_SERVICE",
        "NO_SERVICE",
    }
)

ATTRIBUTION: Final = "Data provided by the Massachusetts Bay Transportation Authority (MBTA)"
