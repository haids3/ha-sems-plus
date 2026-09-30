"""Fixtures for the GoodWe SEMS+ tests.

The API is mocked at the client, but every canned response is a synthetic
payload in `fixtures/` run through the client library's own parsers, so the
tests exercise the real response shapes.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from sems_plus_client import (
    Alarm,
    AlarmCounts,
    BatterySystem,
    Device,
    PowerFlow,
    Station,
    StationInfo,
    StationStatistics,
    parse_devices,
    parse_factors,
)
from syrupy.assertion import SnapshotAssertion

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from custom_components.sems_plus.const import (
    CONF_ALLOW_CONTROL,
    CONF_SCAN_INTERVAL,
    CONF_STATION_ID,
    DOMAIN,
    SUBENTRY_STATION,
)

pytest_plugins = "pytest_homeassistant_custom_component"

FIXTURES = Path(__file__).parent / "fixtures"
USERNAME = "user@example.com"
STATION_ID = "station-1"
INVERTER_SN = "GW0000SN000TEST1"
RACK_SN = "GW0000BAT00RACK1"
METER_SN = "VD3000GW0000SN000TEST1"
BATTERY_SYSTEM_SN = "VD2000GW0000SN000TEST1"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Read snapshots from tests/snapshots whichever plugin loads first.

    Syrupy and the Home Assistant test plugin both define `snapshot`; if
    syrupy's wins, it looks in __snapshots__ and finds nothing.
    """
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable the custom integration in every test."""


@pytest.fixture
def allow_control() -> bool:
    return False


@pytest.fixture
def mock_config_entry(allow_control: bool) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=USERNAME,
        unique_id=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: "secret"},
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_STATION,
                title="Test Station",
                unique_id=STATION_ID,
                data={
                    CONF_STATION_ID: STATION_ID,
                    CONF_SCAN_INTERVAL: 60,
                    CONF_ALLOW_CONTROL: allow_control,
                },
            )
        ],
    )


def _factors(sources: dict[str, str]) -> AsyncMock:
    parsed = {sn: parse_factors(load_fixture(name)) for sn, name in sources.items()}

    async def get(station_id: str, device: Device) -> dict[str, Any]:
        return parsed.get(device.sn, {})

    return AsyncMock(side_effect=get)


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """A SEMS+ client answering from the synthetic fixtures."""
    client = MagicMock()
    client.async_login = AsyncMock()
    client.async_get_stations = AsyncMock(
        return_value=[
            Station.from_api(row) for row in load_fixture("stations")["dataList"]
        ]
    )
    client.async_get_station_info = AsyncMock(
        return_value=StationInfo.from_api(load_fixture("station_info"))
    )
    client.async_get_power_flow = AsyncMock(
        return_value=PowerFlow.from_api(load_fixture("flow"))
    )
    client.async_get_devices = AsyncMock(
        return_value=parse_devices(load_fixture("devices"))
    )
    client.async_get_telemetry = _factors(
        {
            INVERTER_SN: "telemetry_aio",
            RACK_SN: "telemetry_rack",
            METER_SN: "telemetry_meter",
        }
    )
    client.async_get_counters = _factors(
        {
            INVERTER_SN: "counters_aio",
            RACK_SN: "counters_rack",
            METER_SN: "counters_meter",
        }
    )
    client.async_get_battery_systems = AsyncMock(
        return_value=[
            BatterySystem.from_api(row) for row in load_fixture("battery_systems")
        ]
    )

    years = load_fixture("statistics_year")

    async def statistics(
        station_id: str, dimension: str, start: date, end: date
    ) -> StationStatistics:
        if dimension == "day":
            return StationStatistics.from_api(load_fixture("statistics_day"))
        # One year per request, as the coordinator asks; unknown years are 0.
        values = years.get(str(start.year), {})
        return StationStatistics.from_api(
            {
                "dataList": [
                    {
                        "item": item,
                        "statisticsList": [{"date": str(start.year), "val": value}],
                    }
                    for item, value in values.items()
                ]
            }
        )

    client.async_get_statistics = AsyncMock(side_effect=statistics)
    client.async_get_alarm_counts = AsyncMock(
        return_value=AlarmCounts.from_api(load_fixture("alarm_counts"))
    )
    client.async_get_alarms = AsyncMock(
        return_value=[Alarm.from_api(row) for row in load_fixture("alarms")["dataList"]]
    )
    client.async_get_control_tree = AsyncMock(return_value=load_fixture("control_tree"))
    client.async_get_battery_functions = AsyncMock(
        return_value=load_fixture("battery_functions")
    )
    client.async_get_function_values = AsyncMock(
        return_value=load_fixture("function_values")
    )
    client.async_set_function_values = AsyncMock()

    with (
        patch("custom_components.sems_plus.SemsPlusClient", return_value=client),
        patch(
            "custom_components.sems_plus.config_flow.SemsPlusClient",
            return_value=client,
        ),
    ):
        yield client
