"""Tests for setting up and unloading GoodWe SEMS+."""

from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from sems_plus_client import (
    SemsPlusAuthError,
    SemsPlusConnectionError,
    SemsPlusRateLimitError,
)

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant

from . import setup_integration


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
