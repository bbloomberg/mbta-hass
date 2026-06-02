"""Next-departure sensors for the MBTA integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MbtaConfigEntry
from .const import (
    CONF_MAX_DEPARTURES,
    DEFAULT_MAX_DEPARTURES,
)
from .coordinator import MbtaCoordinator
from .entity import MbtaStopEntity

# Hard cap on how many departures we put into the state attribute, to bound the
# attribute size regardless of how many destinations a stop serves.
ATTRIBUTE_DEPARTURE_CAP = 30

# Pick an icon based on the type of the next departure's route.
_ROUTE_TYPE_ICONS = {
    0: "mdi:tram",
    1: "mdi:subway-variant",
    2: "mdi:train",
    3: "mdi:bus",
    4: "mdi:ferry",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MbtaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MBTA next-departure sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        MbtaNextDepartureSensor(coordinator, stop["stop_id"], stop["stop_name"])
        for stop in coordinator.stops
    )


class MbtaNextDepartureSensor(MbtaStopEntity, SensorEntity):
    """Minutes until the next departure at a stop."""

    _attr_translation_key = "next_departure"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:bus-clock"

    def __init__(
        self,
        coordinator: MbtaCoordinator,
        stop_id: str,
        stop_name: str,
    ) -> None:
        super().__init__(coordinator, stop_id, stop_name)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{stop_id}_next_departure"
        self._max_departures = coordinator.entry.options.get(
            CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES
        )

    @property
    def _departures(self):
        return self.coordinator.data.predictions.get(self._stop_id, [])

    @property
    def native_value(self) -> int | None:
        """Minutes to the next non-cancelled departure."""
        for dep in self._departures:
            if dep.is_cancelled:
                continue
            return dep.minutes
        return None

    @property
    def icon(self) -> str:
        for dep in self._departures:
            return _ROUTE_TYPE_ICONS.get(dep.route_type, "mdi:bus-clock")
        return "mdi:bus-clock"

    def _limited_departures(self, departures):
        """Up to ``max_departures`` per destination, preserving time order.

        Capping per destination (rather than taking a flat slice of the next N)
        guarantees every destination is represented even when one direction runs
        far more often than another — e.g. a bus stop's two directions. This is
        what the card's ``per_destination`` grouping needs to show the next few
        of *each* destination; a flat slice would otherwise be dominated by the
        more frequent direction.
        """
        per = self._max_departures
        counts: dict = {}
        out = []
        for dep in departures:
            key = dep.headsign or dep.route_name
            if counts.get(key, 0) >= per:
                continue
            counts[key] = counts.get(key, 0) + 1
            out.append(dep)
            if len(out) >= ATTRIBUTE_DEPARTURE_CAP:
                break
        return out

    @property
    def extra_state_attributes(self) -> dict:
        departures = self._departures
        upcoming = self._limited_departures(departures)
        next_dep = next((d for d in departures if not d.is_cancelled), None)
        return {
            "stop_id": self._stop_id,
            "stop_name": self._stop_name,
            "next_route": next_dep.route_name if next_dep else None,
            "next_headsign": next_dep.headsign if next_dep else None,
            "next_direction": next_dep.direction_name if next_dep else None,
            "next_time": next_dep.time.isoformat()
            if next_dep and next_dep.time
            else None,
            "next_status": next_dep.status if next_dep else None,
            "departures": upcoming,
        }
