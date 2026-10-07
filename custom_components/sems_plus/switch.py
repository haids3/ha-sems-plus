"""Switches for GoodWe SEMS+ inverters and batteries."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .control import BatteryControlEntity, InverterControlEntity
from .coordinator import (
    EXPORT_LIMIT,
    IMMEDIATE_CHARGE,
    RUN_STOP,
    STOP_CHARGING,
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
