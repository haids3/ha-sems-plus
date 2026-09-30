"""Switches for GoodWe SEMS+ inverters and batteries."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .control import BatteryControlEntity, async_write
from .coordinator import (
    IMMEDIATE_CHARGE,
    RUN_STOP,
    STOP_CHARGING,
    SemsPlusStationCoordinator,
)
from .entity import SemsPlusEntity, async_add_station_entities, device_info

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SemsPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_station_entities(entry, async_add_entities, _build)


def _build(coordinator: SemsPlusStationCoordinator) -> Iterator[SemsPlusEntity]:
    data = coordinator.data
    for sn in data.run_stop:
        if sn in data.devices:
            yield RunStopSwitch(coordinator, sn)
    for controls in data.battery_systems.values():
        if {IMMEDIATE_CHARGE, STOP_CHARGING} <= controls.functions.keys():
            yield ImmediateChargingSwitch(coordinator, controls, "immediate_charging")


class RunStopSwitch(SemsPlusEntity, SwitchEntity):
    """Starts or stops an inverter through its own run/stop function."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "run"

    def __init__(self, coordinator: SemsPlusStationCoordinator, sn: str) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"{sn}-{RUN_STOP}"
        self._attr_device_info = device_info(coordinator, coordinator.data.devices[sn])

    @property
    def available(self) -> bool:
        return super().available and self._sn in self.coordinator.data.run_stop

    @property
    def is_on(self) -> bool | None:
        function = self.coordinator.data.run_stop[self._sn]
        value = self.coordinator.data.control_values.get(self._sn, {}).get(
            function.address
        )
        return None if value is None else value == 1

    async def _async_set(self, running: bool) -> None:
        data = self.coordinator.data
        await async_write(
            self.coordinator,
            self._sn,
            data.devices[self._sn].name,
            data.run_stop[self._sn],
            1 if running else 0,
            {RUN_STOP: "remote_Switch_on" if running else "remote_Switch_off"},
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class ImmediateChargingSwitch(BatteryControlEntity, SwitchEntity):
    """Charges the battery now, up to the end SOC at the charge power set."""

    @property
    def is_on(self) -> bool | None:
        value = self._value(IMMEDIATE_CHARGE)
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(IMMEDIATE_CHARGE, 1, {IMMEDIATE_CHARGE: "on"})

    async def async_turn_off(self, **kwargs: Any) -> None:
        # Stopping is a separate function on the same address.
        await self._async_write(STOP_CHARGING, 0, {STOP_CHARGING: "remote_Switch_off"})
