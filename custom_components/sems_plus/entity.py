"""Base entity and device info for GoodWe SEMS+."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from functools import wraps
from typing import TYPE_CHECKING, Any

from sems_plus_client import Device, DeviceType

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN, MANUFACTURER
from .coordinator import BatteryControls, SemsPlusStationCoordinator

if TYPE_CHECKING:
    from . import SemsPlusConfigEntry

_MODELS = {
    DeviceType.INVERTER: "Inverter",
    DeviceType.ALL_IN_ONE: "All-in-One",
    DeviceType.BATTERY_RACK: "Battery rack",
    DeviceType.SMART_METER: "Smart meter",
    DeviceType.DONGLE: "Communication dongle",
}


def station_device_info(coordinator: SemsPlusStationCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.station_id)},
        # Every device of the station shows it as "Connected via", so its name
        # says whose devices they are.
        name=f"{coordinator.station_name} Station",
        manufacturer=MANUFACTURER,
        model="SEMS+ station",
        configuration_url="https://semsplus.goodwe.com/",
    )


def device_info(coordinator: SemsPlusStationCoordinator, device: Device) -> DeviceInfo:
    data = coordinator.data
    devices = data.devices if data else {}
    # A smart meter hangs off an inverter; everything else off the station.
    parent = device.parent_sn if device.parent_sn in devices else None
    return DeviceInfo(
        identifiers={(DOMAIN, device.sn)},
        name=device.name,
        manufacturer=MANUFACTURER,
        model=_MODELS.get(device.device_type, device.device_type),
        model_id=data.model(device) if data else None,
        sw_version=data.firmware(device) if data else None,
        serial_number=device.sn,
        via_device=(DOMAIN, parent or coordinator.station_id),
    )


def tou_slot_identifier(sn: str, index: int) -> str:
    """The device a TOU slot had before slots moved to the work-mode device."""
    return f"{sn}-tou_slot_{index}"


def work_mode_device_info(
    coordinator: SemsPlusStationCoordinator, sn: str
) -> DeviceInfo:
    """The inverter's work modes as their own device, so they group together."""
    return DeviceInfo(
        identifiers={(DOMAIN, work_mode_identifier(sn))},
        name=f"{coordinator.data.devices[sn].name} Work mode",
        manufacturer=MANUFACTURER,
        model="Work mode",
        via_device=(DOMAIN, sn),
    )


def work_mode_identifier(sn: str) -> str:
    return f"{sn}-work_mode"


def battery_system_device_info(
    coordinator: SemsPlusStationCoordinator, controls: BatteryControls
) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, controls.system.sn)},
        name=controls.system.name,
        manufacturer=MANUFACTURER,
        model="Battery system",
        serial_number=controls.system.sn,
        via_device=(DOMAIN, controls.inverter_sn),
    )


class SemsPlusEntity(CoordinatorEntity[SemsPlusStationCoordinator]):
    """An entity of one station."""

    _attr_has_entity_name = True
    # The value a control shows while its write is under way.
    _pending: Any = None
    _pending_written = False

    @asynccontextmanager
    async def _async_pending(self, value: Any) -> AsyncIterator[None]:
        """Show `value` while the writes inside run, then read SEMS+ again.

        A write waits for the device, which takes seconds, and meanwhile the
        frontend would fall back to the old state. The value stays until the
        read after the writes, and is dropped if a write fails.
        """
        self._pending, self._pending_written = value, False
        self.async_write_ha_state()
        try:
            yield
        except BaseException:
            self._pending = None
            self.async_write_ha_state()
            raise
        self._pending_written = True
        await self.coordinator.async_request_refresh()

    async def _async_refresh_after_write(self) -> None:
        # Inside `_async_pending` the refresh waits until every write is done.
        if self._pending is None:
            await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._pending_written:
            self._pending, self._pending_written = None, False
        super()._handle_coordinator_update()

    def _set_entity_id(self, domain: str, *parts: str) -> None:
        """Suggest an entity ID that names the station.

        Displayed names stay short ("All-in-One 1 PV1 power"), but device names
        repeat across stations, so the ID carries the station to stay unique,
        and the integration so it says where the entity comes from.
        Built from keys rather than translated names, so it does not depend on
        the language.
        """
        object_id = slugify(" ".join((DOMAIN, self.coordinator.station_name, *parts)))
        self.entity_id = f"{domain}.{object_id}"


def shows_pending[E: SemsPlusEntity, T](state: Callable[[E], T]) -> Callable[[E], T]:
    """Make a state property report the value being written, while there is one."""

    @wraps(state)
    def wrapper(self: E) -> T:
        return self._pending if self._pending is not None else state(self)

    return wrapper


def async_add_station_entities(
    entry: SemsPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[SemsPlusStationCoordinator], Iterable[SemsPlusEntity]],
) -> None:
    """Add each station's entities under its subentry, as their data appears.

    A request that failed during setup would otherwise leave its entities
    missing until the entry was reloaded.
    """
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        known: set[str] = set()

        @callback
        def _add(
            coordinator: SemsPlusStationCoordinator = coordinator,
            subentry_id: str = subentry_id,
            known: set[str] = known,
        ) -> None:
            if coordinator.data is None:
                return
            new = [e for e in build(coordinator) if e.unique_id not in known]
            if new:
                known.update(str(e.unique_id) for e in new)
                async_add_entities(new, config_subentry_id=subentry_id)

        _add()
        entry.async_on_unload(coordinator.async_add_listener(_add))
