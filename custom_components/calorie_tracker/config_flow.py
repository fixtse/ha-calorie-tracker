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
from .adaptive import bodymiscale_entities
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
            name = user_input.get("name", "").strip()
            if not 1 <= len(name) <= 100:
                errors["name"] = "invalid_name"
            elif token:
                error, token = await _check(token)
            if error:
                errors["base"] = error
            elif not errors:
                return self.async_create_entry(title=name,
                                              data={CONF_SESSION_TOKEN: token, "storage_key": f"{DOMAIN}.{uuid4().hex}"})
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({
                vol.Required("name"): selector.TextSelector(),
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
    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["general", "tdee"])

    async def async_step_general(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input.get(CONF_SYNC_TIME):
                user_input[CONF_SYNC_TIME] = user_input[CONF_SYNC_TIME][:5]
            return self.async_create_entry(data={**self.config_entry.options, **user_input})
        options = self.config_entry.options
        notifiers = sorted(f"notify.{name}" for name in self.hass.services.async_services_for_domain("notify")
                           if name not in ("notify", "send_message", "persistent_notification"))
        return self.async_show_form(step_id="general", data_schema=vol.Schema({
            vol.Optional(CONF_NOTIFY_SERVICE, description={"suggested_value": options.get(CONF_NOTIFY_SERVICE)}):
                selector.SelectSelector(selector.SelectSelectorConfig(options=notifiers, custom_value=True)),
            vol.Optional(CONF_NEAR_PERCENT, default=options.get(CONF_NEAR_PERCENT, DEFAULT_NEAR_PERCENT)):
                vol.All(vol.Coerce(int), vol.Range(min=50, max=100)),
            vol.Optional(CONF_SYNC_TIME, default=options.get(CONF_SYNC_TIME, DEFAULT_SYNC_TIME)):
                selector.TimeSelector(),
            vol.Optional(CONF_HISTORY_START, default=options.get(CONF_HISTORY_START, DEFAULT_HISTORY_START)):
                selector.DateSelector(),
        }))


    async def async_step_tdee(self, user_input=None):
        entry = self.config_entry
        tracker = getattr(entry, "runtime_data", None)
        options = tracker.adaptive.settings if tracker else entry.options
        errors = {}
        if user_input is not None:
            body_profile = user_input.get("tdee_bodymiscale_entry")
            if user_input.get("tdee_enabled") and not (body_profile or user_input.get("tdee_weight_entity")):
                errors["base"] = "missing_weight_source"
            elif body_profile and not all(bodymiscale_entities(self.hass, body_profile).get(key)
                                          for key in ("weight", "last_measurement_time")):
                errors["base"] = "missing_bodymiscale_user"
            else:
                # The new form owns only the TDEE options, including cleared optional sensors.
                saved = {key: value for key, value in entry.options.items() if not key.startswith("tdee_")}
                saved.update(user_input)
                if tracker:
                    await tracker.adaptive.controls({key: user_input.get(key, False if key != "tdee_weekly_goal" else 0)
                                                     for key in ("tdee_enabled", "tdee_auto_goals", "tdee_weekly_goal")})
                return self.async_create_entry(data=saved)
        profiles = [{"value": "", "label": "Use a weight sensor"}] + [
            {"value": item.entry_id, "label": item.title}
            for item in self.hass.config_entries.async_entries("bodymiscale")]
        schema = {
            vol.Optional("tdee_enabled", default=options.get("tdee_enabled", False)): bool,
            vol.Optional("tdee_auto_goals", default=options.get("tdee_auto_goals", False)): bool,
            vol.Optional("tdee_bodymiscale_entry", default=options.get("tdee_bodymiscale_entry", "")):
                selector.SelectSelector(selector.SelectSelectorConfig(options=profiles)),
            vol.Optional("tdee_weight_entity", description={"suggested_value": options.get("tdee_weight_entity")}):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="weight")),
            vol.Optional("tdee_body_fat_entity", description={"suggested_value": options.get("tdee_body_fat_entity")}):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional("tdee_measurement_entity", description={"suggested_value": options.get("tdee_measurement_entity")}):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="timestamp")),
            vol.Optional("tdee_initial_weight", description={"suggested_value": options.get("tdee_initial_weight")}):
                selector.NumberSelector(selector.NumberSelectorConfig(min=.1, max=1000, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="kg")),
            vol.Optional("tdee_initial_body_fat", description={"suggested_value": options.get("tdee_initial_body_fat")}):
                selector.NumberSelector(selector.NumberSelectorConfig(min=.1, max=99.9, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="%")),
            vol.Optional("tdee_start_date", description={"suggested_value": options.get("tdee_start_date")}):
                selector.DateSelector(),
            vol.Optional("tdee_weekly_goal", default=options.get("tdee_weekly_goal", 0)):
                selector.NumberSelector(selector.NumberSelectorConfig(min=-10, max=10, step=.01, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="kg/week")),
        }
        return self.async_show_form(step_id="tdee", data_schema=vol.Schema(schema), errors=errors)
