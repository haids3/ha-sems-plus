"""Sensors for GoodWe SEMS+ stations and devices."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal

from sems_plus_client import WORK_MODES, Device, DeviceType

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import SemsPlusConfigEntry
from .const import DEVICE_STATUS, DEVICE_STATUS_OFFLINE, STATION_STATUS
from .control import TouSlotEntity
from .coordinator import SemsPlusStationCoordinator, StationData
from .entity import (
    SemsPlusEntity,
    async_add_station_entities,
    device_info,
    station_device_info,
    work_mode_device_info,
)

PARALLEL_UPDATES = 0

_PHASES = ("A", "B", "C")
_PV_STRINGS = range(1, 5)


@dataclass(frozen=True, kw_only=True)
class StationSensorDescription(SensorEntityDescription):
    value_fn: Callable[[StationData], StateType]
    exists_fn: Callable[[StationData], bool] = lambda data: True
    # Updated by the live feed between polls.
    live: bool = False


@dataclass(frozen=True, kw_only=True)
class DeviceSensorDescription(SensorEntityDescription):
    """A value keyed by SEMS+ factor code in a device's telemetry or counters."""

    factor: str
    source: Literal["telemetry", "counters"] = "telemetry"


def _power(key: str, factor: str, **kwargs: Any) -> DeviceSensorDescription:
    return DeviceSensorDescription(
        key=key,
        factor=factor,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        **kwargs,
    )


def _voltage(key: str, factor: str, **kwargs: Any) -> DeviceSensorDescription:
    return DeviceSensorDescription(
        key=key,
        factor=factor,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        **kwargs,
    )


def _current(key: str, factor: str, **kwargs: Any) -> DeviceSensorDescription:
    return DeviceSensorDescription(
        key=key,
        factor=factor,
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        **kwargs,
    )


def _energy(key: str, factor: str, **kwargs: Any) -> DeviceSensorDescription:
    return DeviceSensorDescription(
        key=key,
        factor=factor,
        source="counters",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        **kwargs,
    )


def _phase_sensors(
    kind: Callable[..., DeviceSensorDescription], key: str, factor: str
) -> list[DeviceSensorDescription]:
    return [
        kind(
            f"{key}_{phase.lower()}",
            f"PHASE-{phase}:{factor}",
            translation_key=f"phase_{key}",
            translation_placeholders={"phase": phase},
        )
        for phase in _PHASES
    ]


def _pv_string_sensors() -> list[DeviceSensorDescription]:
    sensors: list[DeviceSensorDescription] = []
    for index in _PV_STRINGS:
        placeholders = {"string": str(index)}
        sensors += [
            _power(
                f"pv{index}_power",
                f"MPPT-{index}:Ppv",
                translation_key="pv_string_power",
                translation_placeholders=placeholders,
            ),
            _voltage(
                f"pv{index}_voltage",
                f"MPPT-{index}:Vpv",
                translation_key="pv_string_voltage",
                translation_placeholders=placeholders,
            ),
            _current(
                f"pv{index}_current",
                f"MPPT-{index}:Ipv",
                translation_key="pv_string_current",
                translation_placeholders=placeholders,
            ),
        ]
    return sensors


def _battery_energy() -> list[DeviceSensorDescription]:
    return [
        _energy(
            "battery_charge_today",
            "proCharStatsToday",
            translation_key="battery_charge_today",
        ),
        _energy(
            "battery_charge_total",
            "proCharStatsTotal",
            translation_key="battery_charge_total",
        ),
        _energy(
            "battery_discharge_today",
            "proDischarStatsToday",
            translation_key="battery_discharge_today",
        ),
        _energy(
            "battery_discharge_total",
            "proDischarStatsTotal",
            translation_key="battery_discharge_total",
        ),
    ]


INVERTER_SENSORS: list[DeviceSensorDescription] = [
    _power("power", "pAc", name=None),
    DeviceSensorDescription(
        key="temperature",
        factor="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DeviceSensorDescription(
        key="total_hours",
        factor="hTotal",
        translation_key="total_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _voltage("ac_voltage", "Vac", translation_key="ac_voltage"),
    *_phase_sensors(_voltage, "ac_voltage", "Vac"),
    _current("ac_current", "Iac", translation_key="ac_current"),
    DeviceSensorDescription(
        key="ac_frequency",
        factor="Fac",
        translation_key="ac_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DeviceSensorDescription(
        key="power_factor",
        factor="gridPF",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    *_pv_string_sensors(),
    DeviceSensorDescription(
        key="rated_power",
        factor="ratedPower",
        source="counters",
        translation_key="rated_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _energy("pv_energy_today", "proPvStatsToday", translation_key="pv_energy_today"),
    _energy("pv_energy_week", "proPvStatsWeek", translation_key="pv_energy_week"),
    _energy("pv_energy_month", "proPvStatsMonth", translation_key="pv_energy_month"),
    _energy("pv_energy_year", "proPvStatsYear", translation_key="pv_energy_year"),
    _energy("pv_energy_total", "proPvStatsTotal", translation_key="pv_energy_total"),
    *_battery_energy(),
]

BATTERY_RACK_SENSORS: list[DeviceSensorDescription] = [
    DeviceSensorDescription(
        key="soc",
        factor="soc",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DeviceSensorDescription(
        key="soh",
        factor="soh",
        translation_key="state_of_health",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _power("power", "pBat", translation_key="battery_power"),
    _voltage("voltage", "voltage"),
    _current("current", "a"),
    DeviceSensorDescription(
        key="max_cell_temperature",
        factor="tempMaxCell",
        translation_key="max_cell_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DeviceSensorDescription(
        key="min_cell_temperature",
        factor="tempMinCell",
        translation_key="min_cell_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    *(
        DeviceSensorDescription(
            key=key,
            factor=factor,
            translation_key=key,
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
            suggested_display_precision=0,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        for key, factor in (
            ("max_cell_voltage", "vMaxCell"),
            ("min_cell_voltage", "vMinCell"),
        )
    ),
    _power(
        "max_charge_power",
        "pMaxChar",
        translation_key="max_charge_power",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _power(
        "max_discharge_power",
        "pMaxDischar",
        translation_key="max_discharge_power",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _current(
        "max_charge_current",
        "aMaxChar",
        translation_key="max_charge_current",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _current(
        "max_discharge_current",
        "aMaxDischar",
        translation_key="max_discharge_current",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    *_battery_energy(),
]

METER_SENSORS: list[DeviceSensorDescription] = [
    _power("power", "totalPac", name=None),
    *_phase_sensors(_power, "power", "pAc"),
    *_phase_sensors(_voltage, "voltage", "voltage"),
    *_phase_sensors(_current, "current", "current"),
    DeviceSensorDescription(
        key="power_factor",
        factor="pf",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DeviceSensorDescription(
        key="frequency",
        factor="fac",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _energy("import_today", "proPurchaseStatsToday", translation_key="import_today"),
    _energy("import_total", "proPurchaseStatsTotal", translation_key="import_total"),
    _energy("export_today", "proGridStatsToday", translation_key="export_today"),
    _energy("export_total", "proGridStatsTotal", translation_key="export_total"),
]

DEVICE_SENSORS: dict[str, list[DeviceSensorDescription]] = {
    DeviceType.INVERTER: INVERTER_SENSORS,
    DeviceType.ALL_IN_ONE: INVERTER_SENSORS,
    DeviceType.BATTERY_RACK: BATTERY_RACK_SENSORS,
    DeviceType.SMART_METER: METER_SENSORS,
}

DEVICE_STATUS_SENSOR = SensorEntityDescription(
    key="status",
    translation_key="device_status",
    device_class=SensorDeviceClass.ENUM,
    options=sorted(set(DEVICE_STATUS.values())),
    entity_category=EntityCategory.DIAGNOSTIC,
)


def _flow(attr: str) -> Callable[[StationData], StateType]:
    return lambda data: getattr(data.flow, attr) if data.flow else None


def _today(item: str) -> Callable[[StationData], StateType]:
    return lambda data: data.today.total(item) if data.today else None


def _ratio(numerator: str, denominator: str) -> Callable[[StationData], StateType]:
    def value(data: StationData) -> StateType:
        top = data.lifetime.get(numerator)
        bottom = data.lifetime.get(denominator)
        return round(top / bottom * 100, 1) if top is not None and bottom else None

    return value


def _station_power(
    key: str, attr: str, item: str, *, always: bool = False
) -> StationSensorDescription:
    """A live flow sensor, created when the station's flow lists `item`.

    Without that list, the core flows are always created and the rest once
    they report a value.
    """

    def exists(data: StationData) -> bool:
        if data.info is not None and data.info.flow_items:
            return item in data.info.flow_items
        return always or (
            data.flow is not None and getattr(data.flow, attr) is not None
        )

    return StationSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_flow(attr),
        exists_fn=exists,
        live=True,
    )


def _device_count(*device_types: str) -> Callable[[StationData], int]:
    return lambda data: sum(
        device.device_type in device_types for device in data.devices.values()
    )


_inverter_count = _device_count(DeviceType.INVERTER, DeviceType.ALL_IN_ONE)
_meter_count = _device_count(DeviceType.SMART_METER)


def _station_energy(
    key: str,
    item: str,
    *,
    lifetime: bool,
    device_count: Callable[[StationData], int] | None,
) -> StationSensorDescription:
    return StationSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=(lambda data: data.lifetime.get(item)) if lifetime else _today(item),
        # With exactly one device reporting the same counter, the station
        # total is a duplicate of that device's sensor.
        exists_fn=(lambda data: device_count(data) != 1)
        if device_count
        else (lambda data: True),
    )


# Station energy items, with the devices that report the same counter.
_ENERGY_ITEMS: dict[str, tuple[str, Callable[[StationData], int] | None]] = {
    "production": ("proSystemTotalStats", _inverter_count),
    "import": ("proPurchaseStats", _meter_count),
    "export": ("proGridStats", _meter_count),
    "consumption": ("proConsumStats", None),
    "self_use": ("proSelfConsumStats", None),
    "battery_charge": ("proCharStats", _inverter_count),
    "battery_discharge": ("proDischarStats", _inverter_count),
}

STATION_SENSORS: list[StationSensorDescription] = [
    _station_power("pv_power", "pv", "pSystem", always=True),
    _station_power("battery_power", "battery", "pBat", always=True),
    _station_power("grid_power", "grid", "pGrid", always=True),
    _station_power("load_power", "load", "pConsum", always=True),
    _station_power("third_party_pv_power", "third_party_pv", "pThird"),
    _station_power("ev_charger_power", "ev_charger", "pEvChar"),
    _station_power("heat_pump_power", "heat_pump", "pHeatPump"),
    _station_power("generator_power", "generator", "pDiesel"),
    StationSensorDescription(
        key="soc",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_flow("soc"),
        exists_fn=lambda data: data.flow is not None and data.flow.soc is not None,
        live=True,
    ),
    StationSensorDescription(
        key="status",
        translation_key="station_status",
        device_class=SensorDeviceClass.ENUM,
        options=sorted(set(STATION_STATUS.values())),
        value_fn=lambda data: (
            STATION_STATUS.get(data.info.status)
            if data.info and data.info.status is not None
            else None
        ),
    ),
    StationSensorDescription(
        key="active_alarms",
        translation_key="active_alarms",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.alarm_counts.active if data.alarm_counts else None,
    ),
    *(
        _station_energy(f"{name}_today", item, lifetime=False, device_count=count)
        for name, (item, count) in _ENERGY_ITEMS.items()
    ),
    *(
        _station_energy(f"{name}_total", item, lifetime=True, device_count=count)
        for name, (item, count) in _ENERGY_ITEMS.items()
    ),
    StationSensorDescription(
        key="self_sufficiency_today",
        translation_key="self_sufficiency_today",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            data.today.totals.get("contributionRate") if data.today else None
        ),
    ),
    StationSensorDescription(
        key="self_use_rate_today",
        translation_key="self_use_rate_today",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            data.today.totals.get("proSelfConsumRate") if data.today else None
        ),
    ),
    StationSensorDescription(
        key="self_sufficiency_total",
        translation_key="self_sufficiency_total",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_ratio("proSelfConsumStats", "proConsumStats"),
    ),
    StationSensorDescription(
        key="self_use_rate_total",
        translation_key="self_use_rate_total",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_ratio("proSelfConsumStats", "proSystemTotalStats"),
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SemsPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_station_entities(entry, async_add_entities, _build)


def _build(coordinator: SemsPlusStationCoordinator) -> Iterator[SemsPlusEntity]:
    data = coordinator.data
    for description in STATION_SENSORS:
        if description.exists_fn(data):
            yield StationSensor(coordinator, description)
    for sn, settings in data.settings.items():
        if sn not in data.devices:
            continue
        if settings.work_mode is not None:
            yield WorkModeSensor(coordinator, data.devices[sn])
        for slot in settings.tou_slots.values():
            yield TouSlotSensor(coordinator, sn, slot, None)
    for sn, limit in data.export_limits.items():
        if sn in data.devices and limit.power is not None:
            yield ExportLimitPowerSensor(coordinator, data.devices[sn])
    for device in data.devices.values():
        yield DeviceStatusSensor(coordinator, device)
        if data.firmware(device) is not None:
            yield FirmwareSensor(coordinator, device)
        for description in DEVICE_SENSORS.get(device.device_type, []):
            values = getattr(data, description.source).get(device.sn, {})
            # Devices list factors they never fill (a meter's phase voltage),
            # so wait for a value before creating the sensor.
            if values.get(description.factor) is not None:
                yield DeviceSensor(coordinator, device, description)


class StationSensor(SemsPlusEntity, SensorEntity):
    entity_description: StationSensorDescription

    def __init__(
        self,
        coordinator: SemsPlusStationCoordinator,
        description: StationSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.station_id}-{description.key}"
        self._attr_device_info = station_device_info(coordinator)
        self._set_entity_id(SENSOR_DOMAIN, description.key)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.entity_description.live:
            self.async_on_remove(
                self.coordinator.async_add_live_listener(self.async_write_ha_state)
            )

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "active_alarms":
            return None
        return {
            "alarms": [
                {
                    "name": alarm.name,
                    "code": alarm.code,
                    "device": alarm.device_name,
                    "since": alarm.happened_at.isoformat()
                    if alarm.happened_at
                    else None,
                }
                for alarm in self.coordinator.data.alarms
                if alarm.active
            ]
        }


class _DeviceEntity(SemsPlusEntity):
    def __init__(self, coordinator: SemsPlusStationCoordinator, device: Device) -> None:
        super().__init__(coordinator)
        self._sn = device.sn
        self._attr_device_info = device_info(coordinator, device)

    @property
    def _device(self) -> Device | None:
        return self.coordinator.data.devices.get(self._sn)


class DeviceStatusSensor(_DeviceEntity, SensorEntity):
    entity_description = DEVICE_STATUS_SENSOR

    def __init__(self, coordinator: SemsPlusStationCoordinator, device: Device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.sn}-status"
        self._set_entity_id(SENSOR_DOMAIN, device.name, "status")

    @property
    def native_value(self) -> str | None:
        device = self._device
        return (
            DEVICE_STATUS.get(device.status)
            if device and device.status is not None
            else None
        )


class ExportLimitPowerSensor(_DeviceEntity, SensorEntity):
    """The export limit's power setting; readable without controls."""

    _attr_translation_key = "export_limit_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SemsPlusStationCoordinator, device: Device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.sn}-export_limit_power"
        self._set_entity_id(SENSOR_DOMAIN, device.name, "export_limit_power")

    @property
    def available(self) -> bool:
        return super().available and self._sn in self.coordinator.data.export_limits

    @property
    def native_value(self) -> float | None:
        limit = self.coordinator.data.export_limits.get(self._sn)
        return limit.power if limit else None


class FirmwareSensor(_DeviceEntity, SensorEntity):
    """The firmware version SEMS+ reports for the device.

    The inverter's and dongle's from their information panel, a battery
    rack's BMS version from its telemetry.
    """

    _attr_translation_key = "firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SemsPlusStationCoordinator, device: Device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.sn}-firmware"
        self._set_entity_id(SENSOR_DOMAIN, device.name, "firmware")

    @property
    def native_value(self) -> str | None:
        device = self._device
        return self.coordinator.data.firmware(device) if device else None


class WorkModeSensor(_DeviceEntity, SensorEntity):
    """The mode the inverter is running in right now.

    The main entity of the work-mode device, so it carries the device's name.
    """

    _attr_translation_key = "work_mode"
    _attr_name = None
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = sorted(set(WORK_MODES.values()))

    def __init__(self, coordinator: SemsPlusStationCoordinator, device: Device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.sn}-work_mode"
        self._attr_device_info = work_mode_device_info(coordinator, device.sn)
        self._set_entity_id(SENSOR_DOMAIN, device.name, "work_mode")

    @property
    def available(self) -> bool:
        return super().available and self._sn in self.coordinator.data.settings

    @property
    def native_value(self) -> str | None:
        settings = self.coordinator.data.settings.get(self._sn)
        if settings is None or settings.work_mode is None:
            return None
        return WORK_MODES.get(settings.work_mode)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if (settings := self.coordinator.data.settings.get(self._sn)) is None:
            return None
        return {
            "tou_mode": settings.tou_mode,
            "backup_mode": settings.backup_mode,
            "off_grid_mode": settings.off_grid_mode,
            "peak_shaving": settings.peak_shaving,
            "delayed_charge": settings.delayed_charge,
        }


_WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


class TouSlotSensor(TouSlotEntity, SensorEntity):
    """A TOU slot at a glance: off, charge or discharge, with its schedule.

    Read-only, so the schedule shows even when controls are off.
    """

    _domain = SENSOR_DOMAIN
    _slot_translation_key = "tou_slot_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["off", "charge", "discharge"]

    @property
    def native_value(self) -> str | None:
        if (slot := self._slot) is None:
            return None
        if not slot.enabled:
            return "off"
        return "charge" if slot.charging else "discharge"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if (slot := self._slot) is None:
            return None
        return {
            "mode": "charge" if slot.charging else "discharge",
            "start": slot.start,
            "end": slot.end,
            "power": slot.power_percent,
            "power_limit": None
            if slot.charging
            else ("export" if slot.export_limited else "battery"),
            "cutoff_soc": slot.cutoff_soc,
            "days": [_WEEKDAYS[d] for d in slot.weekdays if 0 <= d < 7],
            "months": [m + 1 for m in slot.calendar_months],
        }


class DeviceSensor(_DeviceEntity, SensorEntity):
    entity_description: DeviceSensorDescription

    def __init__(
        self,
        coordinator: SemsPlusStationCoordinator,
        device: Device,
        description: DeviceSensorDescription,
    ) -> None:
        super().__init__(coordinator, device)
        self.entity_description = description
        self._attr_unique_id = f"{device.sn}-{description.key}"
        self._set_entity_id(SENSOR_DOMAIN, device.name, description.key)

    @property
    def available(self) -> bool:
        device = self._device
        return (
            super().available
            and device is not None
            and device.status not in DEVICE_STATUS_OFFLINE
            and self._sn
            in getattr(self.coordinator.data, self.entity_description.source)
        )

    @property
    def native_value(self) -> StateType:
        values = getattr(self.coordinator.data, self.entity_description.source)
        value = values.get(self._sn, {}).get(self.entity_description.factor)
        return value if isinstance(value, float) else None
