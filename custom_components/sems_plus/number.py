"""Numbers for GoodWe SEMS+ battery charging."""

from __future__ import annotations

from collections.abc import Iterator

from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .control import (
    BatteryControlEntity,
    InverterControlEntity,
    TouSlotEntity,
    WorkModeEntity,
)
from .coordinator import (
    BACKUP_GRID_CHARGE,
    BACKUP_MODE,
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
        if settings.mode_visible("backupMode") and (
            settings.backup_charge_power is not None
        ):
            yield BackupChargePower(coordinator, sn, "backup_charge_power")
        if settings.mode_visible("peakShaveMode") and settings.peak_slot is not None:
            yield PeakShavingSoc(coordinator, sn, "peak_shaving_soc")
            yield PeakShavingImportLimit(coordinator, sn, "peak_shaving_import_limit")
        if settings.mode_visible("delayMode") and settings.delay_slot is not None:
            yield DelayedChargeExportLimit(
                coordinator, sn, "delayed_charge_export_limit"
            )
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


class _WorkModeNumber(WorkModeEntity, NumberEntity):
    _domain = NUMBER_DOMAIN
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX


class BackupChargePower(_WorkModeNumber):
    """How hard backup mode charges from the grid, in % of rated power."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def native_value(self) -> float | None:
        return self._settings.backup_charge_power if self._settings else None

    async def async_set_native_value(self, value: float) -> None:
        grid_charge = self.coordinator.data.controls.get(self._sn, {}).get(
            BACKUP_GRID_CHARGE
        )
        state = (
            self.coordinator.data.control_values.get(self._sn, {}).get(
                grid_charge.address
            )
            if grid_charge
            else None
        )
        # The web only sends a charge power while grid charging is on.
        if state == 0:
            raise HomeAssistantError("Turn on backup grid charging first")
        await self._async_write_setting(
            BACKUP_MODE,
            {"BackupChargeModelEnable": 1, "BackupPChargeP": int(value)},
            {"gird_pur_charge": "remote_Switch_on", "charge_pw": int(value)},
        )


class PeakShavingSoc(_WorkModeNumber):
    """The battery level peak shaving keeps in reserve."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def native_value(self) -> float | None:
        slot = self._settings.peak_slot if self._settings else None
        return slot.soc if slot else None

    async def async_set_native_value(self, value: float) -> None:
        await self._async_write_peak_shaving(soc=int(value))


class PeakShavingImportLimit(_WorkModeNumber):
    """The grid import peak shaving holds the station under."""

    _attr_native_min_value = 0
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = NumberDeviceClass.POWER

    @property
    def native_max_value(self) -> float:
        # The web's limits: just under 655.35 kW on version 3, else 500 kW.
        settings = self._settings
        return 655.34 if settings and settings.version == "3.0" else 500

    @property
    def native_value(self) -> float | None:
        slot = self._settings.peak_slot if self._settings else None
        return slot.power_limit if slot else None

    async def async_set_native_value(self, value: float) -> None:
        await self._async_write_peak_shaving(power_limit=round(value, 2))


class DelayedChargeExportLimit(_WorkModeNumber):
    """The export delayed charge allows before charging, in % of rated power."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def native_value(self) -> float | None:
        slot = self._settings.delay_slot if self._settings else None
        return slot.power_limit / 10 if slot else None

    async def async_set_native_value(self, value: float) -> None:
        await self._async_write_delayed_charge(power_limit=round(value * 10))
