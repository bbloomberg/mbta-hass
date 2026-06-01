"""Tests for the MBTA API client's parsing logic.

These exercise the real parsing code with recorded JSON:API payloads, so the
network layer (``_get``) is the only thing stubbed.
"""

from __future__ import annotations

from custom_components.mbta.api import MbtaApiClient

from .conftest import load_fixture


class _StubClient(MbtaApiClient):
    """An MbtaApiClient whose HTTP layer returns canned payloads by path."""

    def __init__(self, payloads: dict[str, dict]) -> None:
        super().__init__(session=None)
        self._payloads = payloads

    async def _get(self, path: str, params: dict) -> dict:
        return self._payloads[path]


async def test_predictions_parsing() -> None:
    client = _StubClient({"predictions": load_fixture("predictions.json")})

    result = await client.async_get_predictions(["place-test"])
    departures = result["place-test"]

    # p1 (child platform 70061 -> place-test) and p2 (direct) both bucket here;
    # p3 belongs to an unrelated stop and is dropped.
    assert len(departures) == 2

    first = departures[0]
    assert first.route_name == "Red Line"
    assert first.route_type == 1
    assert first.direction_name == "North"
    # trip_headsign was null -> falls back to the route's destination.
    assert first.headsign == "Alewife"
    assert first.minutes is not None and first.minutes >= 0
    assert first.is_cancelled is False

    # The cancelled trip is retained but flagged.
    cancelled = [d for d in departures if d.is_cancelled]
    assert len(cancelled) == 1
    assert cancelled[0].headsign == "Ashmont"


async def test_predictions_serialisation() -> None:
    client = _StubClient({"predictions": load_fixture("predictions.json")})
    result = await client.async_get_predictions(["place-test"])

    payload = result["place-test"][0].as_dict()
    assert payload["route"] == "Red Line"
    assert payload["direction"] == "North"
    assert payload["cancelled"] is False
    assert set(payload) >= {"route", "headsign", "direction", "time", "minutes", "status"}


async def test_predictions_empty_stop_list() -> None:
    client = _StubClient({})
    assert await client.async_get_predictions([]) == {}


async def test_alerts_parsing_and_active_filtering() -> None:
    client = _StubClient({"alerts": load_fixture("alerts.json")})

    result = await client.async_get_alerts(["place-test"])
    alerts = result["place-test"]

    # Both alerts parse...
    assert len(alerts) == 2
    delay = next(a for a in alerts if a.effect == "DELAY")
    expired = next(a for a in alerts if a.effect == "SHUTTLE")

    # ...but only the open-ended one is active now.
    assert delay.is_active() is True
    assert expired.is_active() is False

    serialised = delay.as_dict()
    assert serialised["effect"] == "DELAY"
    assert serialised["severity"] == 5
    # short_header is preferred over header for display.
    assert serialised["header"] == "Red Line delays up to 15 min."


async def test_alerts_bucketed_per_stop() -> None:
    client = _StubClient({"alerts": load_fixture("alerts.json")})
    result = await client.async_get_alerts(["place-a", "place-b"])
    assert set(result) == {"place-a", "place-b"}
