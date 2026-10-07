"""Numbers for GoodWe SEMS+ battery charging."""

from __future__ import annotations

from collections.abc import Iterator

from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .control import BatteryControlEntity, InverterControlEntity, TouSlotEntity
from .coordinator import (
    CHARGE_POWER,
    END_CHARGE_SOC,
    EXPORT_LIMIT_POWER,
    BatteryControls,
    SemsPlusStationCoordinator,
)
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
    for sn, controls in data.controls.items():
        if sn in data.devices and EXPORT_LIMIT_POWER in controls:
            yield ExportLimitNumber(coordinator, sn, EXPORT_LIMIT_POWER)
    for sn, settings in data.settings.items():
        if sn not in data.devices or not coordinator.controls_enabled:
            continue
        for slot in settings.tou_slots.values():
            yield TouSlotPower(coordinator, sn, slot, "power")
            yield TouSlotCutoffSoc(coordinator, sn, slot, "cutoff_soc")
    for controls in data.battery_systems.values():
        for function_key, key in (
            (END_CHARGE_SOC, "end_charge_soc"),
            (CHARGE_POWER, "immediate_charge_power"),
        ):
            if function_key in controls.functions:
                yield BatteryNumber(coordinator, controls, key, function_key)


class BatteryNumber(BatteryControlEntity, NumberEntity):
    """A percentage setting for immediate charging."""

    _domain = NUMBER_DOMAIN

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: SemsPlusStationCoordinator,
        controls: BatteryControls,
        key: str,
        function_key: str,
    ) -> None:
        super().__init__(coordinator, controls, key)
        self._function_key = function_key

    @property
    def native_value(self) -> float | None:
        return self._value(self._function_key)

    async def async_set_native_value(self, value: float) -> None:
        await self._async_write(
            self._function_key, int(value), {self._function_key: int(value)}
        )


class ExportLimitNumber(InverterControlEntity, NumberEntity):
    """The power the inverter may export while export limiting is on."""

    _domain = NUMBER_DOMAIN
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    @property
    def native_min_value(self) -> float:
        bounds = self._function.bounds if self._function else None
        return bounds[0] if bounds else 0

    @property
    def native_max_value(self) -> float:
        bounds = self._function.bounds if self._function else None
        return bounds[1] if bounds else 0

    @property
    def native_value(self) -> float | None:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        await self._async_write(int(value), int(value))


class TouSlotPower(TouSlotEntity, NumberEntity):
    """A TOU slot's power, in % of rated power.

    Charging, the power drawn from the grid; discharging, the battery
    discharge or export limit, per the slot's limit method.
    """

    _domain = NUMBER_DOMAIN
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX

    @property
    def native_value(self) -> float | None:
        return self._slot.power_percent if self._slot else None

    async def async_set_native_value(self, value: float) -> None:
        await self._async_change_slot(lambda slot: slot.with_power(value))


class TouSlotCutoffSoc(TouSlotEntity, NumberEntity):
    """The battery level at which a TOU slot stops charging or discharging."""

    _domain = NUMBER_DOMAIN
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX

    @property
    def native_value(self) -> float | None:
        return self._slot.cutoff_soc if self._slot else None

    async def async_set_native_value(self, value: float) -> None:
        await self._async_write_slot(cutoff_soc=int(value))
