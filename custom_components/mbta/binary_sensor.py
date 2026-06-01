"""Delay / alert binary sensors for the MBTA integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MbtaConfigEntry
from .const import DISRUPTION_EFFECTS
from .coordinator import MbtaCoordinator
from .entity import MbtaStopEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MbtaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MBTA alert/delay binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        MbtaAlertBinarySensor(coordinator, stop["stop_id"], stop["stop_name"])
        for stop in coordinator.stops
    )


class MbtaAlertBinarySensor(MbtaStopEntity, BinarySensorEntity):
    """On when there is an active service alert or delay affecting the stop."""

    _attr_translation_key = "alert"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert"

    def __init__(
        self,
        coordinator: MbtaCoordinator,
        stop_id: str,
        stop_name: str,
    ) -> None:
        super().__init__(coordinator, stop_id, stop_name)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{stop_id}_alert"

    @property
    def _active_alerts(self):
        alerts = self.coordinator.data.alerts.get(self._stop_id, [])
        return [a for a in alerts if a.is_active()]

    @property
    def is_on(self) -> bool:
        return bool(self._active_alerts)

    @property
    def extra_state_attributes(self) -> dict:
        alerts = self._active_alerts
        delays = [a for a in alerts if a.effect == "DELAY"]
        disruptions = [a for a in alerts if a.effect in DISRUPTION_EFFECTS]
        return {
            "stop_id": self._stop_id,
            "stop_name": self._stop_name,
            "alert_count": len(alerts),
            "has_delay": bool(delays),
            "effects": sorted({a.effect for a in alerts}),
            "headers": [a.short_header or a.header for a in disruptions if a.header],
            "alerts": [a.as_dict() for a in alerts],
        }
