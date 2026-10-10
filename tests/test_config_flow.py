"""Tests for the GoodWe SEMS+ config and station subentry flows."""

from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from sems_plus_client import SemsPlusAuthError, SemsPlusConnectionError, Station

from homeassistant.config_entries import SOURCE_USER, ConfigSubentry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.sems_plus.const import (
    CONF_ALLOW_CONTROL,
    CONF_SCAN_INTERVAL,
    CONF_STATION_ID,
    DOMAIN,
    SUBENTRY_STATION,
)

from . import setup_integration
from .conftest import STATION_ID, USERNAME

CREDENTIALS = {CONF_USERNAME: USERNAME, CONF_PASSWORD: "secret"}


@pytest.mark.usefixtures("mock_client")
async def test_user_flow_adds_chosen_stations(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    assert result["step_id"] == "stations"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"stations": [STATION_ID]}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == CREDENTIALS
    entry = result["result"]
    assert entry.unique_id == USERNAME
    (subentry,) = entry.subentries.values()
    assert subentry.title == "Test Station"
    assert subentry.unique_id == STATION_ID
    assert subentry.data == {
        CONF_STATION_ID: STATION_ID,
        CONF_SCAN_INTERVAL: 60,
        CONF_ALLOW_CONTROL: False,
    }


@pytest.mark.usefixtures("mock_client")
async def test_choosing_no_station_is_an_error(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"stations": []}
    )

    assert result["errors"] == {"base": "no_station_selected"}


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        pytest.param(SemsPlusAuthError("no"), "invalid_auth", id="auth"),
        pytest.param(SemsPlusConnectionError("down"), "cannot_connect", id="offline"),
    ],
)
async def test_user_flow_errors_recover(
    hass: HomeAssistant, mock_client: MagicMock, error: Exception, reason: str
) -> None:
    mock_client.async_login.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    assert result["errors"] == {"base": reason}

    mock_client.async_login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    assert result["step_id"] == "stations"


async def test_account_without_stations_aborts(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    mock_client.async_get_stations.return_value = []
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_stations"


@pytest.mark.usefixtures("mock_client")
async def test_account_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CREDENTIALS, CONF_USERNAME: USERNAME.upper()}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    result = await mock_config_entry.start_reauth_flow(hass)
    mock_client.async_login.side_effect = SemsPlusAuthError("no")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.async_login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-secret"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "new-secret"


@pytest.mark.usefixtures("mock_client")
async def test_add_station_subentry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, mock_config_entry)

    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, SUBENTRY_STATION),
        context={"source": SOURCE_USER},
    )
    # Only the station not yet added is offered.
    options = result["data_schema"].schema[CONF_STATION_ID].config["options"]
    assert [option["value"] for option in options] == ["station-2"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_STATION_ID: "station-2",
            CONF_SCAN_INTERVAL: 600,
            CONF_ALLOW_CONTROL: False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    added = next(
        s for s in mock_config_entry.subentries.values() if s.unique_id == "station-2"
    )
    assert added.title == "Customer Station"
    assert added.data[CONF_SCAN_INTERVAL] == 600


async def test_no_more_stations_to_add(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_client.async_get_stations.return_value = (
        mock_client.async_get_stations.return_value[:1]
    )

    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, SUBENTRY_STATION),
        context={"source": SOURCE_USER},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_more_stations"


@pytest.mark.usefixtures("mock_client")
async def test_reconfigure_station_enables_controls(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, mock_config_entry)
    subentry: ConfigSubentry = next(iter(mock_config_entry.subentries.values()))
    assert hass.states.get("button.sems_plus_test_all_in_one_1_start") is None

    result = await mock_config_entry.start_subentry_reconfigure_flow(
        hass, subentry.subentry_id
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 120, CONF_ALLOW_CONTROL: True}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.subentries[subentry.subentry_id].data[CONF_ALLOW_CONTROL]
    # The entry reloaded, so the controls now exist.
    assert hass.states.get("button.sems_plus_test_all_in_one_1_start") is not None


@pytest.mark.parametrize(
    ("name", "title"),
    [
        pytest.param("Jane Citizen", "Jane Citizen Station", id="suffixed"),
        pytest.param("Smith Station", "Smith Station", id="already-a-station"),
    ],
)
async def test_station_titles(
    hass: HomeAssistant, mock_client: MagicMock, name: str, title: str
) -> None:
    mock_client.async_get_stations.return_value = [
        Station(
            id=STATION_ID,
            name=name,
            status=1,
            capacity_kw=None,
            time_zone=None,
            is_shared=False,
        )
    ]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"stations": [STATION_ID]}
    )

    (subentry,) = result["result"].subentries.values()
    assert subentry.title == title


@pytest.mark.parametrize(
    ("station_count", "preselected"),
    [
        pytest.param(2, 2, id="all"),
        pytest.param(12, 8, id="first-eight"),
    ],
)
async def test_stations_start_selected(
    hass: HomeAssistant, mock_client: MagicMock, station_count: int, preselected: int
) -> None:
    """Stations start ticked, so most users only untick what they don't want."""
    mock_client.async_get_stations.return_value = [
        Station(
            id=f"station-{index:02}",
            name=f"Station {index:02}",
            status=1,
            capacity_kw=None,
            time_zone=None,
            is_shared=True,
        )
        for index in range(station_count)
    ]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )

    suggested = next(
        key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key == "stations"
    )
    assert suggested == [f"station-{index:02}" for index in range(preselected)]
    assert result["description_placeholders"] == {
        "count": str(station_count),
        "preselected": str(preselected),
    }
