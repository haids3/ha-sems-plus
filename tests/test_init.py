"""Tests for setting up and unloading GoodWe SEMS+."""

from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from sems_plus_client import (
    SemsPlusApiError,
    SemsPlusAuthError,
    SemsPlusConnectionError,
    SemsPlusRateLimitError,
)

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.sems_plus.const import DEFAULT_SCAN_INTERVAL, DOMAIN

from . import setup_integration
from .conftest import INVERTER_SN, METER_SN, RACK_SN


async def test_setup_and_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("error", "state"),
    [
        pytest.param(SemsPlusAuthError("no"), ConfigEntryState.SETUP_ERROR, id="auth"),
        pytest.param(
            SemsPlusConnectionError("down"), ConfigEntryState.SETUP_RETRY, id="offline"
        ),
    ],
)
async def test_login_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    error: Exception,
    state: ConfigEntryState,
) -> None:
    mock_client.async_login.side_effect = error

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is state


async def test_rejected_session_starts_reauth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_client.async_get_devices.side_effect = SemsPlusAuthError("no")

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    (flow,) = hass.config_entries.flow.async_progress()
    assert flow["context"]["source"] == SOURCE_REAUTH


async def test_failing_station_does_not_block_setup(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A station that cannot be read loads without entities and retries later."""
    mock_client.async_get_devices.side_effect = SemsPlusRateLimitError(60)

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert not hass.states.async_all("sensor")


@pytest.mark.parametrize(
    ("sn", "model_id", "sw_version"),
    [
        pytest.param(INVERTER_SN, "GW10K-EHA-G20", "010101", id="all-in-one"),
        pytest.param(RACK_SN, "GW8.3-BAT-D-G20", "06.00", id="battery-rack"),
        pytest.param("GW0000DONGLE0001", None, "V2.7.64", id="dongle"),
        pytest.param(METER_SN, None, None, id="meter"),
    ],
)
@pytest.mark.usefixtures("mock_client")
async def test_device_models_and_firmware(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    sn: str,
    model_id: str | None,
    sw_version: str | None,
) -> None:
    await setup_integration(hass, mock_config_entry)

    device = device_registry.async_get_device(identifiers={(DOMAIN, sn)})
    assert device is not None
    assert (device.model_id, device.sw_version) == (model_id, sw_version)


async def test_device_models_arrive_later(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Details that failed at setup are filled in once a later poll gets them."""
    details = mock_client.async_get_device_details.return_value
    mock_client.async_get_device_details.side_effect = SemsPlusApiError("X1", "no")
    await setup_integration(hass, mock_config_entry)
    device = device_registry.async_get_device(identifiers={(DOMAIN, RACK_SN)})
    assert device.model_id is None

    mock_client.async_get_device_details.side_effect = None
    mock_client.async_get_device_details.return_value = details
    freezer.tick(DEFAULT_SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    device = device_registry.async_get_device(identifiers={(DOMAIN, RACK_SN)})
    assert device.model_id == "GW8.3-BAT-D-G20"
