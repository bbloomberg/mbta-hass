"""Shared base entity for the MBTA integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import MbtaCoordinator


class MbtaStopEntity(CoordinatorEntity[MbtaCoordinator]):
    """Base entity tied to a single configured stop."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        coordinator: MbtaCoordinator,
        stop_id: str,
        stop_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._stop_id = stop_id
        self._stop_name = stop_name
        device_id = f"{coordinator.entry.entry_id}_{stop_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=stop_name,
            manufacturer="MBTA",
            model="Stop",
            configuration_url="https://www.mbta.com",
        )
