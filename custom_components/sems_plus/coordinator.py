"""Per-station data coordinator for GoodWe SEMS+."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from sems_plus_client import (
    DELAYED_CHARGE_ON,
    MENU_CODES,
    PEAK_SHAVING_ON,
    Alarm,
    AlarmCounts,
    BatterySystem,
    ControlFunction,
    ControlType,
    DemandSlot,
    Device,
    DeviceDetails,
    DeviceInformation,
    DeviceType,
    FactorValue,
    FirmwareUpdate,
    ForceUpgradeStatus,
    InverterFeatures,
    PowerFlow,
    SemsPlusAuthError,
    SemsPlusClient,
    SemsPlusError,
    SemsPlusRateLimitError,
    StationInfo,
    StationStatistics,
    TouSlot,
    WorkModeInfo,
    assign_demand_slots,
    find_control_functions,
    list_control_functions,
)

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    ALARM_LIST_REFRESH,
    CONF_ALLOW_CONTROL,
    CONF_SCAN_INTERVAL,
    CONF_STATION_ID,
    CONTROL_TREE_REFRESH,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_INFORMATION_REFRESH,
    DEVICE_STATUS_OFFLINE,
    DEVICE_TOPOLOGY_REFRESH,
    DOMAIN,
    FIRMWARE_UPDATE_REFRESH,
    LIFETIME_STATISTICS_REFRESH,
    PAST_YEAR_REFRESH,
    STATISTICS_REFRESH,
)

if TYPE_CHECKING:
    from . import SemsPlusConfigEntry

_LOGGER = logging.getLogger(__name__)

RUN_STOP = "run_stop"
EXPORT_LIMIT = "export_limit"
EXPORT_LIMIT_POWER = "export_limit_power"
RESTART = "restart"
START = "start"
SHUTDOWN = "shutdown"
BACKUP_GRID_CHARGE = "backup_grid_charge"
IMMEDIATE_CHARGE = "immediate_charge"
STOP_CHARGING = "stop_charging"
END_CHARGE_SOC = "end_charge_soc"
CHARGE_POWER = "bat_immediate_charge_power"
_BATTERY_CONTROL_KEYS = frozenset(
    {IMMEDIATE_CHARGE, STOP_CHARGING, END_CHARGE_SOC, CHARGE_POWER}
)


@dataclass(frozen=True, slots=True)
class _ControlSpec:
    """How to recognise one inverter control among its general functions.

    Keys repeat across menus and units (a grid-tie inverter has two
    `limit_setting`s, in W and in %), and the stable `funcKey` is mostly
    missing or shared, so a match needs the key, the widget, and sometimes
    the unit and menu.
    """

    key: str
    control: int
    unit: str | None = None
    menu: str | None = None

    def matches(self, function: ControlFunction) -> bool:
        return (
            function.key == self.key
            and function.control == self.control
            and function.writable
            and (self.unit is None or function.unit == self.unit)
            and (self.menu is None or self.menu in function.path)
            # Whether writes take raw or scaled values is unconfirmed.
            and (self.control != ControlType.NUMBER or function.gain in (None, 1))
        )


INVERTER_CONTROLS: dict[str, _ControlSpec] = {
    RUN_STOP: _ControlSpec("run_stop", ControlType.SWITCH),
    EXPORT_LIMIT: _ControlSpec("grid-tie_power_limit", ControlType.SWITCH),
    EXPORT_LIMIT_POWER: _ControlSpec(
        "limit_setting", ControlType.NUMBER, unit="W", menu="grid-tie_power_limit"
    ),
    RESTART: _ControlSpec("restart", ControlType.COMMAND),
    # Grid-tie inverters start and stop through commands instead of run_stop.
    START: _ControlSpec("start_up", ControlType.COMMAND),
    SHUTDOWN: _ControlSpec("shutdown", ControlType.COMMAND),
    # Charging from the grid in backup mode. The named Backup setting does
    # not report it, so it is read from its register (SEMS+'s own spelling).
    BACKUP_GRID_CHARGE: _ControlSpec(
        "gird_pur_charge", ControlType.SWITCH, menu="backup_mode"
    ),
}


def find_inverter_controls(menus: dict[str, Any]) -> dict[str, ControlFunction]:
    """Match an inverter's general functions to the controls offered."""
    functions = list_control_functions(menus)
    found: dict[str, ControlFunction] = {}
    for name, spec in INVERTER_CONTROLS.items():
        if function := next((f for f in functions if spec.matches(f)), None):
            found[name] = function
    return found


# TOU slots the web offers per work-mode version. Versions 2 and 3 have
# independent mode switches; version 1 has one exclusive mode.
_WORK_MODE_SLOTS = {"1.0": 4, "2.0": 4, "3.0": 8}
WORK_MODE_V1 = "1.0"


class V1Mode(NamedTuple):
    """A work mode of version 1, which runs one mode at a time."""

    setting: str
    # The value the setting holds while the mode is selected; written to select it.
    code: int
    option: str
    log_key: str


V1_MODES = {
    "selfUseMode": V1Mode("SelfUseMode", 0, "self_use", "self_use"),
    "backupMode": V1Mode("BackupMode", 2, "backup", "backup_mode"),
    "TOUMode": V1Mode("TOUMode", 3, "tou", "TOU"),
    "offGridMode": V1Mode("OffGridMode", 1, "off_grid", "off_grid_mode"),
}
WORK_MODE = "INVCurrentWorkMode"
TOU_MODE = "TOUModeEnable"
BACKUP_MODE = "Backup"
OFF_GRID_MODE = "OffGridEnable"
DELAYED_CHARGE_ENABLE = "DelayedChargeEnable"
_DEMAND_SLOTS = ("DemandOrDelayed1", "DemandOrDelayed2")


@dataclass(frozen=True, slots=True)
class ExportLimit:
    """Whether an inverter limits export, and to how many watts."""

    enabled: bool | None
    power: float | None

    @classmethod
    def from_values(
        cls,
        controls: dict[str, ControlFunction],
        values: dict[str, float | None] | None,
    ) -> ExportLimit | None:
        switch = controls.get(EXPORT_LIMIT)
        power = controls.get(EXPORT_LIMIT_POWER)
        if values is None or (switch is None and power is None):
            return None
        state = values.get(switch.address) if switch else None
        on = switch.option_value("remote_Switch_on") if switch else None
        return cls(
            enabled=None if state is None else state == (1 if on is None else on),
            power=values.get(power.address) if power else None,
        )


@dataclass(slots=True)
class InverterSettings:
    """A battery inverter's work modes and TOU schedule (`remote/get`)."""

    work_mode: int | None
    tou_mode: bool | None
    backup_mode: bool | None
    tou_slots: dict[int, TouSlot]
    features: InverterFeatures | None = None
    version: str | None = None
    off_grid_mode: bool | None = None
    # Backup mode's grid charging power, in %.
    backup_charge_power: float | None = None
    peak_slot: DemandSlot | None = None
    delay_slot: DemandSlot | None = None
    delayed_charge_enable: bool | None = None
    # The work modes the web offers for the device (funcKeys); None if unknown.
    visible_modes: frozenset[str] | None = None
    # Version 1's selected mode (a `V1_MODES` funcKey).
    v1_mode: str | None = None

    @property
    def v1(self) -> bool:
        return self.version == WORK_MODE_V1

    @property
    def peak_shaving(self) -> bool | None:
        slot = self.peak_slot
        return None if slot is None else slot.week_enable == PEAK_SHAVING_ON

    @property
    def delayed_charge(self) -> bool | None:
        slot = self.delay_slot
        if slot is None or self.delayed_charge_enable is None:
            return None
        return slot.week_enable == DELAYED_CHARGE_ON and self.delayed_charge_enable

    def mode_visible(self, func_key: str) -> bool:
        return self.visible_modes is None or func_key in self.visible_modes

    @classmethod
    def from_values(
        cls,
        values: dict[str, dict[str, Any]],
        slots: int,
        *,
        version: str | None = None,
        visible_modes: frozenset[str] | None = None,
    ) -> InverterSettings:
        def flag(name: str, field_name: str) -> bool | None:
            value = values.get(name, {}).get(field_name)
            return None if value is None else value == 1

        mode = values.get(WORK_MODE, {}).get(WORK_MODE)
        arm2 = values.get("ARMFunction2", {}).get("ARMFunction2")
        arm4 = values.get("ARMFunction4", {}).get("ARMFunction4")
        charge_power = values.get(BACKUP_MODE, {}).get("BackupPChargeP")
        v1 = version == WORK_MODE_V1
        v1_mode = (
            next(
                (
                    func_key
                    for func_key, mode in V1_MODES.items()
                    if values.get(mode.setting, {}).get(mode.setting) == mode.code
                ),
                None,
            )
            if v1
            else None
        )

        def v1_flag(func_key: str) -> bool | None:
            if not any(mode.setting in values for mode in V1_MODES.values()):
                return None
            return v1_mode == func_key

        peak = delay = None
        if all(name in values for name in _DEMAND_SLOTS):
            peak, delay = assign_demand_slots(
                DemandSlot.from_api(1, values[_DEMAND_SLOTS[0]]),
                DemandSlot.from_api(2, values[_DEMAND_SLOTS[1]]),
            )
        return cls(
            work_mode=mode if isinstance(mode, int) else None,
            tou_mode=v1_flag("TOUMode") if v1 else flag(TOU_MODE, TOU_MODE),
            backup_mode=v1_flag("backupMode")
            if v1
            else flag(BACKUP_MODE, "BackupModeEnable"),
            tou_slots={
                n: TouSlot.from_api(n, values[f"TOU{n}"], v1=v1)
                for n in range(1, slots + 1)
                if f"TOU{n}" in values
            },
            features=InverterFeatures(arm2, arm4)
            if isinstance(arm2, int) and isinstance(arm4, int)
            else None,
            version=version,
            off_grid_mode=v1_flag("offGridMode")
            if v1
            else flag(OFF_GRID_MODE, OFF_GRID_MODE),
            backup_charge_power=float(charge_power)
            if isinstance(charge_power, int | float)
            else None,
            peak_slot=peak,
            delay_slot=delay,
            delayed_charge_enable=flag(DELAYED_CHARGE_ENABLE, DELAYED_CHARGE_ENABLE),
            visible_modes=visible_modes,
            v1_mode=v1_mode,
        )


def _flow_time(flow: PowerFlow) -> str:
    """The flow's station-local timestamp, comparable as text.

    Polls say "2026-10-08T09:10:00", pushes "2026-10-08 09:10:05".
    """
    return (flow.updated_at or "").replace("T", " ")


# Counter factors that reset each period. Around midnight SEMS+ keeps serving
# the previous period for several minutes after the reset, so they are held.
_PERIOD_SUFFIXES = ("Today", "Week", "Month", "Year")


@dataclass(slots=True)
class BatteryControls:
    """A battery system behind an All-in-One and its immediate-charge functions."""

    system: BatterySystem
    inverter_sn: str
    functions: dict[str, ControlFunction]


@dataclass(slots=True)
class StationData:
    """Everything the entities of one station read."""

    info: StationInfo | None
    flow: PowerFlow | None
    devices: dict[str, Device]
    telemetry: dict[str, dict[str, FactorValue]]
    counters: dict[str, dict[str, FactorValue]]
    today: StationStatistics | None
    lifetime: dict[str, float]
    alarm_counts: AlarmCounts | None
    alarms: list[Alarm]
    battery_systems: dict[str, BatteryControls] = field(default_factory=dict)
    # Inverter controls by device serial, then control name (`RUN_STOP`, ...).
    controls: dict[str, dict[str, ControlFunction]] = field(default_factory=dict)
    # Control values by device serial, then function address.
    control_values: dict[str, dict[str, float | None]] = field(default_factory=dict)
    settings: dict[str, InverterSettings] = field(default_factory=dict)
    # Read with or without controls, for the read-only export limit sensors.
    export_limits: dict[str, ExportLimit] = field(default_factory=dict)
    details: dict[str, DeviceDetails] = field(default_factory=dict)
    information: dict[str, DeviceInformation] = field(default_factory=dict)
    # Pending firmware by serial; only devices whose list was read are present.
    firmware_updates: dict[str, list[FirmwareUpdate]] = field(default_factory=dict)
    force_upgrades: dict[str, ForceUpgradeStatus] = field(default_factory=dict)

    def can_apply_firmware(self, sn: str) -> bool | None:
        """Whether this account may install the device's firmware in SEMS+.

        Either the station grants the installer permission, or GoodWe has
        released a forced upgrade the owner may apply. None when unknown.
        """
        if self.info is not None and self.info.can_upgrade_firmware:
            return True
        if (status := self.force_upgrades.get(sn)) is not None:
            return status.owner_can_apply
        return None

    def model(self, device: Device) -> str | None:
        """The product model, e.g. "GW10K-EHA-G20"."""
        if (details := self.details.get(device.sn)) and details.model:
            return details.model
        info = self.information.get(device.sn)
        return info.model if info else None

    def firmware(self, device: Device) -> str | None:
        if (info := self.information.get(device.sn)) and info.firmware:
            return info.firmware
        # A battery rack reports its BMS version among its telemetry.
        version = self.telemetry.get(device.sn, {}).get("version")
        return version if isinstance(version, str) else None


@dataclass(slots=True)
class _Cached[T]:
    value: T
    fetched: datetime


class SemsPlusStationCoordinator(DataUpdateCoordinator[StationData]):
    """Polls one station through the account's shared client."""

    config_entry: SemsPlusConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SemsPlusConfigEntry,
        subentry: ConfigSubentry,
        client: SemsPlusClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {subentry.title}",
            update_interval=timedelta(
                seconds=subentry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self.subentry = subentry
        self.station_id: str = subentry.data[CONF_STATION_ID]
        self.allow_control: bool = subentry.data.get(CONF_ALLOW_CONTROL, False)
        # Whether SEMS+ grants this account remote control, from the last
        # station info read; None until one succeeds.
        self._remote_allowed: bool | None = None
        self._remote_read_allowed: bool | None = None
        # The subentry is titled "<name> Station"; entity IDs use the name.
        self.station_name = subentry.title.removesuffix(" Station") or subentry.title
        self._today: _Cached[StationStatistics] | None = None
        self._lifetime: _Cached[dict[str, float]] | None = None
        self._years: dict[int, _Cached[dict[str, float]]] = {}
        self._alarms: _Cached[list[Alarm]] | None = None
        self._alarm_counts: AlarmCounts | None = None
        self._battery_systems: dict[str, _Cached[list[BatterySystem]]] = {}
        self._control_trees: dict[str, _Cached[dict[str, ControlFunction]]] = {}
        self._battery_functions: dict[str, _Cached[dict[str, ControlFunction]]] = {}
        self._work_modes: dict[str, _Cached[WorkModeInfo]] = {}
        self._visible_modes: dict[str, _Cached[frozenset[str]]] = {}
        self._live_listeners: list[Callable[[], None]] = []
        self._details: _Cached[dict[str, DeviceDetails]] | None = None
        self._information: dict[str, _Cached[DeviceInformation]] = {}
        self._firmware_updates: dict[str, _Cached[list[FirmwareUpdate]]] = {}
        self._force_upgrades: dict[str, _Cached[ForceUpgradeStatus]] = {}

    async def _async_update_data(self) -> StationData:
        try:
            return await self._async_fetch()
        except SemsPlusAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SemsPlusRateLimitError as err:
            raise UpdateFailed(
                f"SEMS+ rate limited; retrying in {err.retry_after}s",
                retry_after=err.retry_after,
            ) from err
        except SemsPlusError as err:
            raise UpdateFailed(f"Error communicating with SEMS+: {err}") from err

    async def _async_fetch(self) -> StationData:
        now = dt_util.now()
        devices = {
            device.sn: device
            for device in await self.client.async_get_devices(self.station_id)
        }
        flow = await self.client.async_get_power_flow(self.station_id)
        # A pushed flow is seconds old; the polled one up to a minute.
        if (
            self.data is not None
            and (live := self.data.flow) is not None
            and _flow_time(live) > _flow_time(flow)
        ):
            flow = live
        info = await self._async_optional(
            self.client.async_get_station_info(self.station_id)
        )

        previous = self.data
        telemetry: dict[str, dict[str, FactorValue]] = {}
        counters: dict[str, dict[str, FactorValue]] = {}
        for device in devices.values():
            # A dongle reports nothing and an offline device returns no values.
            if (
                device.device_type == DeviceType.DONGLE
                or device.status in DEVICE_STATUS_OFFLINE
            ):
                continue
            if (
                values := await self._async_optional(
                    self.client.async_get_telemetry(self.station_id, device)
                )
            ) is not None:
                telemetry[device.sn] = values
            if (
                values := await self._async_optional(
                    self.client.async_get_counters(self.station_id, device)
                )
            ) is not None:
                counters[device.sn] = self._hold_at_midnight(
                    now,
                    values,
                    previous.counters.get(device.sn) if previous else None,
                )

        data = StationData(
            info=info,
            flow=flow,
            devices=devices,
            telemetry=telemetry,
            counters=counters,
            today=await self._async_today(now),
            lifetime=await self._async_lifetime(now, info),
            alarm_counts=None,
            alarms=[],
        )
        data.alarm_counts, data.alarms = await self._async_alarms(now)
        if info is not None:
            self._remote_allowed = info.can_control
            self._remote_read_allowed = info.can_read_controls
        await self._async_device_details(now, data)
        await self._async_firmware_updates(now, data)
        await self._async_battery_systems(now, data)
        # Work modes and the TOU schedule are shown, read-only, whenever SEMS+
        # lets the account read device settings; changing them needs controls.
        if self.controls_enabled or self._remote_read_allowed:
            for device in data.devices.values():
                if device.device_type == DeviceType.ALL_IN_ONE or (
                    device.is_inverter and data.info and data.info.battery_capacity_kwh
                ):
                    await self._async_settings(now, device, data)
        if self.controls_enabled:
            await self._async_controls(now, data)
        elif self._remote_read_allowed:
            await self._async_export_limits(now, data)
        self._update_device_registry(data)
        return data

    @callback
    def async_add_live_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call `listener` whenever a pushed flow arrives."""
        self._live_listeners.append(listener)
        return lambda: self._live_listeners.remove(listener)

    @callback
    def async_handle_live_flow(self, message: dict[str, Any]) -> None:
        """Merge a pushed station flow into the current data.

        Only the flow entities are told, so a push every few seconds does not
        rewrite every entity of the station.
        """
        if self.data is None:
            return
        pushed = PowerFlow.from_api(message)
        if (current := self.data.flow) is not None:
            pushed = replace(
                current,
                **{
                    f.name: value
                    for f in fields(pushed)
                    if (value := getattr(pushed, f.name)) is not None
                },
            )
        self.data.flow = pushed
        for listener in list(self._live_listeners):
            listener()

    @property
    def controls_enabled(self) -> bool:
        """Controls are on for this station and SEMS+ lets the account use them."""
        return self.allow_control and bool(self._remote_allowed)

    async def _async_device_details(self, now: datetime, data: StationData) -> None:
        """Models for every device, and firmware for inverters and dongles."""
        cached = self._details
        if cached is None or now - cached.fetched >= DEVICE_TOPOLOGY_REFRESH:
            details = await self._async_optional(
                self.client.async_get_device_details(self.station_id)
            )
            if details is not None:
                cached = self._details = _Cached(details, now)
        data.details = cached.value if cached else {}
        for device in data.devices.values():
            if (
                not (device.is_inverter or device.device_type == DeviceType.DONGLE)
                or device.status in DEVICE_STATUS_OFFLINE
            ):
                if known := self._information.get(device.sn):
                    data.information[device.sn] = known.value
                continue
            known = self._information.get(device.sn)
            if known is None or now - known.fetched >= DEVICE_INFORMATION_REFRESH:
                information = await self._async_optional(
                    self.client.async_get_device_information(self.station_id, device)
                )
                if information is not None:
                    known = self._information[device.sn] = _Cached(information, now)
            if known is not None:
                data.information[device.sn] = known.value

    async def _async_firmware_updates(self, now: datetime, data: StationData) -> None:
        """Pending firmware for inverters and dongles; other devices get none."""
        for device in data.devices.values():
            if not (device.is_inverter or device.device_type == DeviceType.DONGLE):
                continue
            cached = self._firmware_updates.get(device.sn)
            if cached is None or now - cached.fetched >= FIRMWARE_UPDATE_REFRESH:
                updates = await self._async_optional(
                    self.client.async_get_firmware_updates(self.station_id, device.sn)
                )
                if updates is not None:
                    cached = self._firmware_updates[device.sn] = _Cached(updates, now)
            if cached is not None:
                data.firmware_updates[device.sn] = cached.value
            status = self._force_upgrades.get(device.sn)
            if status is None or now - status.fetched >= FIRMWARE_UPDATE_REFRESH:
                force = await self._async_optional(
                    self.client.async_get_force_upgrade(self.station_id, device.sn)
                )
                if force is not None:
                    status = self._force_upgrades[device.sn] = _Cached(force, now)
            if status is not None:
                data.force_upgrades[device.sn] = status.value

    def _update_device_registry(self, data: StationData) -> None:
        """Fill in models and firmware learnt after the devices were added."""
        registry = dr.async_get(self.hass)
        for device in data.devices.values():
            entry = registry.async_get_device(identifiers={(DOMAIN, device.sn)})
            if entry is None:
                continue
            changes: dict[str, str] = {}
            if (model := data.model(device)) and entry.model_id != model:
                changes["model_id"] = model
            if (firmware := data.firmware(device)) and entry.sw_version != firmware:
                changes["sw_version"] = firmware
            if changes:
                registry.async_update_device(entry.id, **changes)

    async def _async_optional[T](self, request: Any) -> T | None:
        """Await a request whose failure must not take the whole station down."""
        try:
            return await request
        except SemsPlusAuthError, SemsPlusRateLimitError:
            raise
        except SemsPlusError as err:
            _LOGGER.debug("Optional SEMS+ request failed: %s", err)
            return None

    @staticmethod
    def _hold_at_midnight(
        now: datetime,
        values: dict[str, FactorValue],
        previous: dict[str, FactorValue] | None,
    ) -> dict[str, FactorValue]:
        """Keep period counters from before midnight until SEMS+ has rolled over."""
        if previous is None or not (
            (now.hour == 23 and now.minute >= 58) or (now.hour == 0 and now.minute < 20)
        ):
            return values
        return {
            key: previous.get(key, value) if key.endswith(_PERIOD_SUFFIXES) else value
            for key, value in values.items()
        }

    async def _async_today(self, now: datetime) -> StationStatistics | None:
        if (cached := self._today) is not None and (
            now - cached.fetched < STATISTICS_REFRESH
            and cached.fetched.date() == now.date()
        ):
            return cached.value
        today = await self._async_optional(
            self.client.async_get_statistics(
                self.station_id, "day", now.date(), now.date()
            )
        )
        if today is None:
            return cached.value if cached else None
        self._today = _Cached(today, now)
        return today

    async def _async_lifetime(
        self, now: datetime, info: StationInfo | None
    ) -> dict[str, float]:
        """Sum each year's statistics since the station was created.

        A statistics range spanning several years comes back as all zeros, so
        every year is its own request. Past years are cached for a day.
        """
        first_year = info.created.year if info and info.created else now.year
        lifetime: dict[str, float] = {}
        for year in range(first_year, now.year + 1):
            if (totals := await self._async_year(now, year)) is None:
                # A partial sum would make a total_increasing sensor drop.
                return self._lifetime.value if self._lifetime else {}
            for item, value in totals.items():
                lifetime[item] = lifetime.get(item, 0.0) + value
        self._lifetime = _Cached(lifetime, now)
        return lifetime

    async def _async_year(self, now: datetime, year: int) -> dict[str, float] | None:
        cached = self._years.get(year)
        refresh = LIFETIME_STATISTICS_REFRESH if year == now.year else PAST_YEAR_REFRESH
        if cached is not None and now - cached.fetched < refresh:
            return cached.value
        statistics = await self._async_optional(
            self.client.async_get_statistics(
                self.station_id, "year", date(year, 1, 1), date(year, 12, 31)
            )
        )
        if statistics is None:
            return cached.value if cached else None
        totals = {
            item: total
            for item in statistics.series
            if (total := statistics.total(item)) is not None
        }
        self._years[year] = _Cached(totals, now)
        return totals

    async def _async_alarms(
        self, now: datetime
    ) -> tuple[AlarmCounts | None, list[Alarm]]:
        counts = await self._async_optional(
            self.client.async_get_alarm_counts(self.station_id)
        )
        cached = self._alarms
        # Recovered alarms are history, so the list is only re-read when the
        # counts move or it has gone stale.
        if (
            cached is not None
            and counts == self._alarm_counts
            and now - cached.fetched < ALARM_LIST_REFRESH
        ):
            return counts, cached.value
        self._alarm_counts = counts
        alarms = await self._async_optional(
            self.client.async_get_alarms(self.station_id)
        )
        if alarms is None:
            return counts, cached.value if cached else []
        self._alarms = _Cached(alarms, now)
        return counts, alarms

    async def _async_battery_systems(self, now: datetime, data: StationData) -> None:
        """Battery systems behind each All-in-One, and their charge controls."""
        for device in data.devices.values():
            if device.device_type != DeviceType.ALL_IN_ONE:
                continue
            cached = self._battery_systems.get(device.sn)
            if cached is None or now - cached.fetched >= DEVICE_TOPOLOGY_REFRESH:
                systems = await self._async_optional(
                    self.client.async_get_battery_systems(self.station_id, device.sn)
                )
                if systems is not None:
                    cached = self._battery_systems[device.sn] = _Cached(systems, now)
            for system in cached.value if cached else []:
                functions: dict[str, ControlFunction] = {}
                if self.controls_enabled:
                    functions = await self._async_battery_functions(
                        now, device.sn, system
                    )
                data.battery_systems[system.sn] = BatteryControls(
                    system, device.sn, functions
                )

    async def _async_battery_functions(
        self, now: datetime, inverter_sn: str, system: BatterySystem
    ) -> dict[str, ControlFunction]:
        cached = self._battery_functions.get(system.sn)
        if cached is not None and now - cached.fetched < CONTROL_TREE_REFRESH:
            return cached.value
        menus = await self._async_optional(
            self.client.async_get_battery_functions(inverter_sn, system.index)
        )
        if menus is None:
            return cached.value if cached else {}
        functions = find_control_functions(menus, _BATTERY_CONTROL_KEYS)
        self._battery_functions[system.sn] = _Cached(functions, now)
        return functions

    async def _async_inverter_controls(
        self, now: datetime, device: Device
    ) -> dict[str, ControlFunction] | None:
        """An inverter's controls from its general functions, cached."""
        cached = self._control_trees.get(device.sn)
        if cached is None or now - cached.fetched >= CONTROL_TREE_REFRESH:
            menus = await self._async_optional(
                self.client.async_get_general_functions(
                    device.sn, MENU_CODES[device.device_type]
                )
            )
            if menus is not None:
                cached = self._control_trees[device.sn] = _Cached(
                    find_inverter_controls(menus), now
                )
        return cached.value if cached else None

    async def _async_controls(self, now: datetime, data: StationData) -> None:
        """Discover each inverter's controls and read every control value."""
        wanted: dict[str, dict[str, str]] = {}
        for device in data.devices.values():
            if not device.is_inverter:
                continue
            if not (controls := await self._async_inverter_controls(now, device)):
                continue
            data.controls[device.sn] = controls
            functions = wanted.setdefault(device.sn, {})
            for function in controls.values():
                if function.control != ControlType.COMMAND:
                    functions[function.address] = function.id
        for controls in data.battery_systems.values():
            functions = wanted.setdefault(controls.inverter_sn, {})
            for key in (IMMEDIATE_CHARGE, END_CHARGE_SOC, CHARGE_POWER):
                if function := controls.functions.get(key):
                    functions.setdefault(function.address, function.id)
        # One read per inverter covers its own and its batteries' controls.
        for sn, functions in wanted.items():
            if (
                functions
                and (
                    values := await self._async_optional(
                        self.client.async_get_function_values(sn, functions)
                    )
                )
                is not None
            ):
                data.control_values[sn] = values
        for sn, controls in data.controls.items():
            if limit := ExportLimit.from_values(controls, data.control_values.get(sn)):
                data.export_limits[sn] = limit

    async def _async_export_limits(self, now: datetime, data: StationData) -> None:
        """Read only the export limit, to show it while controls are off."""
        for device in data.devices.values():
            if not device.is_inverter:
                continue
            controls = await self._async_inverter_controls(now, device)
            functions = {
                function.address: function.id
                for key in (EXPORT_LIMIT, EXPORT_LIMIT_POWER)
                if controls and (function := controls.get(key))
            }
            if not functions:
                continue
            values = await self._async_optional(
                self.client.async_get_function_values(device.sn, functions)
            )
            if limit := ExportLimit.from_values(controls or {}, values):
                data.export_limits[device.sn] = limit

    async def _async_settings(
        self, now: datetime, device: Device, data: StationData
    ) -> None:
        """Read a battery inverter's work modes and TOU slots in one request."""
        cached = self._work_modes.get(device.sn)
        if cached is None or now - cached.fetched >= CONTROL_TREE_REFRESH:
            info = await self._async_optional(
                self.client.async_get_work_mode(device.sn)
            )
            if info is not None:
                cached = self._work_modes[device.sn] = _Cached(info, now)
        if (
            cached is None
            or (slots := _WORK_MODE_SLOTS.get(cached.value.version)) is None
        ):
            return
        visible = self._visible_modes.get(device.sn)
        if visible is None or now - visible.fetched >= CONTROL_TREE_REFRESH:
            modes = await self._async_optional(
                self.client.async_get_visible_work_modes(
                    device.sn, MENU_CODES[device.device_type]
                )
            )
            if modes is not None:
                visible = self._visible_modes[device.sn] = _Cached(modes, now)
        if cached.value.version == WORK_MODE_V1:
            mode_names = [mode.setting for mode in V1_MODES.values()]
        else:
            mode_names = [
                TOU_MODE,
                BACKUP_MODE,
                OFF_GRID_MODE,
                DELAYED_CHARGE_ENABLE,
                *_DEMAND_SLOTS,
            ]
        names = [WORK_MODE, *mode_names, "ARMFunction2", "ARMFunction4"]
        names += [f"TOU{n}" for n in range(1, slots + 1)]
        values = await self._async_optional(
            self.client.async_remote_get(device.sn, names)
        )
        if values:
            data.settings[device.sn] = InverterSettings.from_values(
                values,
                slots,
                version=cached.value.version,
                visible_modes=visible.value if visible else None,
            )

    async def async_write_setting(
        self, sn: str, name: str, value: dict[str, Any], log: dict[str, Any]
    ) -> None:
        """Write one named setting; the caller refreshes once its writes are done."""
        await self.client.async_remote_set(
            station_id=self.station_id,
            sn=sn,
            device_name=self.data.devices[sn].name,
            name=name,
            data=value,
            log=log,
        )

    async def async_write(
        self,
        sn: str,
        device_name: str,
        function: ControlFunction,
        value: int,
        log: dict[str, Any],
    ) -> None:
        """Write one control value; the caller refreshes once its writes are done."""
        await self.client.async_set_function_values(
            station_id=self.station_id,
            sn=sn,
            device_name=device_name,
            values={function.address: value},
            functions={function.address: function.id},
            log=log,
        )
