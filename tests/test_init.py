"""Tests for setup/unload and the entities the integration creates."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mbta.api import Alert, Departure
from custom_components.mbta.const import DOMAIN

from .conftest import load_fixture


def _departure(headsign: str, minutes: int) -> Departure:
    return Departure(
        route_id="1",
        route_name="1",
        route_type=3,
        route_color=None,
        direction_id=None,
        direction_name=None,
        headsign=headsign,
        time=dt_util.utcnow() + timedelta(minutes=minutes),
        status=None,
        is_cancelled=False,
    )


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="place-test",
        data={
            "api_key": None,
            "stops": [{"stop_id": "place-test", "stop_name": "Test Stop"}],
        },
    )


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    with patch.multiple(
        "custom_components.mbta.api.MbtaApiClient",
        async_get_predictions=AsyncMock(return_value={"place-test": []}),
        async_get_alerts=AsyncMock(return_value={"place-test": []}),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.test_stop_next_departure") is not None
    assert hass.states.get("binary_sensor.test_stop_service_alert") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_entities_reflect_data(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    # Build real domain objects from fixtures via the client parser.
    from custom_components.mbta.api import MbtaApiClient

    class _Stub(MbtaApiClient):
        async def _get(self, path, params):
            return load_fixture(f"{path}.json")

    parser = _Stub(session=None)
    predictions = await parser.async_get_predictions(["place-test"])
    alerts = await parser.async_get_alerts(["place-test"])

    with patch.multiple(
        "custom_components.mbta.api.MbtaApiClient",
        async_get_predictions=AsyncMock(return_value=predictions),
        async_get_alerts=AsyncMock(return_value=alerts),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    sensor = hass.states.get("sensor.test_stop_next_departure")
    assert sensor is not None
    # Next non-cancelled departure's minutes (large but >= 0; far-future fixture).
    assert sensor.state != "unknown"
    assert sensor.attributes["next_route"] == "Red Line"
    assert sensor.attributes["next_headsign"] == "Alewife"
    assert len(sensor.attributes["departures"]) == 2

    alert = hass.states.get("binary_sensor.test_stop_service_alert")
    assert alert is not None
    assert alert.state == "on"
    assert alert.attributes["has_delay"] is True
    assert "DELAY" in alert.attributes["effects"]
    # Full alert text is surfaced for display.
    assert "Red Line is experiencing" in alert.attributes["alert_text"]


async def test_bus_stop_merges_both_directions(hass: HomeAssistant) -> None:
    """A stop bundling two ids (both bus directions) shows both in one sensor."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="101_73",
        data={
            "api_key": None,
            "stops": [
                {
                    "stop_id": "101",
                    "stop_name": "Sidney",
                    "stop_ids": ["101", "73"],
                }
            ],
        },
    )
    entry.add_to_hass(hass)

    # Each underlying id serves one direction.
    predictions = {
        "101": [_departure("Harvard", 5)],
        "73": [_departure("Nubian", 3)],
    }
    with patch.multiple(
        "custom_components.mbta.api.MbtaApiClient",
        async_get_predictions=AsyncMock(return_value=predictions),
        async_get_alerts=AsyncMock(return_value={"101": [], "73": []}),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    sensor = hass.states.get("sensor.sidney_next_departure")
    assert sensor is not None
    headsigns = {d["headsign"] for d in sensor.attributes["departures"]}
    assert headsigns == {"Harvard", "Nubian"}
    # The sooner direction (Nubian, 3 min) is surfaced as "next".
    assert sensor.attributes["next_headsign"] == "Nubian"


async def test_departures_capped_per_destination(hass: HomeAssistant) -> None:
    """The departures attribute keeps up to max_departures of *each* destination.

    A flat slice would be dominated by the more frequent direction; the per-
    destination cap keeps both represented so the card can group them.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="stop-x",
        data={"api_key": None, "stops": [{"stop_id": "stop-x", "stop_name": "X"}]},
        options={"max_departures": 3},
    )
    entry.add_to_hass(hass)

    # Harvard runs far more frequently than Nubian (interleaved by time).
    deps = [
        _departure("Harvard", 1),
        _departure("Harvard", 2),
        _departure("Nubian", 3),
        _departure("Harvard", 4),
        _departure("Harvard", 5),
        _departure("Nubian", 6),
        _departure("Harvard", 7),
        _departure("Harvard", 8),
        _departure("Nubian", 9),
    ]
    with patch.multiple(
        "custom_components.mbta.api.MbtaApiClient",
        async_get_predictions=AsyncMock(return_value={"stop-x": deps}),
        async_get_alerts=AsyncMock(return_value={"stop-x": []}),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    sensor = hass.states.get("sensor.x_next_departure")
    assert sensor is not None
    shown = sensor.attributes["departures"]
    headsigns = [d["headsign"] for d in shown]
    # 3 of each destination (not 3 total dominated by Harvard).
    assert headsigns.count("Harvard") == 3
    assert headsigns.count("Nubian") == 3
    # Time order is preserved within the capped set.
    assert headsigns[0] == "Harvard" and headsigns[2] == "Nubian"


def test_domain_objects_importable() -> None:
    # Guard against accidental rename of the public dataclasses.
    assert Departure.__name__ == "Departure"
    assert Alert.__name__ == "Alert"
