"""Binary sensors for GoodWe SEMS+ stations."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .coordinator import SemsPlusStationCoordinator, StationData
from .entity import (
    SemsPlusEntity,
    async_add_station_entities,
    device_info,
    station_device_info,
)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class StationBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[StationData], bool | None]
    exists_fn: Callable[[StationData], bool] = lambda data: True


STATION_BINARY_SENSORS = [
    StationBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        # Station status 0 is offline; every other status means it reports in.
        value_fn=lambda data: (
            data.info.status != 0
            if data.info and data.info.status is not None
            else None
        ),
    ),
    StationBinarySensorDescription(
        key="alarm",
        translation_key="alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: (
            data.alarm_counts.active > 0 if data.alarm_counts else None
        ),
    ),
    StationBinarySensorDescription(
        key="on_grid",
        translation_key="on_grid",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda data: data.info.on_grid if data.info else None,
        # PV-only stations report no grid connection at all.
        exists_fn=lambda data: data.info is not None and data.info.on_grid is not None,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SemsPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_station_entities(entry, async_add_entities, _build)


def _build(coordinator: SemsPlusStationCoordinator) -> Iterator[SemsPlusEntity]:
    data = coordinator.data
    for description in STATION_BINARY_SENSORS:
        if description.exists_fn(data):
            yield StationBinarySensor(coordinator, description)
    for sn in data.firmware_updates:
        if sn in data.devices:
            yield FirmwareUpdateBinarySensor(coordinator, sn)
    for sn in data.export_limits:
        if sn in data.devices:
            yield ExportLimitBinarySensor(coordinator, sn)


class StationBinarySensor(SemsPlusEntity, BinarySensorEntity):
    entity_description: StationBinarySensorDescription

    def __init__(
        self,
        coordinator: SemsPlusStationCoordinator,
        description: StationBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.station_id}-{description.key}"
        self._set_entity_id(BINARY_SENSOR_DOMAIN, description.key)
        self._attr_device_info = station_device_info(coordinator)

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)


class FirmwareUpdateBinarySensor(SemsPlusEntity, BinarySensorEntity):
    """On when SEMS+ has firmware waiting for the device.

    SEMS+ names the new versions but not the installed ones, so this is a
    binary sensor rather than an update entity; the attributes list them.
    """

    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "firmware_update"

    def __init__(self, coordinator: SemsPlusStationCoordinator, sn: str) -> None:
        super().__init__(coordinator)
        self._sn = sn
        device = coordinator.data.devices[sn]
        self._attr_unique_id = f"{sn}-firmware_update"
        self._attr_device_info = device_info(coordinator, device)
        self._set_entity_id(BINARY_SENSOR_DOMAIN, device.name, "firmware_update")

    @property
    def available(self) -> bool:
        return super().available and self._sn in self.coordinator.data.firmware_updates

    @property
    def is_on(self) -> bool | None:
        updates = self.coordinator.data.firmware_updates.get(self._sn)
        return None if updates is None else bool(updates)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        updates = data.firmware_updates.get(self._sn)
        if updates is None:
            return None
        force = data.force_upgrades.get(self._sn)
        return {
            # Whether this account may install them in SEMS+ at all; the web
            # hides its upgrade button otherwise.
            "can_apply": data.can_apply_firmware(self._sn),
            "forced": force.forced if force else None,
            "upgrading": force.upgrading if force else None,
            "updates": [
                {
                    "component": update.component,
                    "version": update.version,
                    "name": update.name,
                    "released": update.released.isoformat()
                    if update.released
                    else None,
                }
                for update in updates
            ],
        }


class ExportLimitBinarySensor(SemsPlusEntity, BinarySensorEntity):
    """Whether the inverter limits export; readable without controls."""

    _attr_translation_key = "export_limit"

    def __init__(self, coordinator: SemsPlusStationCoordinator, sn: str) -> None:
        super().__init__(coordinator)
        self._sn = sn
        device = coordinator.data.devices[sn]
        self._attr_unique_id = f"{sn}-export_limit"
        self._attr_device_info = device_info(coordinator, device)
        self._set_entity_id(BINARY_SENSOR_DOMAIN, device.name, "export_limit")

    @property
    def available(self) -> bool:
        return super().available and self._sn in self.coordinator.data.export_limits

    @property
    def is_on(self) -> bool | None:
        limit = self.coordinator.data.export_limits.get(self._sn)
        return limit.enabled if limit else None
