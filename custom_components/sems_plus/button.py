"""Buttons for GoodWe SEMS+ inverter commands."""

from __future__ import annotations

from collections.abc import Iterator

from homeassistant.components.button import (
    DOMAIN as BUTTON_DOMAIN,
    ButtonDeviceClass,
    ButtonEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SemsPlusConfigEntry
from .control import InverterControlEntity
from .coordinator import RESTART, SHUTDOWN, START, SemsPlusStationCoordinator
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
        if sn not in data.devices:
            continue
        for command in (START, SHUTDOWN, RESTART):
            if command in controls:
                yield InverterCommandButton(coordinator, sn, command)


class InverterCommandButton(InverterControlEntity, ButtonEntity):
    """A one-shot inverter command: start, shut down, restart."""

    _domain = BUTTON_DOMAIN

    def __init__(
        self, coordinator: SemsPlusStationCoordinator, sn: str, command: str
    ) -> None:
        super().__init__(coordinator, sn, command)
        if command == RESTART:
            self._attr_device_class = ButtonDeviceClass.RESTART

    async def async_press(self) -> None:
        if (function := self._function) is None:
            raise HomeAssistantError("This control is no longer available")
        # The command's value is its only option ("restart" = 361), or the
        # single value its range allows.
        option = function.options[0] if len(function.options) == 1 else None
        if option is not None and option.get("value") is not None:
            await self._async_write(int(option["value"]), option.get("transKey"))
        elif (bounds := function.bounds) is not None:
            await self._async_write(int(bounds[0]), int(bounds[0]))
        else:
            raise HomeAssistantError("SEMS+ gives no value for this command")
