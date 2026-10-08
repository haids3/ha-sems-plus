"""Snapshot tests for the GoodWe SEMS+ read-only entities."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from sems_plus_client import ForceUpgradeStatus, SemsPlusApiError
from syrupy.assertion import SnapshotAssertion

from homeassistant.const import STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from . import setup_integration


@pytest.mark.usefixtures("mock_client", "entity_registry_enabled_by_default")
@pytest.mark.parametrize("platform", [Platform.SENSOR, Platform.BINARY_SENSOR])
async def test_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    platform: Platform,
) -> None:
    """Every entity of the synthetic station, with its state."""
    with patch("custom_components.sems_plus.PLATFORMS", [platform]):
        await setup_integration(hass, mock_config_entry)

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


async def test_offline_device_entities_are_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """A device that goes offline keeps its entities, marked unavailable."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get("sensor.sems_plus_test_battery_rack_1_soc").state == "55.0"

    devices = mock_client.async_get_devices.return_value
    rack = next(d for d in devices if d.name == "Battery Rack 1")
    devices[devices.index(rack)] = type(rack)(
        sn=rack.sn, name=rack.name, device_type=rack.device_type, status=0
    )
    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert (
        hass.states.get("sensor.sems_plus_test_battery_rack_1_soc").state
        == "unavailable"
    )


async def test_offline_devices_are_not_polled(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Offline devices and dongles cost no telemetry requests."""
    await setup_integration(hass, mock_config_entry)

    polled = {
        call.args[1].name for call in mock_client.async_get_telemetry.call_args_list
    }
    assert polled == {"All-in-One 1", "Battery Rack 1", "Meter 1"}


async def test_station_import_export_without_a_meter(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Without a meter, only the station reports import and export."""
    mock_client.async_get_devices.return_value = [
        device
        for device in mock_client.async_get_devices.return_value
        if device.name != "Meter 1"
    ]

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get("sensor.sems_plus_test_import_today").state == "3.3"
    assert hass.states.get("sensor.sems_plus_test_export_total").state == "2500.0"
    # The All-in-One still reports production, so the station does not.
    assert hass.states.get("sensor.sems_plus_test_production_today") is None


async def test_lifetime_totals_are_summed_per_year(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Each year since the station was created is its own request."""
    await setup_integration(hass, mock_config_entry)

    years = sorted(
        call.args[2].year
        for call in mock_client.async_get_statistics.call_args_list
        if call.args[1] == "year"
    )
    assert years == list(range(2025, dt_util.now().year + 1))
    assert all(
        call.args[2].year == call.args[3].year
        for call in mock_client.async_get_statistics.call_args_list
    )
    assert hass.states.get("sensor.sems_plus_test_consumption_total").state == "6000.0"


async def test_lifetime_totals_hold_when_a_year_fails(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """A partial sum would make the total drop, so the last full one is kept."""
    await setup_integration(hass, mock_config_entry)
    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))
    coordinator._years.clear()
    mock_client.async_get_statistics.side_effect = SemsPlusApiError("X", "no")

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.sems_plus_test_consumption_total").state == "6000.0"


@pytest.mark.parametrize(
    ("flow_items", "present", "absent"),
    [
        pytest.param(
            "pSystem,pConsum,pGrid",
            ["sensor.sems_plus_test_grid_power"],
            [
                "sensor.sems_plus_test_battery_power",
                "sensor.sems_plus_test_third_party_pv_power",
            ],
            id="pv-only",
        ),
        pytest.param(
            "pSystem,soc,pBat,pConsum,pThird,pGrid",
            [
                "sensor.sems_plus_test_battery_power",
                "sensor.sems_plus_test_third_party_pv_power",
            ],
            ["sensor.sems_plus_test_ev_charger_power"],
            id="third-party-pv",
        ),
    ],
)
async def test_flow_sensors_follow_the_station_flow_items(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    flow_items: str,
    present: list[str],
    absent: list[str],
) -> None:
    """Live flow sensors exist only for the flows SEMS+ lists for the station."""
    mock_client.async_get_station_info.return_value = replace(
        mock_client.async_get_station_info.return_value,
        flow_items=frozenset(flow_items.split(",")),
    )
    await setup_integration(hass, mock_config_entry)

    for entity_id in present:
        assert hass.states.get(entity_id) is not None, entity_id
    for entity_id in absent:
        assert hass.states.get(entity_id) is None, entity_id


async def test_firmware_update_details_and_polling(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Pending firmware is listed, and checked hourly rather than every poll."""
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get("binary_sensor.sems_plus_test_all_in_one_1_firmware_update")
    assert state.state == STATE_ON
    assert state.attributes["updates"] == [
        {
            "component": "DCDC",
            "version": "08",
            "name": "DCDC 08 test release",
            "released": "2026-08-20T01:49:13.837000+00:00",
        }
    ]
    calls = mock_client.async_get_firmware_updates.await_count
    assert calls == 2  # the All-in-One and the dongle

    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_client.async_get_firmware_updates.await_count == calls

    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_client.async_get_firmware_updates.await_count == calls * 2


@pytest.mark.parametrize(
    ("permissions", "owner_can_apply", "can_apply"),
    [
        pytest.param({"FIRMWARE_UPGRADE"}, False, True, id="installer"),
        pytest.param(set(), True, True, id="owner-forced"),
        pytest.param(set(), False, False, id="neither"),
    ],
)
async def test_firmware_update_says_who_can_apply(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    permissions: set[str],
    owner_can_apply: bool,
    can_apply: bool,
) -> None:
    mock_client.async_get_station_info.return_value = replace(
        mock_client.async_get_station_info.return_value,
        permissions=frozenset(permissions),
    )
    mock_client.async_get_force_upgrade.return_value = ForceUpgradeStatus(
        forced=owner_can_apply, upgrading=False, owner_can_apply=owner_can_apply
    )
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get("binary_sensor.sems_plus_test_all_in_one_1_firmware_update")
    assert state.attributes["can_apply"] is can_apply
    assert state.attributes["forced"] is owner_can_apply
