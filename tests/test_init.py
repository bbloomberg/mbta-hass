"""Tests for setup/unload and the entities the integration creates."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mbta.api import Alert, Departure
from custom_components.mbta.const import DOMAIN

from .conftest import load_fixture


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


def test_domain_objects_importable() -> None:
    # Guard against accidental rename of the public dataclasses.
    assert Departure.__name__ == "Departure"
    assert Alert.__name__ == "Alert"
