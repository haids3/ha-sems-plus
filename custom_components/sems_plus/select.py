"""Selects for GoodWe SEMS+ work modes and TOU slots."""

from __future__ import annotations

from collections.abc import Iterator

from homeassistant.components.select import DOMAIN as SELECT_DOMAIN, SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .control import TouSlotEntity, WorkModeEntity
from .coordinator import V1_MODES, SemsPlusStationCoordinator
from .entity import SemsPlusEntity, async_add_station_entities, shows_pending

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
        if settings.v1 and any(
            settings.mode_visible(func_key) for func_key in V1_MODES
        ):
            yield WorkModeSelect(coordinator, sn, "configured_mode")
        # Firmware without this capability always limits battery discharge;
        # version 1 never offers it.
        limit_method = (
            not settings.v1
            and settings.features is not None
            and settings.features.tou_power_limit_mode
        )
        for slot in settings.tou_slots.values():
            yield TouSlotModeSelect(coordinator, sn, slot, "mode")
            if limit_method:
                yield TouSlotLimitSelect(coordinator, sn, slot, "discharge_limit")


class WorkModeSelect(WorkModeEntity, SelectEntity):
    """The one work mode an inverter on work-mode version 1 runs."""

    _domain = SELECT_DOMAIN

    @property
    def options(self) -> list[str]:
        settings = self._settings
        return [
            mode.option
            for func_key, mode in V1_MODES.items()
            if settings is None or settings.mode_visible(func_key)
        ]

    @property
    @shows_pending
    def current_option(self) -> str | None:
        settings = self._settings
        if settings is None or settings.v1_mode is None:
            return None
        return V1_MODES[settings.v1_mode].option

    async def async_select_option(self, option: str) -> None:
        async with self._async_pending(option):
            mode = next(mode for mode in V1_MODES.values() if mode.option == option)
            await self._async_write_setting(
                mode.setting,
                {mode.setting: mode.code},
                {mode.log_key: "remote_Switch_on"},
            )


class TouSlotModeSelect(TouSlotEntity, SelectEntity):
    """Whether a TOU slot charges or discharges the battery."""

    _domain = SELECT_DOMAIN
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [CHARGE, DISCHARGE]

    @property
    @shows_pending
    def current_option(self) -> str | None:
        if (slot := self._slot) is None:
            return None
        return CHARGE if slot.charging else DISCHARGE

    async def async_select_option(self, option: str) -> None:
        async with self._async_pending(option):
            await self._async_change_slot(
                lambda slot: slot.with_mode(charging=option == CHARGE)
            )


class TouSlotLimitSelect(TouSlotEntity, SelectEntity):
    """What a discharge slot's power limits: battery discharge or export."""

    _domain = SELECT_DOMAIN
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [BATTERY, EXPORT]

    @property
    @shows_pending
    def current_option(self) -> str | None:
        if (slot := self._slot) is None:
            return None
        return EXPORT if slot.export_limited else BATTERY

    async def async_select_option(self, option: str) -> None:
        async with self._async_pending(option):
            await self._async_change_slot(
                lambda slot: slot.with_export_limit(option == EXPORT)
            )
