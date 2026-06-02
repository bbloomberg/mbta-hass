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


def test_card_supports_group_by_direction() -> None:
    source = CARD.read_text()
    # Selectable grouping: by terminus/destination or by direction.
    assert "_groupKey" in source
    assert 'group_by === "direction"' in source


def test_card_orders_groups_deterministically() -> None:
    source = CARD.read_text()
    # Groups are ordered by direction then name (fixed), not by soonest train.
    assert "groups.entries()" in source
    assert "direction_id" in source
    assert "sortKey" not in source  # the old soonest-first ordering is gone


def test_card_renders_alert_independently() -> None:
    source = CARD.read_text()
    # The alert banner lives in its own node and is rebuilt only on text/expand
    # change, so departure refreshes don't restart the marquee.
    assert "mbta-alert" in source
    assert "_alertKey" in source


def test_alert_uses_css_animation_and_expands() -> None:
    source = CARD.read_text()
    # CSS animation (reliable start), not SMIL which sometimes failed to begin.
    assert "@keyframes ascroll-" in source
    assert "animateTransform" not in source
    # Tap-to-expand support.
    assert "_alertExpanded" in source
    assert "_alertExpandedSvg" in source
    assert "_wrapText" in source
