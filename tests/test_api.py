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
    assert payload["direction_id"] == 1
    assert payload["cancelled"] is False
    assert set(payload) >= {"route", "headsign", "direction", "direction_id", "time", "minutes", "status"}


async def test_predictions_empty_stop_list() -> None:
    client = _StubClient({})
    assert await client.async_get_predictions([]) == {}


async def test_forking_line_uses_trip_headsign() -> None:
    """A train on a forking line shows its actual destination, not the combined one."""
    payload = {
        "data": [
            {
                "type": "prediction",
                "id": "x",
                "attributes": {
                    "departure_time": "2030-01-01T12:00:00-05:00",
                    "direction_id": 0,
                    "trip_headsign": None,
                },
                "relationships": {
                    "route": {"data": {"id": "Red", "type": "route"}},
                    "stop": {"data": {"id": "place-test", "type": "stop"}},
                    "trip": {"data": {"id": "t1", "type": "trip"}},
                },
            }
        ],
        "included": [
            {
                "type": "route",
                "id": "Red",
                "attributes": {
                    "long_name": "Red Line",
                    "type": 1,
                    "direction_names": ["South", "North"],
                    "direction_destinations": ["Ashmont/Braintree", "Alewife"],
                },
            },
            {"type": "trip", "id": "t1", "attributes": {"headsign": "Braintree"}},
        ],
    }
    client = _StubClient({"predictions": payload})
    result = await client.async_get_predictions(["place-test"])
    # Trip headsign wins over the route's combined "Ashmont/Braintree".
    assert result["place-test"][0].headsign == "Braintree"


async def test_route_labels_include_bus_number() -> None:
    """Bus routes show the number with the termini; subway lines stay clean."""

    def routes_payload(items):
        return {"data": [{"id": i, "attributes": a} for i, a in items]}

    bus = _StubClient(
        {
            "routes": routes_payload(
                [("1", {"short_name": "1", "long_name": "Harvard Square - Nubian Station"})]
            )
        }
    )
    assert (await bus.async_get_routes(3))[0]["name"] == (
        "1 — Harvard Square - Nubian Station"
    )

    subway = _StubClient(
        {"routes": routes_payload([("Red", {"short_name": "", "long_name": "Red Line"})])}
    )
    assert (await subway.async_get_routes(1))[0]["name"] == "Red Line"


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
    # short_header is preferred over header for the concise display field.
    assert serialised["header"] == "Red Line delays up to 15 min."
    # ...but the full header and description are available too.
    assert serialised["full_header"].startswith("Red Line is experiencing")
    assert serialised["description"] == "Due to a disabled train."
    # The combined human-readable text includes both the header and description.
    assert "Red Line is experiencing" in delay.text
    assert "Due to a disabled train." in delay.text


async def test_alerts_bucketed_per_stop() -> None:
    client = _StubClient({"alerts": load_fixture("alerts.json")})
    result = await client.async_get_alerts(["place-a", "place-b"])
    assert set(result) == {"place-a", "place-b"}
