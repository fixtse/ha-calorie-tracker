"""Config flow: paste the MyFitnessPal session cookie; options for goal and sync schedule."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_HISTORY_START, CONF_NEAR_PERCENT, CONF_NOTIFY_SERVICE, CONF_SESSION_TOKEN, CONF_SYNC_TIME,
    DEFAULT_HISTORY_START, DEFAULT_NEAR_PERCENT, DEFAULT_SYNC_TIME, DOMAIN,
)
from .mfp import AuthError, MyFitnessPal

TOKEN = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))


async def _check(token: str) -> tuple[str | None, str]:
    """Return (error key or None, cookie to store).

    Validating can rotate the cookie; the old value must never be used again or MyFitnessPal
    revokes the session, so the rotated value is what gets stored.
    """
    try:
        async with MyFitnessPal(token) as client:
            return None, client.session_token
    except AuthError:
        return "invalid_auth", token
    except Exception:  # network, Cloudflare, site changes
        return "cannot_connect", token


class CalorieTrackerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input.get(CONF_SESSION_TOKEN, "").strip()
            error = None
            if token:
                error, token = await _check(token)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(title=user_input["name"].strip(),
                                              data={CONF_SESSION_TOKEN: token, "storage_key": f"{DOMAIN}.{uuid4().hex}"})
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({
                vol.Required("name"): vol.All(str, str.strip, vol.Length(min=1, max=100)),
                vol.Optional(CONF_SESSION_TOKEN): TOKEN}), errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            error, token = await _check(user_input[CONF_SESSION_TOKEN].strip())
            if not error:
                entry = self._get_reauth_entry()
                return self.async_update_reload_and_abort(entry, data={**entry.data, CONF_SESSION_TOKEN: token})
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_SESSION_TOKEN): TOKEN}),
            errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            error, token = await _check(user_input[CONF_SESSION_TOKEN].strip())
            if not error:
                entry = self._get_reconfigure_entry()
                return self.async_update_reload_and_abort(entry, data={**entry.data, CONF_SESSION_TOKEN: token})
            errors["base"] = error
        return self.async_show_form(
            step_id="reconfigure", data_schema=vol.Schema({vol.Required(CONF_SESSION_TOKEN): TOKEN}),
            errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowWithReload:
        return CalorieTrackerOptionsFlow()


class CalorieTrackerOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input.get(CONF_SYNC_TIME):
                user_input[CONF_SYNC_TIME] = user_input[CONF_SYNC_TIME][:5]
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        notifiers = sorted(f"notify.{name}" for name in self.hass.services.async_services_for_domain("notify")
                           if name not in ("notify", "send_message", "persistent_notification"))
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Optional(CONF_NOTIFY_SERVICE, description={"suggested_value": options.get(CONF_NOTIFY_SERVICE)}):
                selector.SelectSelector(selector.SelectSelectorConfig(options=notifiers, custom_value=True)),
            vol.Optional(CONF_NEAR_PERCENT, default=options.get(CONF_NEAR_PERCENT, DEFAULT_NEAR_PERCENT)):
                vol.All(vol.Coerce(int), vol.Range(min=50, max=100)),
            vol.Optional(CONF_SYNC_TIME, default=options.get(CONF_SYNC_TIME, DEFAULT_SYNC_TIME)):
                selector.TimeSelector(),
            vol.Optional(CONF_HISTORY_START, default=options.get(CONF_HISTORY_START, DEFAULT_HISTORY_START)):
                selector.DateSelector(),
        }))
