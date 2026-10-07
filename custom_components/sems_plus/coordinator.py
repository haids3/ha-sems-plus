"""Per-station data coordinator for GoodWe SEMS+."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from sems_plus_client import (
    MENU_CODES,
    Alarm,
    AlarmCounts,
    BatterySystem,
    ControlFunction,
    ControlType,
    Device,
    DeviceDetails,
    DeviceInformation,
    DeviceType,
    FactorValue,
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
}


def find_inverter_controls(menus: dict[str, Any]) -> dict[str, ControlFunction]:
    """Match an inverter's general functions to the controls offered."""
    functions = list_control_functions(menus)
    found: dict[str, ControlFunction] = {}
    for name, spec in INVERTER_CONTROLS.items():
        if function := next((f for f in functions if spec.matches(f)), None):
            found[name] = function
    return found


# Work-mode versions whose modes are independent switches and whose TOU
# power is per-mille. Version 1 has one exclusive mode and is not handled.
_WORK_MODE_SLOTS = {"2.0": 4, "3.0": 8}
WORK_MODE = "INVCurrentWorkMode"
TOU_MODE = "TOUModeEnable"
BACKUP_MODE = "Backup"


@dataclass(slots=True)
class InverterSettings:
    """A battery inverter's work modes and TOU schedule (`remote/get`)."""

    work_mode: int | None
    tou_mode: bool | None
    backup_mode: bool | None
    tou_slots: dict[int, TouSlot]
    features: InverterFeatures | None = None

    @classmethod
    def from_values(
        cls, values: dict[str, dict[str, Any]], slots: int
    ) -> InverterSettings:
        def flag(name: str, field_name: str) -> bool | None:
            value = values.get(name, {}).get(field_name)
            return None if value is None else value == 1

        mode = values.get(WORK_MODE, {}).get(WORK_MODE)
        arm2 = values.get("ARMFunction2", {}).get("ARMFunction2")
        arm4 = values.get("ARMFunction4", {}).get("ARMFunction4")
        return cls(
            work_mode=mode if isinstance(mode, int) else None,
            tou_mode=flag(TOU_MODE, TOU_MODE),
            backup_mode=flag(BACKUP_MODE, "BackupModeEnable"),
            tou_slots={
                n: TouSlot.from_api(n, values[f"TOU{n}"])
                for n in range(1, slots + 1)
                if f"TOU{n}" in values
            },
            features=InverterFeatures(arm2, arm4)
            if isinstance(arm2, int) and isinstance(arm4, int)
            else None,
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
    details: dict[str, DeviceDetails] = field(default_factory=dict)
    information: dict[str, DeviceInformation] = field(default_factory=dict)

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
        self._live_listeners: list[Callable[[], None]] = []
        self._details: _Cached[dict[str, DeviceDetails]] | None = None
        self._information: dict[str, _Cached[DeviceInformation]] = {}

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

    async def _async_controls(self, now: datetime, data: StationData) -> None:
        """Discover each inverter's controls and read every control value."""
        wanted: dict[str, dict[str, str]] = {}
        for device in data.devices.values():
            if not device.is_inverter:
                continue
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
            if not cached or not cached.value:
                continue
            data.controls[device.sn] = cached.value
            functions = wanted.setdefault(device.sn, {})
            for function in cached.value.values():
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
        names = [WORK_MODE, TOU_MODE, BACKUP_MODE, "ARMFunction2", "ARMFunction4"]
        names += [f"TOU{n}" for n in range(1, slots + 1)]
        values = await self._async_optional(
            self.client.async_remote_get(device.sn, names)
        )
        if values:
            data.settings[device.sn] = InverterSettings.from_values(values, slots)

    async def async_write_setting(
        self, sn: str, name: str, value: dict[str, Any], log: dict[str, Any]
    ) -> None:
        """Write one named setting, then refresh so entities show the result."""
        await self.client.async_remote_set(
            station_id=self.station_id,
            sn=sn,
            device_name=self.data.devices[sn].name,
            name=name,
            data=value,
            log=log,
        )
        await self.async_request_refresh()

    async def async_write(
        self,
        sn: str,
        device_name: str,
        function: ControlFunction,
        value: int,
        log: dict[str, Any],
    ) -> None:
        """Write one control value, then refresh so entities show the result."""
        await self.client.async_set_function_values(
            station_id=self.station_id,
            sn=sn,
            device_name=device_name,
            values={function.address: value},
            functions={function.address: function.id},
            log=log,
        )
        await self.async_request_refresh()
