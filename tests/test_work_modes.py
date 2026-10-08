"""Tests for the GoodWe SEMS+ work-mode and TOU entities."""

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from sems_plus_client import WorkModeInfo

from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.time import (
    ATTR_TIME,
    DOMAIN as TIME_DOMAIN,
    SERVICE_SET_VALUE as SERVICE_SET_TIME,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.sems_plus.const import DOMAIN

from . import setup_integration
from .conftest import INVERTER_SN, STATION_ID

PREFIX = "sems_plus_test_all_in_one_1"
WORK_MODE = f"sensor.{PREFIX}_work_mode"
TOU_MODE = f"switch.{PREFIX}_tou_mode"
BACKUP_MODE = f"switch.{PREFIX}_backup_mode"
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
ALL_MONTHS = list(range(12))
DAY_LOG = "sun、mon、tue、wed、thu、fri、sat"
MONTH_LOG = (
    "jan_1、feb_1、march_1、apr_1、may_1、june_1、"
    "july_1、aug_1、sept_1、oct_1、nov_1、dec_1"
)


@pytest.mark.usefixtures("mock_client")
@pytest.mark.parametrize("allow_control", [True])
async def test_work_mode_and_tou_states(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(WORK_MODE).state == "tou"
    assert hass.states.get(TOU_MODE).state == STATE_ON
    assert hass.states.get(BACKUP_MODE).state == STATE_OFF
    assert hass.states.get(f"switch.{PREFIX}_tou_slot_1").state == STATE_ON
    assert hass.states.get(f"time.{PREFIX}_tou_slot_1_start").state == "12:00:00"
    assert hass.states.get(f"time.{PREFIX}_tou_slot_1_end").state == "15:00:00"
    assert hass.states.get(f"number.{PREFIX}_tou_slot_1_power").state == "100.0"
    assert hass.states.get(f"number.{PREFIX}_tou_slot_1_cutoff_soc").state == "100"
    assert hass.states.get(f"select.{PREFIX}_tou_slot_1_mode").state == "charge"
    assert hass.states.get(f"select.{PREFIX}_tou_slot_1_discharge_limit").state == (
        "battery"
    )
    assert hass.states.get(f"switch.{PREFIX}_tou_slot_2").state == STATE_OFF
    assert hass.states.get(f"number.{PREFIX}_tou_slot_2_power").state == "32.0"
    assert hass.states.get(f"select.{PREFIX}_tou_slot_2_mode").state == "discharge"
    assert hass.states.get(f"select.{PREFIX}_tou_slot_2_discharge_limit").state == (
        "export"
    )
    # Version 3 has eight slots; the unused ones start disabled.
    unused = entity_registry.async_get(f"switch.{PREFIX}_tou_slot_8")
    assert unused is not None
    assert unused.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entity_registry.async_get(f"switch.{PREFIX}_tou_slot_9") is None


@pytest.mark.parametrize("allow_control", [True])
@pytest.mark.parametrize(
    ("domain", "service", "data", "name", "value", "log"),
    [
        pytest.param(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: TOU_MODE},
            "TOUModeEnable",
            {"TOUModeEnable": 0},
            {"TOU": "remote_Switch_off"},
            id="tou-mode-off",
        ),
        pytest.param(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: BACKUP_MODE},
            "Backup",
            {"BackupModeEnable": 1},
            {"backup_mode": "remote_Switch_on"},
            id="backup-mode-on",
        ),
        pytest.param(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: f"switch.{PREFIX}_tou_slot_2"},
            "TOU2",
            {
                "TOUStart2": "18:00",
                "TOUEnd2": "21:00",
                "TOUWeekEnable2": 249,
                "ChargeDischargePW2": 320,
                "ChargeCutOffSet2": 60,
                "TOUMonth2": [*ALL_MONTHS, 12],
                "TOUWeek2": ALL_DAYS,
            },
            {
                "start_t": "18:00",
                "end_t": "21:00",
                "switch": "on",
                "monthly_repetition": MONTH_LOG,
                "wkly_rep": DAY_LOG,
                "cd_mod": "discharge",
                "import_power_soc": 60,
                "discharge_limit_pw": 32,
            },
            id="slot-on",
        ),
        pytest.param(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: f"number.{PREFIX}_tou_slot_1_power", ATTR_VALUE: 50},
            "TOU1",
            {
                "TOUStart1": "12:00",
                "TOUEnd1": "15:00",
                "TOUWeekEnable1": 249,
                "ChargeDischargePW1": -500,
                "ChargeCutOffSet1": 100,
                "TOUMonth1": ALL_MONTHS,
                "TOUWeek1": ALL_DAYS,
            },
            {
                "start_t": "12:00",
                "end_t": "15:00",
                "switch": "on",
                "monthly_repetition": MONTH_LOG,
                "wkly_rep": DAY_LOG,
                "cd_mod": "charge",
                "import_power_soc": 100,
                "rated_power": 50,
            },
            id="slot-power",
        ),
        pytest.param(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: f"select.{PREFIX}_tou_slot_2_mode", ATTR_OPTION: "charge"},
            "TOU2",
            {
                "TOUStart2": "18:00",
                "TOUEnd2": "21:00",
                "TOUWeekEnable2": 6,
                "ChargeDischargePW2": -320,
                "ChargeCutOffSet2": 60,
                # A charge slot has no export limit.
                "TOUMonth2": ALL_MONTHS,
                "TOUWeek2": ALL_DAYS,
            },
            {
                "start_t": "18:00",
                "end_t": "21:00",
                "switch": "off",
                "monthly_repetition": MONTH_LOG,
                "wkly_rep": DAY_LOG,
                "cd_mod": "charge",
                "import_power_soc": 60,
                "rated_power": 32,
            },
            id="slot-to-charge",
        ),
        pytest.param(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {
                ATTR_ENTITY_ID: f"select.{PREFIX}_tou_slot_2_discharge_limit",
                ATTR_OPTION: "battery",
            },
            "TOU2",
            {
                "TOUStart2": "18:00",
                "TOUEnd2": "21:00",
                "TOUWeekEnable2": 6,
                "ChargeDischargePW2": 320,
                "ChargeCutOffSet2": 60,
                "TOUMonth2": ALL_MONTHS,
                "TOUWeek2": ALL_DAYS,
            },
            {
                "start_t": "18:00",
                "end_t": "21:00",
                "switch": "off",
                "monthly_repetition": MONTH_LOG,
                "wkly_rep": DAY_LOG,
                "cd_mod": "discharge",
                "import_power_soc": 60,
                "discharge_limit_pw": 32,
            },
            id="slot-limit-battery",
        ),
        pytest.param(
            TIME_DOMAIN,
            SERVICE_SET_TIME,
            {ATTR_ENTITY_ID: f"time.{PREFIX}_tou_slot_1_start", ATTR_TIME: "11:30"},
            "TOU1",
            {
                "TOUStart1": "11:30",
                "TOUEnd1": "15:00",
                "TOUWeekEnable1": 249,
                "ChargeDischargePW1": -1000,
                "ChargeCutOffSet1": 100,
                "TOUMonth1": ALL_MONTHS,
                "TOUWeek1": ALL_DAYS,
            },
            {
                "start_t": "11:30",
                "end_t": "15:00",
                "switch": "on",
                "monthly_repetition": MONTH_LOG,
                "wkly_rep": DAY_LOG,
                "cd_mod": "charge",
                "import_power_soc": 100,
                "rated_power": 100,
            },
            id="slot-start",
        ),
    ],
)
async def test_setting_writes(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    domain: str,
    service: str,
    data: dict[str, Any],
    name: str,
    value: dict[str, Any],
    log: dict[str, Any],
) -> None:
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(domain, service, data, blocking=True)

    mock_client.async_remote_set.assert_awaited_once_with(
        station_id=STATION_ID,
        sn=INVERTER_SN,
        device_name="All-in-One 1",
        name=name,
        data=value,
        log=log,
    )


@pytest.mark.parametrize("allow_control", [True])
async def test_enabling_an_unscheduled_slot_runs_it_every_day(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A slot with no days or months would never apply, so both are filled in."""
    original = mock_client.async_remote_get.side_effect

    async def remote_get(sn: str, names: list[str]) -> dict[str, Any]:
        values = await original(sn, names)
        values["TOU2"] = {**values["TOU2"], "TOUWeek2": [], "TOUMonth2": []}
        return values

    mock_client.async_remote_get.side_effect = remote_get
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: f"switch.{PREFIX}_tou_slot_2"},
        blocking=True,
    )

    written = mock_client.async_remote_set.await_args.kwargs["data"]
    assert written["TOUWeek2"] == ALL_DAYS
    assert written["TOUMonth2"] == ALL_MONTHS


@pytest.mark.parametrize("allow_control", [True])
async def test_no_settings_for_work_mode_version_1(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Version 1 has a single exclusive mode, which is not supported."""
    mock_client.async_get_work_mode.return_value = WorkModeInfo("1.0", None)
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(WORK_MODE) is None
    assert hass.states.get(TOU_MODE) is None
    assert not hass.states.async_all(TIME_DOMAIN)
    mock_client.async_remote_get.assert_not_called()


@pytest.mark.parametrize("allow_control", [True])
@pytest.mark.parametrize(
    ("domain", "service", "data", "error"),
    [
        pytest.param(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: f"number.{PREFIX}_tou_slot_2_power", ATTR_VALUE: 0},
            "needs a power above 0",
            id="discharge-at-zero",
        ),
        pytest.param(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {
                ATTR_ENTITY_ID: f"select.{PREFIX}_tou_slot_1_discharge_limit",
                ATTR_OPTION: "export",
            },
            "Only a discharge slot",
            id="limit-on-charge-slot",
        ),
    ],
)
async def test_invalid_slot_changes_raise(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    domain: str,
    service: str,
    data: dict[str, Any],
    error: str,
) -> None:
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError, match=error):
        await hass.services.async_call(domain, service, data, blocking=True)
    mock_client.async_remote_set.assert_not_called()


@pytest.mark.usefixtures("mock_client")
async def test_settings_are_read_only_without_controls(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """With controls off, the work mode and TOU schedule still show."""
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(WORK_MODE).state == "tou"
    assert hass.states.get(WORK_MODE).attributes["tou_mode"] is True
    slot_1 = hass.states.get(f"sensor.{PREFIX}_tou_slot_1")
    assert slot_1.state == "charge"
    assert slot_1.attributes["power"] == 100
    assert slot_1.attributes["power_limit"] is None
    slot_2 = hass.states.get(f"sensor.{PREFIX}_tou_slot_2")
    assert slot_2.state == "off"
    assert slot_2.attributes["mode"] == "discharge"
    assert slot_2.attributes["power_limit"] == "export"
    assert slot_2.attributes["months"] == list(range(1, 13))
    for domain in (SWITCH_DOMAIN, NUMBER_DOMAIN, SELECT_DOMAIN, TIME_DOMAIN):
        assert not hass.states.async_all(domain), domain


@pytest.mark.usefixtures("mock_client")
async def test_no_settings_without_read_permission(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_client.async_get_station_info.return_value = replace(
        mock_client.async_get_station_info.return_value,
        permissions=frozenset({"STATION_VIEW"}),
    )
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(WORK_MODE) is None
    mock_client.async_remote_get.assert_not_called()


@pytest.mark.usefixtures("mock_client")
@pytest.mark.parametrize("allow_control", [True])
async def test_each_tou_slot_is_a_device_under_the_inverter(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A slot's controls share their own device, so they group together."""
    await setup_integration(hass, mock_config_entry)

    inverter = device_registry.async_get_device(identifiers={(DOMAIN, INVERTER_SN)})
    slot = device_registry.async_get_device(
        identifiers={(DOMAIN, f"{INVERTER_SN}-tou_slot_1")}
    )
    assert slot.name == "All-in-One 1 TOU slot 1"
    assert slot.via_device_id == inverter.id
    slot_entities = {
        entry.entity_id
        for entry in er.async_entries_for_device(entity_registry, slot.id)
    }
    assert slot_entities == {
        f"switch.{PREFIX}_tou_slot_1",
        f"time.{PREFIX}_tou_slot_1_start",
        f"time.{PREFIX}_tou_slot_1_end",
        f"number.{PREFIX}_tou_slot_1_power",
        f"number.{PREFIX}_tou_slot_1_cutoff_soc",
        f"select.{PREFIX}_tou_slot_1_mode",
        f"select.{PREFIX}_tou_slot_1_discharge_limit",
        f"sensor.{PREFIX}_tou_slot_1",
    }
    assert hass.states.get(f"switch.{PREFIX}_tou_slot_1").name == (
        "All-in-One 1 TOU slot 1"
    )
    assert hass.states.get(f"number.{PREFIX}_tou_slot_1_power").name == (
        "All-in-One 1 TOU slot 1 Power"
    )
