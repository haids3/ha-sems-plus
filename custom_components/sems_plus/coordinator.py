"""Per-station data coordinator for GoodWe SEMS+."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from sems_plus_client import (
    Alarm,
    AlarmCounts,
    BatterySystem,
    ControlFunction,
    Device,
    DeviceType,
    FactorValue,
    PowerFlow,
    SemsPlusAuthError,
    SemsPlusClient,
    SemsPlusError,
    SemsPlusRateLimitError,
    StationInfo,
    StationStatistics,
    find_control_functions,
)

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    ALARM_LIST_REFRESH,
    CONF_ALLOW_CONTROL,
    CONF_SCAN_INTERVAL,
    CONF_STATION_ID,
    CONTROL_TREE_REFRESH,
    DEFAULT_SCAN_INTERVAL,
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
IMMEDIATE_CHARGE = "immediate_charge"
STOP_CHARGING = "stop_charging"
END_CHARGE_SOC = "end_charge_soc"
CHARGE_POWER = "bat_immediate_charge_power"
_BATTERY_CONTROL_KEYS = frozenset(
    {IMMEDIATE_CHARGE, STOP_CHARGING, END_CHARGE_SOC, CHARGE_POWER}
)

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
    run_stop: dict[str, ControlFunction] = field(default_factory=dict)
    # Control values by device serial, then function address.
    control_values: dict[str, dict[str, float | None]] = field(default_factory=dict)


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
        await self._async_battery_systems(now, data)
        if self.allow_control:
            await self._async_controls(now, data)
        return data

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
                if self.allow_control:
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
        """Find each inverter's run/stop function and read every control value."""
        wanted: dict[str, dict[str, str]] = {}
        for device in data.devices.values():
            if not device.is_inverter:
                continue
            cached = self._control_trees.get(device.sn)
            if cached is None or now - cached.fetched >= CONTROL_TREE_REFRESH:
                tree = await self._async_optional(
                    self.client.async_get_control_tree(device.sn)
                )
                if tree is not None:
                    cached = self._control_trees[device.sn] = _Cached(
                        find_control_functions(tree, {RUN_STOP}), now
                    )
            if (
                cached
                and (run_stop := cached.value.get(RUN_STOP))
                and run_stop.writable
            ):
                data.run_stop[device.sn] = run_stop
                wanted.setdefault(device.sn, {})[run_stop.address] = run_stop.id
        for controls in data.battery_systems.values():
            functions = wanted.setdefault(controls.inverter_sn, {})
            for key in (IMMEDIATE_CHARGE, END_CHARGE_SOC, CHARGE_POWER):
                if function := controls.functions.get(key):
                    functions.setdefault(function.address, function.id)
        # One read per inverter covers its run/stop and its batteries' controls.
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
