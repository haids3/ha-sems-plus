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
from .coordinator import (
    RESTART,
    RUN_STOP,
    SHUTDOWN,
    START,
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
    data = coordinator.data
    for sn, controls in data.controls.items():
        if sn not in data.devices:
            continue
        for command in (START, SHUTDOWN, RESTART):
            if command in controls:
                yield InverterCommandButton(coordinator, sn, command)
        if RUN_STOP in controls:
            yield RunStopButton(coordinator, sn, start=True)
            yield RunStopButton(coordinator, sn, start=False)


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


class RunStopButton(InverterControlEntity, ButtonEntity):
    """Start or stop an All-in-One through its run/stop function.

    The register behind it is a write-only command, so SEMS+ only knows the
    last value sent, not whether the inverter runs; the status sensor does.
    """

    _domain = BUTTON_DOMAIN

    def __init__(
        self, coordinator: SemsPlusStationCoordinator, sn: str, *, start: bool
    ) -> None:
        key = "start" if start else "stop"
        super().__init__(coordinator, sn, RUN_STOP, key)
        self._attr_unique_id = f"{sn}-{RUN_STOP}_{key}"
        self._trans_key = "remote_Switch_on" if start else "remote_Switch_off"
        self._fallback = int(start)

    async def async_press(self) -> None:
        function = self._function
        value = function.option_value(self._trans_key) if function else None
        await self._async_write(
            value if value is not None else self._fallback, self._trans_key
        )
