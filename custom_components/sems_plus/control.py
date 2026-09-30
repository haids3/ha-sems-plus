"""Shared plumbing for GoodWe SEMS+ control entities."""

from __future__ import annotations

from typing import Any

from sems_plus_client import ControlFunction, SemsPlusError

from homeassistant.exceptions import HomeAssistantError

from .coordinator import BatteryControls, SemsPlusStationCoordinator
from .entity import SemsPlusEntity, battery_system_device_info


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
    except SemsPlusError as err:
        raise HomeAssistantError(f"SEMS+ rejected the change: {err}") from err


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
