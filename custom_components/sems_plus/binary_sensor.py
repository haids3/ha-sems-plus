"""Binary sensors for GoodWe SEMS+ stations."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .coordinator import SemsPlusStationCoordinator, StationData
from .entity import SemsPlusEntity, async_add_station_entities, station_device_info

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
    for description in STATION_BINARY_SENSORS:
        if description.exists_fn(coordinator.data):
            yield StationBinarySensor(coordinator, description)


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
