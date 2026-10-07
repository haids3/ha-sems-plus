"""Selects for GoodWe SEMS+ TOU slots."""

from __future__ import annotations

from collections.abc import Iterator

from homeassistant.components.select import DOMAIN as SELECT_DOMAIN, SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .control import TouSlotEntity
from .coordinator import SemsPlusStationCoordinator
from .entity import SemsPlusEntity, async_add_station_entities

PARALLEL_UPDATES = 1

CHARGE = "charge"
DISCHARGE = "discharge"
BATTERY = "battery"
EXPORT = "export"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SemsPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_station_entities(entry, async_add_entities, _build)


def _build(coordinator: SemsPlusStationCoordinator) -> Iterator[SemsPlusEntity]:
    data = coordinator.data
    for sn, settings in data.settings.items():
        if sn not in data.devices or not coordinator.controls_enabled:
            continue
        # Firmware without this capability always limits battery discharge.
        limit_method = settings.features is not None and (
            settings.features.tou_power_limit_mode
        )
        for slot in settings.tou_slots.values():
            yield TouSlotModeSelect(coordinator, sn, slot, "mode")
            if limit_method:
                yield TouSlotLimitSelect(coordinator, sn, slot, "discharge_limit")


class TouSlotModeSelect(TouSlotEntity, SelectEntity):
    """Whether a TOU slot charges or discharges the battery."""

    _domain = SELECT_DOMAIN
    _attr_options = [CHARGE, DISCHARGE]

    @property
    def current_option(self) -> str | None:
        if (slot := self._slot) is None:
            return None
        return CHARGE if slot.charging else DISCHARGE

    async def async_select_option(self, option: str) -> None:
        await self._async_change_slot(
            lambda slot: slot.with_mode(charging=option == CHARGE)
        )


class TouSlotLimitSelect(TouSlotEntity, SelectEntity):
    """What a discharge slot's power limits: battery discharge or export."""

    _domain = SELECT_DOMAIN
    _attr_options = [BATTERY, EXPORT]

    @property
    def current_option(self) -> str | None:
        if (slot := self._slot) is None:
            return None
        return EXPORT if slot.export_limited else BATTERY

    async def async_select_option(self, option: str) -> None:
        await self._async_change_slot(
            lambda slot: slot.with_export_limit(option == EXPORT)
        )
