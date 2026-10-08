"""Shared plumbing for GoodWe SEMS+ control entities."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from sems_plus_client import (
    ControlFunction,
    DemandSlot,
    SemsPlusCommandError,
    SemsPlusError,
    TouSlot,
)

from homeassistant.exceptions import HomeAssistantError

from .coordinator import BatteryControls, InverterSettings, SemsPlusStationCoordinator
from .entity import (
    SemsPlusEntity,
    battery_system_device_info,
    device_info,
    work_mode_device_info,
)


@contextmanager
def _write_errors() -> Iterator[None]:
    try:
        yield
    except SemsPlusCommandError as err:
        raise HomeAssistantError(
            f"The device did not accept the change: {err}"
        ) from err
    except SemsPlusError as err:
        raise HomeAssistantError(f"SEMS+ rejected the change: {err}") from err


async def async_write(
    coordinator: SemsPlusStationCoordinator,
    sn: str,
    device_name: str,
    function: ControlFunction,
    value: int,
    log: dict[str, Any],
) -> None:
    with _write_errors():
        await coordinator.async_write(sn, device_name, function, value, log)


class InverterSettingEntity(SemsPlusEntity):
    """A work mode or TOU setting of a battery inverter."""

    _domain: str

    def __init__(
        self,
        coordinator: SemsPlusStationCoordinator,
        sn: str,
        key: str,
        translation_key: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"{sn}-{key}"
        self._attr_translation_key = translation_key or key
        device = coordinator.data.devices[sn]
        self._attr_device_info = device_info(coordinator, device)
        self._set_entity_id(self._domain, device.name, key)

    @property
    def _settings(self) -> InverterSettings | None:
        return self.coordinator.data.settings.get(self._sn)

    @property
    def available(self) -> bool:
        return super().available and self._settings is not None

    async def _async_write_setting(
        self, name: str, value: dict[str, Any], log: dict[str, Any]
    ) -> None:
        with _write_errors():
            await self.coordinator.async_write_setting(self._sn, name, value, log)


# Modes that cannot run together; the web refuses to switch one on while a
# conflicting one is active.
MODE_CONFLICTS: dict[str, frozenset[str]] = {
    "backup_mode": frozenset({"peak_shaving"}),
    "tou_mode": frozenset({"peak_shaving"}),
    "delayed_charge": frozenset({"peak_shaving"}),
    "peak_shaving": frozenset({"backup_mode", "tou_mode", "delayed_charge"}),
}


class WorkModeEntity(InverterSettingEntity):
    """A work mode switch or setting, on the inverter's work-mode device."""

    def __init__(
        self,
        coordinator: SemsPlusStationCoordinator,
        sn: str,
        key: str,
    ) -> None:
        super().__init__(coordinator, sn, key)
        self._attr_device_info = work_mode_device_info(coordinator, sn)

    def _demand_slot(self, peak_shaving: bool) -> DemandSlot:
        settings = self._settings
        slot = None
        if settings is not None:
            slot = settings.peak_slot if peak_shaving else settings.delay_slot
        if slot is None:
            raise HomeAssistantError("This work mode setting is no longer available")
        return slot

    async def _async_write_peak_shaving(self, **changes: Any) -> None:
        slot = replace(self._demand_slot(True), **changes)
        await self._async_write_setting(
            slot.name, slot.peak_shaving_data(), slot.peak_shaving_log()
        )

    async def _async_write_delayed_charge(self, **changes: Any) -> None:
        slot = replace(self._demand_slot(False), **changes)
        await self._async_write_setting(
            slot.name, slot.delayed_charge_data(), slot.delayed_charge_log()
        )


class TouSlotEntity(InverterSettingEntity):
    """One field of one TOU slot, on the inverter's work-mode device.

    Names start "TOU slot N", so a slot's entities sort together there.
    Unused slots start disabled. An entity without a field is the slot itself,
    named by `_slot_translation_key`.
    """

    _slot_translation_key = "tou_slot"

    def __init__(
        self,
        coordinator: SemsPlusStationCoordinator,
        sn: str,
        slot: TouSlot,
        field: str | None,
    ) -> None:
        key = f"tou_slot_{slot.index}" + (f"_{field}" if field else "")
        translation_key = f"tou_slot_{field}" if field else self._slot_translation_key
        super().__init__(coordinator, sn, key, translation_key)
        self._index = slot.index
        self._attr_device_info = work_mode_device_info(coordinator, sn)
        self._attr_translation_placeholders = {"slot": str(slot.index)}
        self._attr_entity_registry_enabled_default = slot.configured

    @property
    def _slot(self) -> TouSlot | None:
        settings = self._settings
        return settings.tou_slots.get(self._index) if settings else None

    @property
    def available(self) -> bool:
        return super().available and self._slot is not None

    async def _async_change_slot(self, change: Callable[[TouSlot], TouSlot]) -> None:
        if (slot := self._slot) is None:
            raise HomeAssistantError("This TOU slot is no longer available")
        try:
            slot = change(slot)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        await self._async_write_setting(
            f"TOU{slot.index}", slot.to_api(), slot.audit_log()
        )

    async def _async_write_slot(self, **changes: Any) -> None:
        await self._async_change_slot(lambda slot: replace(slot, **changes))


class InverterControlEntity(SemsPlusEntity):
    """A control discovered among an inverter's general functions."""

    _domain: str

    def __init__(
        self,
        coordinator: SemsPlusStationCoordinator,
        sn: str,
        control: str,
        entity_key: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._control = control
        self._attr_unique_id = f"{sn}-{control}"
        self._attr_translation_key = entity_key or control
        device = coordinator.data.devices[sn]
        self._attr_device_info = device_info(coordinator, device)
        self._set_entity_id(self._domain, device.name, entity_key or control)

    @property
    def _function(self) -> ControlFunction | None:
        return self.coordinator.data.controls.get(self._sn, {}).get(self._control)

    @property
    def available(self) -> bool:
        return super().available and self._function is not None

    @property
    def _value(self) -> float | None:
        if (function := self._function) is None:
            return None
        return self.coordinator.data.control_values.get(self._sn, {}).get(
            function.address
        )

    async def _async_write(self, value: int, log_value: Any) -> None:
        if (function := self._function) is None:
            raise HomeAssistantError("This control is no longer available")
        await async_write(
            self.coordinator,
            self._sn,
            self.coordinator.data.devices[self._sn].name,
            function,
            value,
            {function.key: log_value},
        )


class BatteryControlEntity(SemsPlusEntity):
    """A control of a battery system; its values are read through the inverter."""

    _domain: str

    def __init__(
        self,
        coordinator: SemsPlusStationCoordinator,
        controls: BatteryControls,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._system_sn = controls.system.sn
        self._attr_unique_id = f"{controls.system.sn}-{key}"
        self._attr_translation_key = key
        self._attr_device_info = battery_system_device_info(coordinator, controls)
        self._set_entity_id(self._domain, controls.system.name, key)

    @property
    def _controls(self) -> BatteryControls | None:
        return self.coordinator.data.battery_systems.get(self._system_sn)

    def _value(self, key: str) -> float | None:
        controls = self._controls
        if controls is None or (function := controls.functions.get(key)) is None:
            return None
        return self.coordinator.data.control_values.get(controls.inverter_sn, {}).get(
            function.address
        )

    @property
    def available(self) -> bool:
        return super().available and self._controls is not None

    async def _async_write(self, key: str, value: int, log: dict[str, Any]) -> None:
        controls = self._controls
        if controls is None or (function := controls.functions.get(key)) is None:
            raise HomeAssistantError("This battery control is no longer available")
        # The battery system is addressed through its inverter, by the key
        # SEMS+ uses for it (e.g. "mppt1_battery").
        await async_write(
            self.coordinator,
            controls.inverter_sn,
            controls.system.key,
            function,
            value,
            log,
        )
