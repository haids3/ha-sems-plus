"""Times for GoodWe SEMS+ TOU slots."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import time

from homeassistant.components.time import DOMAIN as TIME_DOMAIN, TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .control import TouSlotEntity
from .coordinator import SemsPlusStationCoordinator
from .entity import SemsPlusEntity, async_add_station_entities

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SemsPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_station_entities(entry, async_add_entities, _build)


def _build(coordinator: SemsPlusStationCoordinator) -> Iterator[SemsPlusEntity]:
    data = coordinator.data
    for sn, settings in data.settings.items():
        if sn not in data.devices:
            continue
        for slot in settings.tou_slots.values():
            yield TouSlotTime(coordinator, sn, slot, "start")
            yield TouSlotTime(coordinator, sn, slot, "end")


class TouSlotTime(TouSlotEntity, TimeEntity):
    """When a TOU slot starts or ends."""

    _domain = TIME_DOMAIN

    @property
    def native_value(self) -> time | None:
        if (slot := self._slot) is None:
            return None
        try:
            return time.fromisoformat(
                slot.start if self._attr_translation_key.endswith("start") else slot.end
            )
        except ValueError:
            return None

    async def async_set_value(self, value: time) -> None:
        field = "start" if self._attr_translation_key.endswith("start") else "end"
        await self._async_write_slot(**{field: value.strftime("%H:%M")})
