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
