"""Config flow for GoodWe SEMS+: one account, with stations as subentries."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from sems_plus_client import (
    SemsPlusAuthError,
    SemsPlusClient,
    SemsPlusError,
    Station,
)
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ALLOW_CONTROL,
    CONF_SCAN_INTERVAL,
    CONF_STATION_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    SUBENTRY_STATION,
)

_LOGGER = logging.getLogger(__name__)

CONF_STATIONS = "stations"
PRESELECTED_STATIONS = 8

_CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)
_SCAN_INTERVAL_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=MIN_SCAN_INTERVAL,
        max=3600,
        step=10,
        unit_of_measurement="s",
        mode=NumberSelectorMode.BOX,
    )
)


async def _async_list_stations(
    hass: HomeAssistant, username: str, password: str
) -> list[Station]:
    client = SemsPlusClient(async_get_clientsession(hass), username, password)
    await client.async_login()
    return await client.async_get_stations()


def _station_options(stations: list[Station]) -> list[SelectOptionDict]:
    return [
        SelectOptionDict(value=station.id, label=station.name)
        for station in sorted(stations, key=lambda s: s.name.casefold())
    ]


def _station_title(station: Station) -> str:
    """Title a station's subentry, e.g. "Jane Citizen Station"."""
    if station.name.casefold().endswith("station"):
        return station.name
    return f"{station.name} Station"


def _station_subentry(station: Station) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type=SUBENTRY_STATION,
        title=_station_title(station),
        unique_id=station.id,
        data={
            CONF_STATION_ID: station.id,
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            # Controls act on real hardware, possibly someone else's.
            CONF_ALLOW_CONTROL: False,
        },
    )


class SemsPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Log in once, then pick the stations to add."""

    VERSION = 1

    def __init__(self) -> None:
        self._username = ""
        self._password = ""
        self._stations: list[Station] = []

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_STATION: StationSubentryFlow}

    async def _async_check_credentials(
        self, user_input: dict[str, Any]
    ) -> dict[str, str]:
        """Log in and list stations; return form errors, if any."""
        try:
            self._stations = await _async_list_stations(
                self.hass, user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
        except SemsPlusAuthError:
            return {"base": "invalid_auth"}
        except SemsPlusError:
            _LOGGER.debug("SEMS+ login failed", exc_info=True)
            return {"base": "cannot_connect"}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].casefold())
            self._abort_if_unique_id_configured()
            if not (errors := await self._async_check_credentials(user_input)):
                if not self._stations:
                    return self.async_abort(reason="no_stations")
                self._username = user_input[CONF_USERNAME]
                self._password = user_input[CONF_PASSWORD]
                return await self.async_step_stations()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _CREDENTIALS_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_stations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose stations; an installer account may hold many that are not ours."""
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = set(user_input[CONF_STATIONS])
            if chosen:
                return self.async_create_entry(
                    title=self._username,
                    data={
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                    },
                    subentries=[
                        _station_subentry(station)
                        for station in self._stations
                        if station.id in chosen
                    ],
                )
            errors["base"] = "no_station_selected"
        options = _station_options(self._stations)
        schema = vol.Schema(
            {
                vol.Required(CONF_STATIONS): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        # Most accounts want every station, so they start ticked. An installer
        # account can hold hundreds; all of them sharing one request queue
        # would crawl, so only the first few are.
        preselected = [option["value"] for option in options[:PRESELECTED_STATIONS]]
        return self.async_show_form(
            step_id="stations",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or {CONF_STATIONS: preselected}
            ),
            description_placeholders={
                "count": str(len(options)),
                "preselected": str(len(preselected)),
            },
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            credentials = {
                CONF_USERNAME: entry.data[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            if not (errors := await self._async_check_credentials(credentials)):
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_PASSWORD): _CREDENTIALS_SCHEMA.schema[CONF_PASSWORD]}
            ),
            description_placeholders={CONF_USERNAME: entry.data[CONF_USERNAME]},
            errors=errors,
        )


class StationSubentryFlow(ConfigSubentryFlow):
    """Add a station to an account, or change how one is polled and controlled."""

    def __init__(self) -> None:
        self._stations: dict[str, Station] = {}

    async def _async_available_stations(self) -> list[Station]:
        entry = self._get_entry()
        if entry.state is ConfigEntryState.LOADED:
            # Reuse the running session; a fresh login can replace it.
            stations = await entry.runtime_data.client.async_get_stations()
        else:
            stations = await _async_list_stations(
                self.hass, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
            )
        added = {subentry.unique_id for subentry in entry.subentries.values()}
        return [station for station in stations if station.id not in added]

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            station = self._stations[user_input[CONF_STATION_ID]]
            return self.async_create_entry(
                title=_station_title(station),
                unique_id=station.id,
                data={
                    CONF_STATION_ID: station.id,
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_ALLOW_CONTROL: user_input[CONF_ALLOW_CONTROL],
                },
            )
        try:
            stations = await self._async_available_stations()
        except SemsPlusError:
            _LOGGER.debug("Listing SEMS+ stations failed", exc_info=True)
            return self.async_abort(reason="cannot_connect")
        if not stations:
            return self.async_abort(reason="no_more_stations")
        self._stations = {station.id: station for station in stations}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=_station_options(stations),
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                    ): _SCAN_INTERVAL_SELECTOR,
                    vol.Required(CONF_ALLOW_CONTROL, default=False): BooleanSelector(),
                }
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                data={
                    **subentry.data,
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_ALLOW_CONTROL: user_input[CONF_ALLOW_CONTROL],
                },
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=subentry.data.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): _SCAN_INTERVAL_SELECTOR,
                    vol.Required(
                        CONF_ALLOW_CONTROL,
                        default=subentry.data.get(CONF_ALLOW_CONTROL, False),
                    ): BooleanSelector(),
                }
            ),
            description_placeholders={"station": subentry.title},
        )
