"""Switches for GoodWe SEMS+ inverters and batteries."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .control import (
    MODE_CONFLICTS,
    BatteryControlEntity,
    InverterControlEntity,
    TouSlotEntity,
    WorkModeEntity,
)
from .coordinator import (
    BACKUP_GRID_CHARGE,
    BACKUP_MODE,
    DELAYED_CHARGE_ENABLE,
    EXPORT_LIMIT,
    IMMEDIATE_CHARGE,
    OFF_GRID_MODE,
    RUN_STOP,
    STOP_CHARGING,
    TOU_MODE,
    SemsPlusStationCoordinator,
)
from .entity import (
    SemsPlusEntity,
    async_add_station_entities,
    shows_pending,
    work_mode_device_info,
)

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
        if BACKUP_GRID_CHARGE in controls:
            yield BackupGridChargeSwitch(coordinator, sn, BACKUP_GRID_CHARGE)
    for sn, settings in data.settings.items():
        # Settings are also read without controls, to show them read-only.
        if sn not in data.devices or not coordinator.controls_enabled:
            continue
        # Version 1 runs one mode at a time, chosen with a select instead.
        for key, (func_key, *_rest) in _WORK_MODES.items():
            if (
                not settings.v1
                and settings.mode_visible(func_key)
                and getattr(settings, key) is not None
            ):
                yield WorkModeSwitch(coordinator, sn, key)
        if settings.mode_visible("delayMode") and settings.delay_slot is not None:
            yield DelayedChargePvFirstSwitch(coordinator, sn, "delayed_charge_pv_first")
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
    @shows_pending
    def is_on(self) -> bool | None:
        if (value := self._value) is None or (function := self._function) is None:
            return None
        on = function.option_value("remote_Switch_on")
        return value == (1 if on is None else on)

    async def _async_set(self, on: bool) -> None:
        async with self._async_pending(on):
            function = self._function
            trans_key = "remote_Switch_on" if on else "remote_Switch_off"
            value = function.option_value(trans_key) if function else None
            await self._async_write(value if value is not None else int(on), trans_key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


# Entity key: (the web's funcKey, setting written, field, web log key). Peak
# shaving and delayed charge write their DemandOrDelayed setting instead.
_WORK_MODES = {
    "tou_mode": ("TOUMode", TOU_MODE, "TOUModeEnable", "TOU"),
    "backup_mode": ("backupMode", BACKUP_MODE, "BackupModeEnable", "backup_mode"),
    "off_grid_mode": ("offGridMode", OFF_GRID_MODE, "OffGridEnable", "off_grid_mode"),
    "peak_shaving": ("peakShaveMode", None, None, "peak_shave"),
    "delayed_charge": ("delayMode", None, None, "delayed_charge"),
}


class WorkModeSwitch(WorkModeEntity, SwitchEntity):
    """A work mode the inverter may use alongside self-use."""

    _domain = SWITCH_DOMAIN

    @property
    @shows_pending
    def is_on(self) -> bool | None:
        if (settings := self._settings) is None:
            return None
        return getattr(settings, self._attr_translation_key)

    async def _async_set(self, on: bool) -> None:
        key = self._attr_translation_key
        settings = self._settings
        if on and settings is not None:
            for other in MODE_CONFLICTS.get(key, ()):
                if getattr(settings, other):
                    raise HomeAssistantError(
                        f"Turn off {other.replace('_', ' ')} first; "
                        f"it cannot run with {key.replace('_', ' ')}"
                    )
        async with self._async_pending(on):
            _func_key, name, field, log_key = _WORK_MODES[key]
            log = {log_key: "remote_Switch_on" if on else "remote_Switch_off"}
            if key in ("peak_shaving", "delayed_charge"):
                slot = self._demand_slot(peak_shaving=key == "peak_shaving")
                await self._async_write_setting(
                    slot.name,
                    slot.toggle_data(on, peak_shaving=key == "peak_shaving"),
                    log,
                )
                if key == "delayed_charge":
                    await self._async_write_setting(
                        DELAYED_CHARGE_ENABLE, {DELAYED_CHARGE_ENABLE: int(on)}, log
                    )
                return
            assert name is not None and field is not None
            value = {field: int(on)}
            if (
                key == "off_grid_mode"
                and not on
                and settings is not None
                and settings.features is not None
                and settings.features.auto_off_grid
            ):
                # Firmware that can leave the grid on its own needs that off too.
                value["AutoOffGridModeEnable"] = 0
            await self._async_write_setting(name, value, log)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class DelayedChargePvFirstSwitch(WorkModeEntity, SwitchEntity):
    """Whether delayed charge lets PV charge the battery first."""

    _domain = SWITCH_DOMAIN
    _attr_entity_category = EntityCategory.CONFIG

    @property
    @shows_pending
    def is_on(self) -> bool | None:
        settings = self._settings
        slot = settings.delay_slot if settings else None
        return None if slot is None else slot.charge_priority == 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        async with self._async_pending(True):
            await self._async_write_delayed_charge(charge_priority=0)

    async def async_turn_off(self, **kwargs: Any) -> None:
        async with self._async_pending(False):
            await self._async_write_delayed_charge(charge_priority=1)


class BackupGridChargeSwitch(InverterSwitch):
    """Charging the battery from the grid while backup mode is on.

    SEMS+ only caches this register once it has been written, so until then
    the state is unknown and Home Assistant offers both on and off.
    """

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def assumed_state(self) -> bool:
        return self.is_on is None

    def __init__(
        self, coordinator: SemsPlusStationCoordinator, sn: str, control: str
    ) -> None:
        super().__init__(coordinator, sn, control)
        self._attr_device_info = work_mode_device_info(coordinator, sn)


class TouSlotSwitch(TouSlotEntity, SwitchEntity):
    """Whether a TOU slot is active."""

    _domain = SWITCH_DOMAIN

    @property
    @shows_pending
    def is_on(self) -> bool | None:
        return self._slot.enabled if self._slot else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        async with self._async_pending(True):
            # A slot that never had a schedule would otherwise never apply.
            # Version 1 slots have no months.
            await self._async_change_slot(
                lambda slot: replace(
                    slot.with_enabled(True),
                    weekdays=slot.weekdays or tuple(range(7)),
                    months=slot.months or (() if slot.v1 else tuple(range(12))),
                )
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        async with self._async_pending(False):
            await self._async_change_slot(lambda slot: slot.with_enabled(False))


class ImmediateChargingSwitch(BatteryControlEntity, SwitchEntity):
    """Charges the battery now, up to the end SOC at the charge power set."""

    _domain = SWITCH_DOMAIN

    @property
    @shows_pending
    def is_on(self) -> bool | None:
        value = self._value(IMMEDIATE_CHARGE)
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        async with self._async_pending(True):
            await self._async_write(IMMEDIATE_CHARGE, 1, {IMMEDIATE_CHARGE: "on"})

    async def async_turn_off(self, **kwargs: Any) -> None:
        async with self._async_pending(False):
            # Stopping is a separate function on the same address.
            await self._async_write(
                STOP_CHARGING, 0, {STOP_CHARGING: "remote_Switch_off"}
            )
