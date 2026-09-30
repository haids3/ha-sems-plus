"""Numbers for GoodWe SEMS+ battery charging."""

from __future__ import annotations

from collections.abc import Iterator

from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .control import BatteryControlEntity
from .coordinator import (
    CHARGE_POWER,
    END_CHARGE_SOC,
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
    for controls in coordinator.data.battery_systems.values():
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
