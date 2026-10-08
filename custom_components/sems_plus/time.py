"""Times for GoodWe SEMS+ TOU slots."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import time

from homeassistant.components.time import DOMAIN as TIME_DOMAIN, TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .control import TouSlotEntity, WorkModeEntity
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
        # Settings are also read without controls, to show them read-only.
        if sn not in data.devices or not coordinator.controls_enabled:
            continue
        for slot in settings.tou_slots.values():
            yield TouSlotTime(coordinator, sn, slot, "start")
            yield TouSlotTime(coordinator, sn, slot, "end")
        if settings.mode_visible("peakShaveMode") and settings.peak_slot is not None:
            yield WorkModeTime(coordinator, sn, "peak_shaving_start")
            yield WorkModeTime(coordinator, sn, "peak_shaving_end")
        if settings.mode_visible("delayMode") and settings.delay_slot is not None:
            yield WorkModeTime(coordinator, sn, "delayed_charge_time")


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


# Entity key: (peak shaving or delayed charge, the slot field it sets).
_WORK_MODE_TIMES = {
    "peak_shaving_start": (True, "start"),
    "peak_shaving_end": (True, "end"),
    # The web labels delayed charge's only time "start" but stores it as end.
    "delayed_charge_time": (False, "end"),
}


class WorkModeTime(WorkModeEntity, TimeEntity):
    """A peak-shaving window edge, or when delayed charge starts charging."""

    _domain = TIME_DOMAIN
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def native_value(self) -> time | None:
        peak, field = _WORK_MODE_TIMES[self._attr_translation_key]
        settings = self._settings
        slot = None
        if settings is not None:
            slot = settings.peak_slot if peak else settings.delay_slot
        if slot is None:
            return None
        try:
            return time.fromisoformat(getattr(slot, field))
        except ValueError:
            return None

    async def async_set_value(self, value: time) -> None:
        peak, field = _WORK_MODE_TIMES[self._attr_translation_key]
        change = {field: value.strftime("%H:%M")}
        if peak:
            await self._async_write_peak_shaving(**change)
        else:
            await self._async_write_delayed_charge(**change)
