"""Tests for the MBTA config and reconfigure flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mbta.const import DOMAIN


def _patch_api(**overrides):
    """Patch every network method on MbtaApiClient with safe defaults."""
    defaults = {
        "async_validate_key": AsyncMock(return_value=None),
        "async_get_routes": AsyncMock(
            return_value=[{"id": "Red", "name": "Red Line"}]
        ),
        "async_get_stops_for_route": AsyncMock(
            return_value=[
                {"id": "place-test", "name": "Test Stop"},
                {"id": "place-two", "name": "Second Stop"},
            ]
        ),
        "async_get_predictions": AsyncMock(return_value={}),
        "async_get_alerts": AsyncMock(return_value={}),
    }
    defaults.update(overrides)
    return patch.multiple("custom_components.mbta.api.MbtaApiClient", **defaults)


async def test_full_user_flow(hass: HomeAssistant) -> None:
    with _patch_api():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        # No API key entered.
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "route_type"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"route_type": "1"}
        )
        assert result["step_id"] == "route"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"route_id": "Red"}
        )
        assert result["step_id"] == "stops"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"stops": ["place-test"]}
        )
        # finish-or-more menu
        assert result["type"] is FlowResultType.MENU

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "finish"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "MBTA (1 stops)"
    assert result["data"]["stops"] == [
        {"stop_id": "place-test", "stop_name": "Test Stop"}
    ]


async def test_invalid_auth(hass: HomeAssistant) -> None:
    from custom_components.mbta.api import MbtaAuthError

    with _patch_api(async_validate_key=AsyncMock(side_effect=MbtaAuthError)):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"api_key": "bad-key"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_removes_a_stop(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="place-test_place-two",
        data={
            "api_key": None,
            "stops": [
                {"stop_id": "place-test", "stop_name": "Test Stop"},
                {"stop_id": "place-two", "stop_name": "Second Stop"},
            ],
        },
    )
    entry.add_to_hass(hass)

    with _patch_api():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "reconfigure_menu"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "edit_stops"}
        )
        assert result["step_id"] == "edit_stops"

        # Keep only the first stop.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"stops": ["place-test"]}
        )
        assert result["type"] is FlowResultType.MENU

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "finish"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["stops"] == [{"stop_id": "place-test", "stop_name": "Test Stop"}]
