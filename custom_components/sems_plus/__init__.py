"""The GoodWe SEMS+ integration."""

from __future__ import annotations

from dataclasses import dataclass

from sems_plus_client import (
    LiveMessage,
    SemsPlusAuthError,
    SemsPlusClient,
    SemsPlusError,
    SemsPlusLiveFeed,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.ssl import get_default_context

from .const import DOMAIN, SUBENTRY_STATION
from .coordinator import SemsPlusStationCoordinator
from .entity import tou_slot_identifier, work_mode_identifier

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]


@dataclass(slots=True)
class SemsPlusRuntimeData:
    client: SemsPlusClient
    # Keyed by station subentry id.
    coordinators: dict[str, SemsPlusStationCoordinator]


type SemsPlusConfigEntry = ConfigEntry[SemsPlusRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: SemsPlusConfigEntry) -> bool:
    """Set up one SEMS+ account and each station chosen under it."""
    client = SemsPlusClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    try:
        await client.async_login()
    except SemsPlusAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SemsPlusError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinators = {
        subentry_id: SemsPlusStationCoordinator(hass, entry, subentry, client)
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_STATION
    }
    # One failing station must not keep the others from loading, so this is a
    # plain refresh rather than a first refresh that raises. The client runs
    # the requests one after another either way.
    for coordinator in coordinators.values():
        await coordinator.async_refresh()
        if isinstance(coordinator.last_exception, ConfigEntryAuthFailed):
            raise coordinator.last_exception

    entry.runtime_data = SemsPlusRuntimeData(client, coordinators)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _async_start_live_feed(hass, entry)
    _async_remove_empty_tou_slot_devices(hass, entry)
    _async_remove_run_switches(hass, entry)
    return True


@callback
def _async_remove_run_switches(hass: HomeAssistant, entry: SemsPlusConfigEntry) -> None:
    """Remove the run switches that Start and Stop buttons replaced."""
    entity_registry = er.async_get(hass)
    for coordinator in entry.runtime_data.coordinators.values():
        for sn in coordinator.data.devices if coordinator.data else ():
            if entity_id := entity_registry.async_get_entity_id(
                "switch", DOMAIN, f"{sn}-run_stop"
            ):
                entity_registry.async_remove(entity_id)


@callback
def _async_remove_empty_tou_slot_devices(
    hass: HomeAssistant, entry: SemsPlusConfigEntry
) -> None:
    """Remove the per-slot devices TOU slots had before moving to work mode.

    Only devices left without entities go, so a device whose entities have
    not moved yet keeps them (and their settings) until the next start.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    old = {
        tou_slot_identifier(sn, index)
        for coordinator in entry.runtime_data.coordinators.values()
        if coordinator.data is not None
        for sn in coordinator.data.devices
        for index in range(1, 13)
    }
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if any(
            domain == DOMAIN and identifier in old
            for domain, identifier in device.identifiers
        ) and not er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities=True
        ):
            device_registry.async_remove_device(device.id)


@callback
def _async_start_live_feed(hass: HomeAssistant, entry: SemsPlusConfigEntry) -> None:
    """Push live power flow to each station between polls.

    One MQTT connection serves the whole account. Polling carries on as
    before, so losing the feed only makes the flow sensors less current.
    """
    stations = {c.station_id: c for c in entry.runtime_data.coordinators.values()}

    @callback
    def _on_message(message: LiveMessage) -> None:
        if message.kind == "station" and (coordinator := stations.get(message.key)):
            coordinator.async_handle_live_flow(message.data)

    feed = SemsPlusLiveFeed(entry.runtime_data.client, get_default_context())
    entry.async_create_background_task(
        hass, feed.async_run(stations, [], _on_message), f"{DOMAIN} live feed"
    )


async def _async_reload(hass: HomeAssistant, entry: SemsPlusConfigEntry) -> None:
    """Reload when stations are added, removed or reconfigured."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SemsPlusConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: SemsPlusConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing a device that SEMS+ no longer reports."""
    for coordinator in entry.runtime_data.coordinators.values():
        data = coordinator.data
        if data is None:
            # Unknown state; keep the device rather than guess.
            return False
        known = {
            coordinator.station_id,
            *data.devices,
            *data.battery_systems,
            *(work_mode_identifier(sn) for sn in data.settings),
        }
        if any(
            identifier in known
            for domain, identifier in device.identifiers
            if domain == DOMAIN
        ):
            return False
    return True
