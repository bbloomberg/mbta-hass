"""Config and options flow for the MBTA integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import MbtaApiClient, MbtaApiError, MbtaAuthError
from .const import (
    CONF_API_KEY,
    CONF_MAX_DEPARTURES,
    CONF_ROUTE_ID,
    CONF_ROUTE_TYPE,
    CONF_SCAN_INTERVAL,
    CONF_STOP_ID,
    CONF_STOP_IDS,
    CONF_STOP_NAME,
    CONF_STOPS,
    DEFAULT_MAX_DEPARTURES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL_SECONDS,
    ROUTE_TYPE_NAMES,
)

_LOGGER = logging.getLogger(__name__)


def _route_type_options() -> list[SelectOptionDict]:
    return [
        SelectOptionDict(value=str(value), label=label)
        for value, label in ROUTE_TYPE_NAMES.items()
    ]


class MbtaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup: API key, then pick stops route-by-route."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._stops: list[dict[str, str]] = []
        self._route_type: int | None = None
        self._route_id: str | None = None
        self._stop_groups: dict[str, dict] = {}
        self._client: MbtaApiClient | None = None
        self._reconfigure_entry: ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MbtaOptionsFlow:
        return MbtaOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: collect the (optional) API key and validate it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._api_key = user_input.get(CONF_API_KEY) or None
            self._client = MbtaApiClient(
                async_get_clientsession(self.hass), self._api_key
            )
            try:
                await self._client.async_validate_key()
            except MbtaAuthError:
                errors["base"] = "invalid_auth"
            except MbtaApiError:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_route_type()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Optional(CONF_API_KEY): str}),
            errors=errors,
            description_placeholders={
                "signup_url": "https://api-v3.mbta.com/register"
            },
        )

    async def async_step_route_type(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: choose a transit mode."""
        if user_input is not None:
            self._route_type = int(user_input[CONF_ROUTE_TYPE])
            return await self.async_step_route()

        return self.async_show_form(
            step_id="route_type",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROUTE_TYPE): SelectSelector(
                        SelectSelectorConfig(
                            options=_route_type_options(),
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_route(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: choose a route within the selected mode."""
        assert self._client is not None and self._route_type is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            self._route_id = user_input[CONF_ROUTE_ID]
            return await self.async_step_stops()

        try:
            routes = await self._client.async_get_routes(self._route_type)
        except MbtaApiError:
            return self.async_abort(reason="cannot_connect")

        if not routes:
            return self.async_abort(reason="no_routes")

        options = [
            SelectOptionDict(value=r["id"], label=r["name"]) for r in routes
        ]
        return self.async_show_form(
            step_id="route",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROUTE_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_stops(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4: pick one or more stops on the route."""
        assert self._client is not None

        if user_input is not None:
            existing = {s[CONF_STOP_ID]: s for s in self._stops}
            for primary in user_input[CONF_STOPS]:
                group = self._stop_groups.get(primary)
                if not group:
                    continue
                entry: dict[str, Any] = {
                    CONF_STOP_ID: primary,
                    CONF_STOP_NAME: group["name"],
                }
                if len(group["ids"]) > 1:
                    entry[CONF_STOP_IDS] = group["ids"]
                if primary in existing:
                    # Re-selecting an existing stop upgrades it in place (e.g.
                    # to add the opposite direction of a bus stop).
                    existing[primary].update(entry)
                else:
                    self._stops.append(entry)
                    existing[primary] = entry
            if self._reconfigure_entry is not None:
                return await self.async_step_reconfigure_menu()
            return await self.async_step_finish_or_more()

        try:
            stops = await self._client.async_get_stops_for_route(self._route_id)
        except MbtaApiError:
            return self.async_abort(reason="cannot_connect")

        if not stops:
            return self.async_abort(reason="no_stops")

        # Group stops that share a name on this route. MBTA models each bus
        # direction as a separate stop id with no parent station, so the two
        # directions show up as two identically-named stops; bundling them lets
        # one selection track both directions in a single sensor.
        groups: dict[str, list[str]] = {}
        for s in stops:
            groups.setdefault(s["name"], []).append(s["id"])

        self._stop_groups = {}
        options: list[SelectOptionDict] = []
        for name, ids in groups.items():
            ids_sorted = sorted(ids)
            primary = ids_sorted[0]
            self._stop_groups[primary] = {"name": name, "ids": ids_sorted}
            label = name if len(ids_sorted) == 1 else f"{name} (both directions)"
            options.append(SelectOptionDict(value=primary, label=label))
        options.sort(key=lambda o: o["label"])

        return self.async_show_form(
            step_id="stops",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STOPS): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.LIST,
                            multiple=True,
                        )
                    )
                }
            ),
        )

    async def async_step_finish_or_more(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Loop: add another route's stops or finish."""
        if not self._stops:
            return await self.async_step_route_type()

        return self.async_show_menu(
            step_id="finish_or_more",
            menu_options=["route_type", "finish"],
            description_placeholders={"count": str(len(self._stops))},
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create (or, when reconfiguring, update) the config entry."""
        if not self._stops:
            return self.async_abort(reason="no_stops")

        if self._reconfigure_entry is not None:
            return self.async_update_reload_and_abort(
                self._reconfigure_entry,
                data={CONF_API_KEY: self._api_key, CONF_STOPS: self._stops},
            )

        await self.async_set_unique_id(
            "_".join(sorted(s[CONF_STOP_ID] for s in self._stops))
        )
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"MBTA ({len(self._stops)} stops)",
            data={
                CONF_API_KEY: self._api_key,
                CONF_STOPS: self._stops,
            },
        )

    # ------------------------------------------------------------------ #
    # Reconfigure: edit an existing entry in place
    # ------------------------------------------------------------------ #

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Entry point when the user clicks 'Reconfigure' on the integration."""
        entry = self._get_reconfigure_entry()
        self._reconfigure_entry = entry
        self._api_key = entry.data.get(CONF_API_KEY)
        self._client = MbtaApiClient(
            async_get_clientsession(self.hass), self._api_key
        )
        self._stops = [dict(s) for s in entry.data.get(CONF_STOPS, [])]
        return await self.async_step_reconfigure_menu()

    async def async_step_reconfigure_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose what to change: stops to keep, add a route, key, or finish."""
        return self.async_show_menu(
            step_id="reconfigure_menu",
            menu_options=["edit_stops", "route_type", "change_api_key", "finish"],
            description_placeholders={"count": str(len(self._stops))},
        )

    async def async_step_edit_stops(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove currently-tracked stops by unchecking them."""
        if not self._stops:
            return await self.async_step_route_type()

        if user_input is not None:
            keep = set(user_input[CONF_STOPS])
            self._stops = [s for s in self._stops if s[CONF_STOP_ID] in keep]
            return await self.async_step_reconfigure_menu()

        options = [
            SelectOptionDict(value=s[CONF_STOP_ID], label=s[CONF_STOP_NAME])
            for s in self._stops
        ]
        return self.async_show_form(
            step_id="edit_stops",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_STOPS,
                        default=[s[CONF_STOP_ID] for s in self._stops],
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.LIST,
                            multiple=True,
                        )
                    )
                }
            ),
        )

    async def async_step_change_api_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace the API key (validated before saving)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            key = user_input.get(CONF_API_KEY) or None
            client = MbtaApiClient(async_get_clientsession(self.hass), key)
            try:
                await client.async_validate_key()
            except MbtaAuthError:
                errors["base"] = "invalid_auth"
            except MbtaApiError:
                errors["base"] = "cannot_connect"
            else:
                self._api_key = key
                self._client = client
                return await self.async_step_reconfigure_menu()

        return self.async_show_form(
            step_id="change_api_key",
            data_schema=vol.Schema(
                {vol.Optional(CONF_API_KEY, default=self._api_key or ""): str}
            ),
            errors=errors,
        )


class MbtaOptionsFlow(OptionsFlow):
    """Tune polling interval and number of departures shown."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        current_interval = options.get(
            CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds())
        )
        current_max = options.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=current_interval
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL_SECONDS,
                            max=600,
                            step=5,
                            unit_of_measurement="seconds",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_MAX_DEPARTURES, default=current_max
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=20, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                }
            ),
        )
