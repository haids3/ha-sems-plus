"""Tests for the GoodWe SEMS+ control switches and numbers."""

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from sems_plus_client import SemsPlusApiError, SemsPlusCommandError

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import setup_integration
from .conftest import INVERTER_SN, STATION_ID

RUN_SWITCH = "switch.sems_plus_test_all_in_one_1_run"
CHARGING_SWITCH = "switch.sems_plus_test_bat1_immediate_charging"
END_SOC = "number.sems_plus_test_bat1_end_charge_soc"
CHARGE_POWER = "number.sems_plus_test_bat1_immediate_charge_power"
EXPORT_SWITCH = "switch.sems_plus_test_all_in_one_1_export_limit"
EXPORT_POWER = "number.sems_plus_test_all_in_one_1_export_limit_power"
RESTART_BUTTON = "button.sems_plus_test_all_in_one_1_restart"


async def test_no_controls_unless_allowed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Controls are opt-in per station and cost no requests until then."""
    await setup_integration(hass, mock_config_entry)

    assert not hass.states.async_all(SWITCH_DOMAIN)
    assert not hass.states.async_all(NUMBER_DOMAIN)
    mock_client.async_get_general_functions.assert_not_called()
    mock_client.async_get_function_values.assert_not_called()


@pytest.mark.parametrize("allow_control", [True])
async def test_no_controls_without_remote_permission(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """SEMS+ itself has not granted remote control, so none is offered."""
    mock_client.async_get_station_info.return_value = replace(
        mock_client.async_get_station_info.return_value,
        permissions=frozenset({"STATION_VIEW", "INVERTER_REMOTE_READ"}),
    )
    await setup_integration(hass, mock_config_entry)

    assert not hass.states.async_all(SWITCH_DOMAIN)
    assert not hass.states.async_all(NUMBER_DOMAIN)
    mock_client.async_get_general_functions.assert_not_called()
    mock_client.async_get_battery_functions.assert_not_called()


@pytest.mark.usefixtures("mock_client")
@pytest.mark.parametrize("allow_control", [True])
async def test_control_states(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(RUN_SWITCH).state == STATE_ON
    assert hass.states.get(CHARGING_SWITCH).state == STATE_OFF
    assert hass.states.get(END_SOC).state == "90.0"
    assert hass.states.get(CHARGE_POWER).state == "50.0"
    assert hass.states.get(EXPORT_SWITCH).state == STATE_ON
    export_power = hass.states.get(EXPORT_POWER)
    assert export_power.state == "5000.0"
    assert export_power.attributes["max"] == 10000
    assert hass.states.get(RESTART_BUTTON) is not None
    # The All-in-One starts and stops through run/stop, not commands.
    assert hass.states.get("button.sems_plus_test_all_in_one_1_shutdown") is None


@pytest.mark.parametrize("allow_control", [True])
@pytest.mark.parametrize(
    (
        "domain",
        "service",
        "data",
        "sn",
        "device_name",
        "address",
        "function_id",
        "value",
        "log",
    ),
    [
        pytest.param(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: RUN_SWITCH},
            INVERTER_SN,
            "All-in-One 1",
            "45218",
            "func-run-stop",
            0,
            {"run_stop": "remote_Switch_off"},
            id="stop-inverter",
        ),
        pytest.param(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: RUN_SWITCH},
            INVERTER_SN,
            "All-in-One 1",
            "45218",
            "func-run-stop",
            1,
            {"run_stop": "remote_Switch_on"},
            id="start-inverter",
        ),
        pytest.param(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: EXPORT_SWITCH},
            INVERTER_SN,
            "All-in-One 1",
            "47509",
            "func-export-limit",
            0,
            {"grid-tie_power_limit": "remote_Switch_off"},
            id="export-limit-off",
        ),
        pytest.param(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: EXPORT_POWER, ATTR_VALUE: 3000},
            INVERTER_SN,
            "All-in-One 1",
            "47510",
            "func-export-limit-power",
            3000,
            {"limit_setting": 3000},
            id="export-limit-power",
        ),
        pytest.param(
            BUTTON_DOMAIN,
            SERVICE_PRESS,
            {ATTR_ENTITY_ID: RESTART_BUTTON},
            INVERTER_SN,
            "All-in-One 1",
            "45221",
            "func-restart",
            361,
            {"restart": "restart"},
            id="restart",
        ),
        pytest.param(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: CHARGING_SWITCH},
            INVERTER_SN,
            "mppt1_battery",
            "47545",
            "func-immediate",
            1,
            {"immediate_charge": "on"},
            id="start-charging",
        ),
        pytest.param(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: CHARGING_SWITCH},
            INVERTER_SN,
            "mppt1_battery",
            "47545",
            "func-stop-charging",
            0,
            {"stop_charging": "remote_Switch_off"},
            id="stop-charging",
        ),
        pytest.param(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: END_SOC, ATTR_VALUE: 80},
            INVERTER_SN,
            "mppt1_battery",
            "47546",
            "func-end-soc",
            80,
            {"end_charge_soc": 80},
            id="end-soc",
        ),
        pytest.param(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: CHARGE_POWER, ATTR_VALUE: 40},
            INVERTER_SN,
            "mppt1_battery",
            "47603",
            "func-charge-power",
            40,
            {"bat_immediate_charge_power": 40},
            id="charge-power",
        ),
    ],
)
async def test_control_writes(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    domain: str,
    service: str,
    data: dict[str, Any],
    sn: str,
    device_name: str,
    address: str,
    function_id: str,
    value: int,
    log: dict[str, Any],
) -> None:
    """Each control writes its own function, found by discovery."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(domain, service, data, blocking=True)

    mock_client.async_set_function_values.assert_awaited_once_with(
        station_id=STATION_ID,
        sn=sn,
        device_name=device_name,
        values={address: value},
        functions={address: function_id},
        log=log,
    )


@pytest.mark.parametrize("allow_control", [True])
async def test_rejected_write_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_client.async_set_function_values.side_effect = SemsPlusApiError("X1", "no")

    with pytest.raises(HomeAssistantError, match="SEMS\\+ rejected the change"):
        await hass.services.async_call(
            SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: RUN_SWITCH}, blocking=True
        )


@pytest.mark.parametrize("allow_control", [True])
async def test_device_rejecting_a_write_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    mock_client.async_set_function_values.side_effect = SemsPlusCommandError(
        "P0215", "operation failed"
    )

    with pytest.raises(HomeAssistantError, match="did not accept the change"):
        await hass.services.async_call(
            SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: RUN_SWITCH}, blocking=True
        )


@pytest.mark.parametrize("allow_control", [True])
async def test_grid_tie_inverter_starts_and_stops_by_command(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A grid-tie inverter has start and shutdown commands instead of run/stop."""
    command = {"rwType": "RW", "control": 16, "range": "[0,0]", "gain": 1}
    mock_client.async_get_general_functions.return_value = {
        "functionMenus": {
            "children": [
                {
                    "translateKey": "device_start_stop",
                    "functions": [
                        {
                            **command,
                            "address": "40330",
                            "id": "func-start",
                            "translateKey": "start_up",
                        },
                        {
                            **command,
                            "address": "40331",
                            "id": "func-shutdown",
                            "translateKey": "shutdown",
                            "funcKey": "ShutDown",
                        },
                        {
                            "address": "40328",
                            "id": "func-limit-pct",
                            "translateKey": "limit_setting",
                            "rwType": "RW",
                            "control": 3,
                            "range": "[0,500]",
                            "gain": 1,
                            "unit": "%Pn",
                        },
                    ],
                }
            ]
        }
    }
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(RUN_SWITCH) is None
    # A limit outside the export-limit menu, in %, is not the W limit.
    assert hass.states.get(EXPORT_POWER) is None
    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: "button.sems_plus_test_all_in_one_1_shutdown"},
        blocking=True,
    )

    mock_client.async_set_function_values.assert_awaited_once_with(
        station_id=STATION_ID,
        sn=INVERTER_SN,
        device_name="All-in-One 1",
        values={"40331": 0},
        functions={"40331": "func-shutdown"},
        log={"shutdown": 0},
    )
    assert hass.states.get("button.sems_plus_test_all_in_one_1_start") is not None


@pytest.mark.parametrize("allow_control", [True])
async def test_read_only_run_stop_is_skipped(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    tree = mock_client.async_get_general_functions.return_value
    tree["functionMenus"]["children"][0]["functions"][0]["rwType"] = "RO"

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(RUN_SWITCH) is None
    assert hass.states.get(CHARGING_SWITCH) is not None
