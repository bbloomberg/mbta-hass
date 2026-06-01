"""Smoke tests for the bundled Lovelace arrival-board card."""

from __future__ import annotations

import pathlib

CARD = (
    pathlib.Path(__file__).parents[1]
    / "custom_components"
    / "mbta"
    / "www"
    / "mbta-arrival-board-card.js"
)


def test_card_file_is_bundled() -> None:
    assert CARD.is_file(), "arrival-board card JS must ship with the integration"


def test_card_defines_custom_element() -> None:
    source = CARD.read_text()
    assert 'customElements.define("mbta-arrival-board-card"' in source
    # Registered with the card picker so it appears in the UI.
    assert "window.customCards" in source


def test_card_ships_visual_editor() -> None:
    source = CARD.read_text()
    assert 'customElements.define("mbta-arrival-board-card-editor"' in source
    assert "getConfigElement" in source


def test_card_supports_grouping_and_filters() -> None:
    source = CARD.read_text()
    # Per-destination grouping and route/destination filtering.
    assert "per_destination" in source
    assert "_computeDepartures" in source
    assert "cfg.routes" in source and "cfg.destinations" in source


def test_card_renders_alert_independently() -> None:
    source = CARD.read_text()
    # The alert banner lives in its own node and is rebuilt only on text change,
    # so departure refreshes don't restart the marquee.
    assert "mbta-alert" in source
    assert "_alertText" in source
