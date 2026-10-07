"""Switches for GoodWe SEMS+ inverters and batteries."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sems_plus_client import TOU_SLOT_OFF, TOU_SLOT_ON

from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .control import (
    BatteryControlEntity,
    InverterControlEntity,
    InverterSettingEntity,
    TouSlotEntity,
)
from .coordinator import (
    BACKUP_MODE,
    EXPORT_LIMIT,
    IMMEDIATE_CHARGE,
    RUN_STOP,
    STOP_CHARGING,
    TOU_MODE,
    SemsPlusStationCoordinator,
)
from .entity import SemsPlusEntity, async_add_station_entities

PARALLEL_UPDATES = 1

# Control name, and the entity key it is named and identified by.
_INVERTER_SWITCHES = {RUN_STOP: "run", EXPORT_LIMIT: "export_limit"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SemsPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_station_entities(entry, async_add_entities, _build)


def _build(coordinator: SemsPlusStationCoordinator) -> Iterator[SemsPlusEntity]:
    data = coordinator.data
    for sn, controls in data.controls.items():
        if sn not in data.devices:
            continue
        for control, key in _INVERTER_SWITCHES.items():
            if control in controls:
                yield InverterSwitch(coordinator, sn, control, key)
    for sn, settings in data.settings.items():
        if sn not in data.devices:
            continue
        if settings.tou_mode is not None:
            yield WorkModeSwitch(coordinator, sn, "tou_mode")
        if settings.backup_mode is not None:
            yield WorkModeSwitch(coordinator, sn, "backup_mode")
        for slot in settings.tou_slots.values():
            yield TouSlotSwitch(coordinator, sn, slot, None)
    for controls in data.battery_systems.values():
        if {IMMEDIATE_CHARGE, STOP_CHARGING} <= controls.functions.keys():
            yield ImmediateChargingSwitch(coordinator, controls, "immediate_charging")


class InverterSwitch(InverterControlEntity, SwitchEntity):
    """An inverter on/off setting: run/stop, export limiting."""

    _domain = SWITCH_DOMAIN
    _attr_device_class = SwitchDeviceClass.SWITCH

    @property
    def is_on(self) -> bool | None:
        if (value := self._value) is None or (function := self._function) is None:
            return None
        on = function.option_value("remote_Switch_on")
        return value == (1 if on is None else on)

    async def _async_set(self, on: bool) -> None:
        function = self._function
        trans_key = "remote_Switch_on" if on else "remote_Switch_off"
        value = function.option_value(trans_key) if function else None
        await self._async_write(value if value is not None else int(on), trans_key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


# Entity key: (setting name, field written, web log key).
_WORK_MODES = {
    "tou_mode": (TOU_MODE, "TOUModeEnable", "TOU"),
    "backup_mode": (BACKUP_MODE, "BackupModeEnable", "backup_mode"),
}


class WorkModeSwitch(InverterSettingEntity, SwitchEntity):
    """A work mode the inverter may use alongside self-use."""

    _domain = SWITCH_DOMAIN

    @property
    def is_on(self) -> bool | None:
        if (settings := self._settings) is None:
            return None
        return getattr(settings, self._attr_translation_key)

    async def _async_set(self, on: bool) -> None:
        name, field, log_key = _WORK_MODES[self._attr_translation_key]
        await self._async_write_setting(
            name,
            {field: int(on)},
            {log_key: "remote_Switch_on" if on else "remote_Switch_off"},
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class TouSlotSwitch(TouSlotEntity, SwitchEntity):
    """Whether a TOU slot is active."""

    _domain = SWITCH_DOMAIN

    @property
    def is_on(self) -> bool | None:
        return self._slot.enabled if self._slot else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        slot = self._slot
        # A slot that never had a schedule would otherwise never apply.
        await self._async_write_slot(
            week_enable=TOU_SLOT_ON,
            weekdays=(slot and slot.weekdays) or tuple(range(7)),
            months=(slot and slot.months) or tuple(range(12)),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write_slot(week_enable=TOU_SLOT_OFF)


class ImmediateChargingSwitch(BatteryControlEntity, SwitchEntity):
    """Charges the battery now, up to the end SOC at the charge power set."""

    _domain = SWITCH_DOMAIN

    @property
    def is_on(self) -> bool | None:
        value = self._value(IMMEDIATE_CHARGE)
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(IMMEDIATE_CHARGE, 1, {IMMEDIATE_CHARGE: "on"})

    async def async_turn_off(self, **kwargs: Any) -> None:
        # Stopping is a separate function on the same address.
        await self._async_write(STOP_CHARGING, 0, {STOP_CHARGING: "remote_Switch_off"})
