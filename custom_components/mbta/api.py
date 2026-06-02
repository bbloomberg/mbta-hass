"""Async client for the MBTA V3 API.

Docs: https://api-v3.mbta.com/docs/swagger/index.html

Only the small slice of the API the integration needs is implemented here:
predictions, alerts, routes and stops. Everything is returned as plain dicts /
dataclasses so the rest of the integration never has to know about JSON:API.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import aiohttp
import async_timeout

from homeassistant.util import dt as dt_util

from .const import MBTA_BASE_URL

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


class MbtaApiError(Exception):
    """Generic MBTA API error."""


class MbtaAuthError(MbtaApiError):
    """Raised when the API key is rejected."""


@dataclass(slots=True)
class Departure:
    """A single upcoming departure (or arrival) at a stop."""

    route_id: str
    route_name: str
    route_type: int
    route_color: str | None
    direction_id: int | None
    direction_name: str | None
    headsign: str | None
    time: datetime | None
    status: str | None
    is_cancelled: bool

    @property
    def minutes(self) -> int | None:
        """Whole minutes from now until this departure (None if unknown/past)."""
        if self.time is None:
            return None
        delta = (self.time - dt_util.utcnow()).total_seconds()
        if delta < 0:
            return 0
        return int(delta // 60)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for use as a Home Assistant attribute."""
        return {
            "route": self.route_name,
            "route_id": self.route_id,
            "headsign": self.headsign,
            "direction": self.direction_name,
            "time": self.time.isoformat() if self.time else None,
            "minutes": self.minutes,
            "status": self.status,
            "cancelled": self.is_cancelled,
        }


@dataclass(slots=True)
class Alert:
    """A service alert affecting a stop."""

    alert_id: str
    effect: str
    severity: int | None
    lifecycle: str | None
    header: str | None
    short_header: str | None
    description: str | None
    url: str | None
    active_periods: list[tuple[datetime | None, datetime | None]] = field(
        default_factory=list
    )

    def is_active(self, now: datetime | None = None) -> bool:
        """Whether the alert is in effect right now."""
        now = now or dt_util.utcnow()
        if not self.active_periods:
            # No bounded period means the alert is current.
            return True
        for start, end in self.active_periods:
            if start is not None and now < start:
                continue
            if end is not None and now > end:
                continue
            return True
        return False

    @property
    def text(self) -> str:
        """Human-readable alert text: full header plus description if extra."""
        head = self.header or self.short_header or self.effect.title()
        desc = (self.description or "").strip()
        if desc and desc not in (head or ""):
            return f"{head}\n{desc}"
        return head

    def as_dict(self) -> dict[str, Any]:
        """Serialise for use as a Home Assistant attribute."""
        return {
            "effect": self.effect,
            "severity": self.severity,
            "lifecycle": self.lifecycle,
            "header": self.short_header or self.header,
            "full_header": self.header,
            "description": self.description,
            "text": self.text,
            "url": self.url,
        }


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    # Normalise to UTC so arithmetic against utcnow() is correct.
    return dt_util.as_utc(parsed)


class MbtaApiClient:
    """Thin async wrapper around the MBTA V3 API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str | None = None,
    ) -> None:
        self._session = session
        self._api_key = api_key or None

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"Accept": "application/vnd.api+json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        url = f"{MBTA_BASE_URL}/{path}"
        try:
            async with async_timeout.timeout(REQUEST_TIMEOUT):
                resp = await self._session.get(url, params=params, headers=headers)
        except asyncio.TimeoutError as err:
            raise MbtaApiError(f"Timeout talking to MBTA API ({path})") from err
        except aiohttp.ClientError as err:
            raise MbtaApiError(f"Error talking to MBTA API: {err}") from err

        if resp.status in (401, 403):
            raise MbtaAuthError("MBTA API rejected the API key")
        if resp.status == 429:
            raise MbtaApiError("MBTA API rate limit exceeded (HTTP 429)")
        if resp.status >= 400:
            text = await resp.text()
            raise MbtaApiError(f"MBTA API error {resp.status}: {text[:200]}")
        return await resp.json()

    # ------------------------------------------------------------------ #
    # Config-flow helpers
    # ------------------------------------------------------------------ #

    async def async_validate_key(self) -> None:
        """Make a cheap call to confirm the API key (if any) is accepted."""
        await self._get("routes", {"page[limit]": 1})

    async def async_get_routes(self, route_type: int) -> list[dict[str, Any]]:
        """Return routes of a given type as ``[{id, name}]`` sorted by name."""
        data = await self._get(
            "routes",
            {
                "filter[type]": route_type,
                "fields[route]": "long_name,short_name,sort_order",
                "sort": "sort_order",
            },
        )
        routes: list[dict[str, Any]] = []
        for item in data.get("data", []):
            attrs = item.get("attributes", {})
            short = (attrs.get("short_name") or "").strip()
            long_name = (attrs.get("long_name") or "").strip()
            # Show the route number alongside the termini for buses (e.g.
            # "1 — Harvard Square - Nubian Station"), while leaving subway lines
            # that already carry the name in long_name alone (e.g. "Red Line").
            if short and short not in long_name:
                name = f"{short} — {long_name}" if long_name else short
            else:
                name = long_name or short or item["id"]
            routes.append({"id": item["id"], "name": name})
        return routes

    async def async_get_stops_for_route(self, route_id: str) -> list[dict[str, Any]]:
        """Return stops served by a route as ``[{id, name}]`` ordered as on the line."""
        data = await self._get(
            "stops",
            {
                "filter[route]": route_id,
                "fields[stop]": "name",
            },
        )
        stops: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in data.get("data", []):
            stop_id = item["id"]
            if stop_id in seen:
                continue
            seen.add(stop_id)
            stops.append(
                {"id": stop_id, "name": item.get("attributes", {}).get("name", stop_id)}
            )
        return stops

    # ------------------------------------------------------------------ #
    # Runtime data
    # ------------------------------------------------------------------ #

    async def async_get_predictions(
        self, stop_ids: list[str]
    ) -> dict[str, list[Departure]]:
        """Return upcoming departures bucketed by stop id."""
        if not stop_ids:
            return {}
        data = await self._get(
            "predictions",
            {
                "filter[stop]": ",".join(stop_ids),
                "include": "route,trip,stop",
                "sort": "departure_time",
                "page[limit]": 100,
            },
        )
        included = _index_included(data.get("included", []))
        result: dict[str, list[Departure]] = {stop_id: [] for stop_id in stop_ids}

        for item in data.get("data", []):
            attrs = item.get("attributes", {})
            rels = item.get("relationships", {})

            stop_ref = _rel_id(rels, "stop")
            # The stop a prediction is attached to may be a child platform of the
            # configured parent station; map it back when possible.
            bucket = _resolve_stop_bucket(stop_ref, stop_ids, included)
            if bucket is None:
                continue

            route_id = _rel_id(rels, "route") or ""
            route = included.get(("route", route_id), {})
            route_attrs = route.get("attributes", {})
            route_name = (
                route_attrs.get("short_name")
                or route_attrs.get("long_name")
                or route_id
            )
            route_color = route_attrs.get("color")
            route_type = route_attrs.get("type")

            trip_id = _rel_id(rels, "trip") or ""
            trip = included.get(("trip", trip_id), {})
            trip_headsign = trip.get("attributes", {}).get("headsign")
            # Prefer the trip's own headsign so forking lines show the actual
            # destination of each train (e.g. "Ashmont" or "Braintree", rather
            # than the combined "Ashmont/Braintree"). Fall back to the route's
            # destination for this direction when the trip headsign is missing.
            headsign = (
                trip_headsign
                or attrs.get("trip_headsign")
                or _direction_destination(route_attrs, attrs.get("direction_id"))
            )

            schedule_rel = attrs.get("schedule_relationship")
            cancelled = schedule_rel in ("CANCELLED", "SKIPPED", "NO_DATA")

            time = _parse_dt(attrs.get("departure_time")) or _parse_dt(
                attrs.get("arrival_time")
            )
            # Skip stale/irrelevant predictions with no usable time, unless they
            # represent a cancellation we want to surface.
            if time is None and not cancelled:
                continue

            direction_id = attrs.get("direction_id")
            result[bucket].append(
                Departure(
                    route_id=route_id,
                    route_name=route_name,
                    route_type=route_type if isinstance(route_type, int) else -1,
                    route_color=route_color,
                    direction_id=direction_id,
                    direction_name=_direction_name(route_attrs, direction_id),
                    headsign=headsign,
                    time=time,
                    status=attrs.get("status"),
                    is_cancelled=cancelled,
                )
            )

        for departures in result.values():
            departures.sort(
                key=lambda d: (d.time is None, d.time or dt_util.utcnow())
            )
        return result

    async def async_get_alerts(self, stop_ids: list[str]) -> dict[str, list[Alert]]:
        """Return active alerts bucketed by stop id.

        Queried one stop at a time (concurrently) so each alert is attributed to
        exactly the stop it affects — a single batched call cannot tell a
        route-wide alert apart for stops on different routes.
        """
        if not stop_ids:
            return {}
        results = await asyncio.gather(
            *(self._async_get_alerts_for_stop(stop_id) for stop_id in stop_ids)
        )
        return dict(zip(stop_ids, results))

    async def _async_get_alerts_for_stop(self, stop_id: str) -> list[Alert]:
        data = await self._get(
            "alerts",
            {
                "filter[stop]": stop_id,
                "filter[datetime]": "NOW",
            },
        )
        alerts: list[Alert] = []
        for item in data.get("data", []):
            attrs = item.get("attributes", {})
            periods = [
                (_parse_dt(p.get("start")), _parse_dt(p.get("end")))
                for p in attrs.get("active_period", []) or []
            ]
            alerts.append(
                Alert(
                    alert_id=item["id"],
                    effect=attrs.get("effect", "UNKNOWN"),
                    severity=attrs.get("severity"),
                    lifecycle=attrs.get("lifecycle"),
                    header=attrs.get("header"),
                    short_header=attrs.get("short_header"),
                    description=attrs.get("description"),
                    url=attrs.get("url"),
                    active_periods=periods,
                )
            )
        return alerts


# ---------------------------------------------------------------------- #
# JSON:API helpers
# ---------------------------------------------------------------------- #


def _index_included(included: list[dict[str, Any]]) -> dict[tuple[str, str], dict]:
    return {(item["type"], item["id"]): item for item in included}


def _rel_id(relationships: dict[str, Any], name: str) -> str | None:
    rel = relationships.get(name)
    if not rel:
        return None
    data = rel.get("data")
    if not data:
        return None
    return data.get("id")


def _resolve_stop_bucket(
    stop_ref: str | None,
    stop_ids: list[str],
    included: dict[tuple[str, str], dict],
) -> str | None:
    """Map a prediction's stop (possibly a child platform) to a configured stop."""
    if stop_ref is None:
        return None
    if stop_ref in stop_ids:
        return stop_ref
    # Walk up to the parent station if the child platform isn't one we track.
    stop = included.get(("stop", stop_ref))
    if stop:
        parent = _rel_id(stop.get("relationships", {}), "parent_station")
        if parent and parent in stop_ids:
            return parent
    return None


def _direction_name(route_attrs: dict[str, Any], direction_id: int | None) -> str | None:
    if direction_id is None:
        return None
    names = route_attrs.get("direction_names") or []
    if 0 <= direction_id < len(names):
        return names[direction_id]
    return None


def _direction_destination(
    route_attrs: dict[str, Any], direction_id: int | None
) -> str | None:
    if direction_id is None:
        return None
    dests = route_attrs.get("direction_destinations") or []
    if 0 <= direction_id < len(dests):
        return dests[direction_id]
    return None
