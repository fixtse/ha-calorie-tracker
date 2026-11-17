"""Calorie Tracker: food catalog and diary in Home Assistant, synced from MyFitnessPal."""

from __future__ import annotations

import asyncio
import logging
import datetime as dt
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_change, async_track_time_interval
from homeassistant.components import persistent_notification
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DAILY_GOAL, CONF_HISTORY_START, CONF_NEAR_PERCENT, CONF_NOTIFY_SERVICE, CONF_SESSION_TOKEN,
    CONF_SYNC_TIME, DEFAULT_HISTORY_START, DEFAULT_NEAR_PERCENT, DEFAULT_SYNC_TIME, DOMAIN, OVERLAP_DAYS,
    SIGNAL_UPDATED, STORAGE_VERSION,
)
from .goals import ALERT_INTERVAL, GOAL_KEYS, check_alerts, clean_goals, meal_targets, progress, week_budget
from .core import KINDS, Catalog, meal_key
from .mfp import AuthError, MyFitnessPal, sync_catalog

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BUTTON, Platform.DATE]
NUMBER = vol.All(vol.Coerce(float), vol.Range(min=0, max=100000))
DAY = cv.matches_regex(r"^\d{4}-\d{2}-\d{2}$")
MEAL = vol.All(cv.string, vol.Length(min=1, max=30))


class Tracker:
    """Runtime state for the single config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.data")
        self.catalog = Catalog()
        self.lock = asyncio.Lock()
        self.sync_task: asyncio.Task | None = None
        self.status: dict[str, Any] = {"state": "idle"}
        self.selected_day: dt.date | None = None  # dashboard date; None follows today

    def shown_day(self) -> str:
        return (self.selected_day or dt_util.now().date()).isoformat()

    @callback
    def select_day(self, day: dt.date | None) -> None:
        self.selected_day = None if day == dt_util.now().date() else day
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    async def load(self) -> None:
        self.catalog = Catalog.load(await self.store.async_load())
        self.status = self.catalog.sync.get("status", {"state": "idle"})
        if self.status.get("state") == "running":  # interrupted by a restart
            self.status = {**self.status, "state": "interrupted"}
        if "goals" not in self.catalog.sync and self.entry.options.get(CONF_DAILY_GOAL):
            self.catalog.sync["goals"] = {"calories": float(self.entry.options[CONF_DAILY_GOAL])}
        if self.status.get("state") == "auth_failed":
            # Loading follows a restart or a re-authentication; let the next check decide again.
            self.status = {**self.status, "state": "idle"}

    async def save(self) -> None:
        self.catalog.sync["status"] = self.status
        await self.store.async_save(self.catalog.dump())
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    # ---- goals and alerts ----------------------------------------------------------------
    @property
    def goals(self) -> dict[str, float]:
        return clean_goals(self.catalog.sync.get("goals"))

    @property
    def calorie_period(self) -> str:
        return self.catalog.sync.get("calorie_period", "week")

    def week(self, day: str | None = None) -> dict[str, Any] | None:
        """Weekly calorie budget (Monday-Sunday) for the given day, when calories are weekly."""
        goal = self.goals.get("calories")
        if not goal or self.calorie_period != "week":
            return None
        current = dt.date.fromisoformat(day or self.today())
        monday = current - timedelta(days=current.weekday())
        before = [(monday + timedelta(days=i)).isoformat() for i in range(current.weekday())]
        eaten_before = sum(d["calories"] for d in self.catalog.history(before)) if before else 0
        eaten_today = self.catalog.day(current.isoformat())["totals"]["calories"] or 0
        return week_budget(goal, eaten_before, eaten_today, 7 - current.weekday())

    def effective_goals(self, day: str | None = None) -> dict[str, float]:
        goals = dict(self.goals)
        if week := self.week(day):
            goals["calories"] = max(week["today_allowance"], 1)
        return goals

    def goal_status(self, day: str | None = None) -> dict[str, Any]:
        day = day or self.today()
        status = {"goals": self.goals, "calorie_period": self.calorie_period,
                  "recommend_since": self.catalog.sync.get("recommend_since"),
                  "progress": progress(self.effective_goals(day), self.catalog.day(day)["totals"])}
        if week := self.week(day):
            status["week"] = week
        return status

    def meal_targets(self, meal: str | None) -> dict[str, Any]:
        today = self.catalog.day(self.today())
        week = self.week()
        flex = {"calories": week["today_flex_max"]} if week else None
        return meal_targets(self.effective_goals(), today["totals"], set(today["meals"]), meal, flex)

    async def check_alerts(self, _now: Any = None) -> None:
        """Notify when a goal gets near or over; at most one notification per 30 minutes."""
        self._alert_timer = None
        service = self.entry.options.get(CONF_NOTIFY_SERVICE)
        if not service or not self.goals:
            return
        state, message = check_alerts(
            self.effective_goals(), self.catalog.day(self.today())["totals"], self.catalog.sync.get("alerts", {}),
            self.today(), dt_util.utcnow(), self.entry.options.get(CONF_NEAR_PERCENT, DEFAULT_NEAR_PERCENT),
            self.week())
        self.catalog.sync["alerts"] = state
        if message:
            domain, name = service.split(".", 1)
            try:
                await self.hass.services.async_call(domain, name, {
                    "title": "Calorie Tracker", "message": message,
                    "data": {"tag": DOMAIN, "group": DOMAIN}}, blocking=True)
            except Exception as error:
                _LOGGER.warning("Calorie Tracker notification failed: %s", error)
        elif state.get("pending") and not getattr(self, "_alert_timer", None):
            wait = ALERT_INTERVAL - (dt_util.utcnow() - datetime.fromisoformat(state["last_sent"]))
            self._alert_timer = async_call_later(self.hass, max(wait.total_seconds(), 1) + 1, self._deferred_alert)
        await self.store.async_save(self.catalog.dump())

    @callback
    def _deferred_alert(self, _now: Any) -> None:
        self.hass.async_create_task(self.check_alerts())

    def today(self) -> str:
        return dt_util.now().date().isoformat()

    # ---- MyFitnessPal sync ---------------------------------------------------------------
    def start_sync(self, since: dt.date | None = None) -> asyncio.Task:
        if self.sync_task and not self.sync_task.done():
            return self.sync_task
        self.sync_task = self.entry.async_create_background_task(
            self.hass, self._sync(since), f"{DOMAIN}_sync")
        # Failures are reported through the status sensor; avoid "exception never retrieved".
        self.sync_task.add_done_callback(lambda task: task.cancelled() or task.exception())
        return self.sync_task

    async def _sync(self, since: dt.date | None) -> dict[str, Any]:
        token = self.entry.data.get(CONF_SESSION_TOKEN, "")
        if not token:
            raise HomeAssistantError("Add your MyFitnessPal session cookie in the integration options")
        today = dt_util.now().date()
        scanned = self.catalog.sync.get("diary_scanned_through")
        if since:
            start = since
        elif scanned:
            start = dt.date.fromisoformat(scanned) - timedelta(days=OVERLAP_DAYS)
        else:
            start = dt.date.fromisoformat(self.entry.options.get(CONF_HISTORY_START, DEFAULT_HISTORY_START))
        self.status = {"state": "running", "started": dt_util.utcnow().isoformat(), "range": f"{start}..{today}"}
        await self.save()

        client: MyFitnessPal | None = None

        async def progress(message: str) -> None:
            self.status = {**self.status, "progress": message}
            if client:
                self._remember_token(client.session_token)
            await self.save()  # keeps the scan position and current cookie if Home Assistant restarts

        try:
            async with MyFitnessPal(token) as client:
                self._remember_token(client.session_token)
                try:
                    result = await sync_catalog(client, self.catalog, start, today, progress)
                finally:
                    self._remember_token(client.session_token)
        except AuthError as error:
            await self._auth_failed(error)
            raise HomeAssistantError("MyFitnessPal session expired; update the cookie in Home Assistant") from error
        except Exception as error:
            _LOGGER.exception("MyFitnessPal sync failed")
            self.status = {"state": "error", "error": str(error)[:300], "at": dt_util.utcnow().isoformat()}
            await self.save()
            raise HomeAssistantError(f"MyFitnessPal sync failed: {error}") from error
        await self.check_alerts()
        self.status = {"state": "ok", "at": dt_util.utcnow().isoformat(), "range": f"{start}..{today}",
                       "result": {k: v for k, v in result.items() if k != "new_food_names"}}
        await self.save()
        return result

    async def keepalive(self) -> None:
        """Use the session regularly so it never idles out, saving the rotated cookie."""
        token = self.entry.data.get(CONF_SESSION_TOKEN)
        if not token or self.status.get("state") == "auth_failed":
            return
        if self.sync_task and not self.sync_task.done():
            return  # the running sync already uses (and saves) the session
        try:
            async with MyFitnessPal(token) as client:
                self._remember_token(client.session_token)
            self.catalog.sync["session_checked"] = dt_util.utcnow().isoformat()
        except AuthError as error:
            await self._auth_failed(error)
        except Exception as error:  # network problems: try again next interval
            _LOGGER.warning("MyFitnessPal keepalive failed: %s", error)

    async def _auth_failed(self, error: Exception) -> None:
        self.status = {"state": "auth_failed", "error": str(error), "at": dt_util.utcnow().isoformat()}
        await self.save()
        self.entry.async_start_reauth(self.hass)
        persistent_notification.async_create(
            self.hass,
            "The MyFitnessPal session used by Calorie Tracker has ended. To reconnect:\n\n"
            "1. Open a **private window** and log in at myfitnesspal.com.\n"
            "2. Developer tools → Storage → Cookies → copy `__Secure-next-auth.session-token`.\n"
            "3. Close the private window **without logging out** (do not reuse this cookie in any browser).\n"
            "4. Settings → Devices & services → Calorie Tracker → **Re-authenticate**, paste it.",
            title="Calorie Tracker: reconnect MyFitnessPal",
            notification_id=f"{DOMAIN}_reauth",
        )

    @callback
    async def copy_day(self, source: str, target: str, meals: list[str] | None = None,
                       to_meal: str | None = None) -> list[dict[str, Any]]:
        """Copy entries between days; callers hold self.lock."""
        if source == target and not to_meal:
            raise ValueError("Pick a different day (or a different meal) to copy to")
        copies = self.catalog.copy_day(source, target, meals, to_meal)
        await self.save()
        await self.check_alerts()
        return copies

    async def copy_shown_day_to_today(self) -> None:
        async with self.lock:
            await self.copy_day(self.shown_day(), self.today())

    def _remember_token(self, token: str) -> None:
        if token and token != self.entry.data.get(CONF_SESSION_TOKEN):
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, CONF_SESSION_TOKEN: token})


def _tracker(hass: HomeAssistant) -> Tracker:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Calorie Tracker is not set up")
    return entries[0].runtime_data


def _message(error: Exception) -> str:
    return str(error).strip("'\"")


def _fail(call: ServiceCall, error: Exception) -> ServiceResponse:
    """Correctable errors go back in the response so API clients (Zero) can read and fix them."""
    if call.return_response:
        return {"error": _message(error)}
    raise ServiceValidationError(_message(error)) from error


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async def search(call: ServiceCall) -> ServiceResponse:
        t = _tracker(hass)
        return t.catalog.search(call.data.get("query", ""), call.data.get("kind"),
                                call.data["offset"], call.data["limit"])

    async def get_food(call: ServiceCall) -> ServiceResponse:
        try:
            return _tracker(hass).catalog.food(call.data["food_id"])
        except KeyError as error:
            return _fail(call, error)

    async def recommend(call: ServiceCall) -> ServiceResponse:
        t = _tracker(hass)
        targets = {k: call.data[k] for k in GOAL_KEYS if k in call.data}
        kind, query, limit = call.data.get("kind"), call.data.get("query", ""), call.data["limit"]
        # Only foods eaten since this date (e.g. available where the user lives now); "" disables.
        since = call.data["since"] if "since" in call.data else t.catalog.sync.get("recommend_since")
        since = since or None
        try:
            meal = meal_key(call.data["meal"]) if call.data.get("meal") else None
            if not targets:  # general suggestion: stay within this meal's share of today's goals
                basis = t.meal_targets(meal)
                flex = basis.pop("flex_limits", None)
                result = t.catalog.recommend_within(basis.pop("limits"), kind, query, limit, since=since)
                if not result["items"] and flex:
                    # Nothing fits today's calorie share: use the weekly slack instead.
                    result = t.catalog.recommend_within(flex, kind, query, limit, since=since)
                    week = t.week()
                    result["weekly_flex_used"] = True
                    result["notice"] += (f" Nothing fit today's calorie share, so these use the weekly budget "
                                         f"(week remaining {week['week_remaining']} kcal over {week['days_left']} days).")
                result["based_on_goals"] = basis
                if week := t.week():
                    result["week"] = week
                return result
            # Explicit targets: closest match, never beyond what's left of today's goals.
            remaining = {k: max(v["remaining"], 0) for k, v in t.goal_status()["progress"].items()}
            if week := t.week():  # explicit requests may use the weekly calorie slack
                remaining["calories"] = week["today_flex_max"]
            if not call.data.get("respect_goals", True):
                remaining = {}
            return t.catalog.recommend(targets, kind, query, limit, limits=remaining, since=since)
        except ValueError as error:
            return _fail(call, error)

    async def set_goals(call: ServiceCall) -> ServiceResponse:
        t = _tracker(hass)
        async with t.lock:
            goals = dict(t.catalog.sync.get("goals", {}))
            for key in GOAL_KEYS:
                if key in call.data:
                    goals[key] = call.data[key]  # 0 removes the goal
            if "calorie_period" in call.data:
                t.catalog.sync["calorie_period"] = call.data["calorie_period"]
            if "recommend_since" in call.data:
                t.catalog.sync["recommend_since"] = call.data["recommend_since"] or None
            t.catalog.sync["goals"] = clean_goals(goals)
            t.catalog.sync["alerts"] = {}  # re-evaluate against the new goals
            await t.save()
            await t.check_alerts()
        return t.goal_status()

    async def get_goals(call: ServiceCall) -> ServiceResponse:
        return _tracker(hass).goal_status()

    async def log_food(call: ServiceCall) -> ServiceResponse:
        t = _tracker(hass)
        eaten = dt_util.as_local(call.data.get("eaten_at") or dt_util.now())
        values = {k: call.data[k] for k in ("calories", "protein", "carbohydrates", "fat", "fiber", "sugar", "sodium")
                  if k in call.data}
        async with t.lock:
            try:
                entry = t.catalog.log(
                    day=eaten.date().isoformat(), eaten_at=eaten, meal=call.data["meal"],
                    food_id=call.data.get("food_id"), name=call.data.get("name"), values=values or None,
                    servings=call.data.get("servings"), quantity=call.data.get("quantity"),
                    unit=call.data.get("unit"))
            except (KeyError, ValueError) as error:
                return _fail(call, error)
            await t.save()
            await t.check_alerts()
        return {"entry": entry, "day_totals": t.catalog.day(entry["day"])["totals"],
                **({"goals": t.goal_status(entry["day"])["progress"]} if t.goals else {})}

    async def delete_entries(call: ServiceCall) -> ServiceResponse:
        t = _tracker(hass)
        async with t.lock:
            try:
                deleted = t.catalog.delete(call.data["entry_ids"])
            except KeyError as error:
                return _fail(call, error)
            await t.save()
        return {"deleted": deleted}

    async def update_entry(call: ServiceCall) -> ServiceResponse:
        t = _tracker(hass)
        values = {k: call.data[k] for k in ("calories", "protein", "carbohydrates", "fat", "fiber", "sugar", "sodium")
                  if k in call.data}
        eaten = call.data.get("eaten_at")
        async with t.lock:
            try:
                old_day = next((e["day"] for e in t.catalog.entries if e["id"] == call.data["entry_id"]), None)
                entry = t.catalog.update_entry(
                    call.data["entry_id"], meal=call.data.get("meal"), name=call.data.get("name"),
                    eaten_at=dt_util.as_local(eaten) if eaten else None, servings=call.data.get("servings"),
                    quantity=call.data.get("quantity"), unit=call.data.get("unit"), values=values or None)
            except (KeyError, ValueError) as error:
                return _fail(call, error)
            await t.save()
            await t.check_alerts()
        days = {d: t.catalog.day(d)["totals"] for d in {old_day, entry["day"]} if d}
        return {"entry": entry, "day_totals": days}

    async def copy_day(call: ServiceCall) -> ServiceResponse:
        t = _tracker(hass)
        target = call.data.get("to") or t.today()
        async with t.lock:
            try:
                copies = await t.copy_day(call.data["from"], target, call.data.get("meals"), call.data.get("to_meal"))
            except (KeyError, ValueError) as error:
                return _fail(call, error)
        return {"copied": len(copies), "day": target, "entries": [
            {"entry_id": e["id"], "meal": e["meal"], "name": e["name"], "portion": e["portion"],
             "calories": e["nutrients"]["calories"]} for e in copies], "day_totals": t.catalog.day(target)["totals"]}

    async def get_diary(call: ServiceCall) -> ServiceResponse:
        t = _tracker(hass)
        start = call.data.get("start") or t.today()
        end = call.data.get("end")
        if not end or end == start:
            day = t.catalog.day(start)
            if t.goals:
                day["goals"] = t.goal_status(start)["progress"]
            return day
        if end < start or (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days > 366:
            raise ServiceValidationError("end must be after start and within 366 days")
        return t.catalog.range(start, end)

    async def save_food(call: ServiceCall) -> ServiceResponse:
        t = _tracker(hass)
        values = {k: call.data[k] for k in ("calories", "protein", "carbohydrates", "fat", "fiber", "sugar", "sodium")
                  if k in call.data}
        async with t.lock:
            food = t.catalog.save_manual(call.data["name"], call.data.get("brand", ""),
                                         call.data["serving_amount"], call.data["serving_unit"], values,
                                         kind=call.data.get("kind", "food"),
                                         ingredients=call.data.get("ingredients"),
                                         instructions=call.data.get("instructions"),
                                         recipe_servings=call.data.get("recipe_servings", 1))
            await t.save()
        return {"food": t.catalog.brief(food)}

    async def sync(call: ServiceCall) -> ServiceResponse:
        t = _tracker(hass)
        since = dt.date.fromisoformat(call.data["since"]) if call.data.get("since") else None
        task = t.start_sync(since)
        if not call.return_response:
            return None
        return await task

    targets = {vol.Optional(k): NUMBER for k in ("calories", "protein", "carbohydrates", "fat")}
    nutrients = {**targets, **{vol.Optional(k): NUMBER for k in ("fiber", "sugar", "sodium")}}
    kind = {vol.Optional("kind"): vol.In(KINDS)}
    register = hass.services.async_register
    register(DOMAIN, "search_foods", search, vol.Schema({
        vol.Optional("query", default=""): cv.string, **kind,
        vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0)),
        vol.Optional("limit", default=10): vol.All(int, vol.Range(min=1, max=50)),
    }), supports_response=SupportsResponse.ONLY)
    register(DOMAIN, "get_food", get_food, vol.Schema({vol.Required("food_id"): cv.string}),
             supports_response=SupportsResponse.ONLY)
    register(DOMAIN, "recommend_foods", recommend, vol.Schema({
        **targets, **kind, vol.Optional("query", default=""): cv.string, vol.Optional("meal"): MEAL,
        vol.Optional("respect_goals", default=True): cv.boolean, vol.Optional("since"): vol.Any("", DAY),
        vol.Optional("limit", default=5): vol.All(int, vol.Range(min=1, max=20)),
    }), supports_response=SupportsResponse.ONLY)
    register(DOMAIN, "log_food", log_food, vol.Schema({
        vol.Required("meal"): MEAL, vol.Optional("food_id"): cv.string,
        vol.Optional("name"): vol.All(cv.string, vol.Length(min=1, max=200)), **nutrients,
        vol.Optional("servings"): vol.All(vol.Coerce(float), vol.Range(min=0.01, max=100)),
        vol.Optional("quantity"): vol.All(vol.Coerce(float), vol.Range(min=0.01, max=100000)),
        vol.Optional("unit"): vol.All(cv.string, vol.Length(max=50)),
        vol.Optional("eaten_at"): cv.datetime,
    }), supports_response=SupportsResponse.OPTIONAL)
    register(DOMAIN, "delete_entries", delete_entries, vol.Schema({
        vol.Required("entry_ids"): vol.All(cv.ensure_list, [cv.string], vol.Length(min=1, max=50)),
    }), supports_response=SupportsResponse.OPTIONAL)
    register(DOMAIN, "update_entry", update_entry, vol.Schema({
        vol.Required("entry_id"): cv.string, vol.Optional("meal"): MEAL,
        vol.Optional("name"): vol.All(cv.string, vol.Length(min=1, max=200)), **nutrients,
        vol.Optional("servings"): vol.All(vol.Coerce(float), vol.Range(min=0.01, max=100)),
        vol.Optional("quantity"): vol.All(vol.Coerce(float), vol.Range(min=0.01, max=100000)),
        vol.Optional("unit"): vol.All(cv.string, vol.Length(max=50)),
        vol.Optional("eaten_at"): cv.datetime,
    }), supports_response=SupportsResponse.OPTIONAL)
    register(DOMAIN, "copy_day", copy_day, vol.Schema({
        vol.Required("from"): DAY, vol.Optional("to"): DAY,
        vol.Optional("meals"): vol.All(cv.ensure_list, [MEAL]), vol.Optional("to_meal"): MEAL,
    }), supports_response=SupportsResponse.OPTIONAL)
    register(DOMAIN, "get_diary", get_diary, vol.Schema({
        vol.Optional("start"): DAY, vol.Optional("end"): DAY,
    }), supports_response=SupportsResponse.ONLY)
    register(DOMAIN, "save_food", save_food, vol.Schema({
        **kind,
        vol.Optional("ingredients"): vol.All([vol.All(cv.string, vol.Length(min=1, max=500))], vol.Length(max=100)),
        vol.Optional("instructions"): vol.All([vol.All(cv.string, vol.Length(min=1, max=2000))], vol.Length(max=100)),
        vol.Optional("recipe_servings"): vol.All(vol.Coerce(float), vol.Range(min=0.001, max=100000)),
        vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=200)),
        vol.Optional("brand", default=""): cv.string,
        vol.Optional("serving_amount", default=1): vol.All(vol.Coerce(float), vol.Range(min=0.001)),
        vol.Optional("serving_unit", default="serving"): vol.All(cv.string, vol.Length(min=1, max=50)),
        vol.Required("calories"): NUMBER, **{k: v for k, v in nutrients.items() if str(k) != "calories"},
    }), supports_response=SupportsResponse.OPTIONAL)
    register(DOMAIN, "set_goals", set_goals, vol.Schema({
        **targets, vol.Optional("calorie_period"): vol.In(["day", "week"]),
        vol.Optional("recommend_since"): vol.Any("", DAY),
    }), supports_response=SupportsResponse.OPTIONAL)
    register(DOMAIN, "get_goals", get_goals, vol.Schema({}), supports_response=SupportsResponse.ONLY)
    register(DOMAIN, "sync_myfitnesspal", sync, vol.Schema({vol.Optional("since"): DAY}),
             supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    tracker = Tracker(hass, entry)
    await tracker.load()
    entry.runtime_data = tracker
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    hour, minute = (int(x) for x in entry.options.get(CONF_SYNC_TIME, DEFAULT_SYNC_TIME).split(":"))

    @callback
    def daily_sync(now: datetime) -> None:
        if entry.data.get(CONF_SESSION_TOKEN) and tracker.status.get("state") != "auth_failed":
            tracker.start_sync()

    @callback
    def midnight(now: datetime) -> None:
        async_dispatcher_send(hass, SIGNAL_UPDATED)

    entry.async_on_unload(async_track_time_change(hass, daily_sync, hour=hour, minute=minute, second=0))
    entry.async_on_unload(async_track_time_change(hass, midnight, hour=0, minute=0, second=5))

    async def keepalive(now: datetime) -> None:
        await tracker.keepalive()

    entry.async_on_unload(async_track_time_interval(hass, keepalive, timedelta(hours=6)))
    persistent_notification.async_dismiss(hass, f"{DOMAIN}_reauth")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    task = entry.runtime_data.sync_task
    if task and not task.done():
        task.cancel()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
