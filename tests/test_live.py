"""Tests for live flow updates pushed between polls."""

from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from sems_plus_client import LiveMessage

from homeassistant.core import HomeAssistant

from . import setup_integration
from .conftest import STATION_ID, FakeLiveFeed

GRID_POWER = "sensor.sems_plus_test_grid_power"
PV_POWER = "sensor.sems_plus_test_pv_power"


@pytest.mark.usefixtures("mock_client")
async def test_pushed_flow_updates_flow_sensors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, live_feed: FakeLiveFeed
) -> None:
    await setup_integration(hass, mock_config_entry)
    assert live_feed.stations == [STATION_ID]
    assert hass.states.get(GRID_POWER).state == "-0.4"

    live_feed.push(
        LiveMessage(
            "station",
            STATION_ID,
            {"time": "2026-01-01 12:00:05", "pSystem": "5.931", "pGrid": "-2.5"},
        )
    )
    await hass.async_block_till_done()

    # SEMS+ reports import as negative; the sensor shows it as positive.
    assert hass.states.get(GRID_POWER).state == "2.5"
    assert hass.states.get(PV_POWER).state == "5.931"
    # Fields the push left out keep their polled value.
    assert hass.states.get("sensor.sems_plus_test_load_power").state == "1.7"


async def test_newer_pushed_flow_survives_a_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    live_feed: FakeLiveFeed,
) -> None:
    """A poll returning older data does not undo a fresher push."""
    await setup_integration(hass, mock_config_entry)
    live_feed.push(
        LiveMessage(
            "station",
            STATION_ID,
            {"time": "2026-01-01 12:00:30", "pGrid": "-2.5"},
        )
    )
    coordinator = next(iter(mock_config_entry.runtime_data.coordinators.values()))

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(GRID_POWER).state == "2.5"


@pytest.mark.usefixtures("mock_client")
async def test_pushes_for_other_stations_are_ignored(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, live_feed: FakeLiveFeed
) -> None:
    await setup_integration(hass, mock_config_entry)

    live_feed.push(LiveMessage("station", "someone-else", {"pGrid": "-9"}))
    live_feed.push(LiveMessage("device", STATION_ID, {"pGrid": "-9"}))
    await hass.async_block_till_done()

    assert hass.states.get(GRID_POWER).state == "-0.4"
