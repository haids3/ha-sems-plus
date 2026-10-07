"""Shared plumbing for GoodWe SEMS+ control entities."""

from __future__ import annotations

from typing import Any

from sems_plus_client import ControlFunction, SemsPlusCommandError, SemsPlusError

from homeassistant.exceptions import HomeAssistantError

from .coordinator import BatteryControls, SemsPlusStationCoordinator
from .entity import SemsPlusEntity, battery_system_device_info, device_info


async def async_write(
    coordinator: SemsPlusStationCoordinator,
    sn: str,
    device_name: str,
    function: ControlFunction,
    value: int,
    log: dict[str, Any],
) -> None:
    try:
        await coordinator.async_write(sn, device_name, function, value, log)
    except SemsPlusCommandError as err:
        raise HomeAssistantError(
            f"The device did not accept the change: {err}"
        ) from err
    except SemsPlusError as err:
        raise HomeAssistantError(f"SEMS+ rejected the change: {err}") from err


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
