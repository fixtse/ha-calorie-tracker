"""Today's nutrition totals and the MyFitnessPal sync status."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from datetime import date, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_UPDATED

TOTALS = (
    ("calories_today", "Calories today", "calories", "kcal", "mdi:fire"),
    ("protein_today", "Protein today", "protein", "g", "mdi:food-steak"),
    ("carbohydrates_today", "Carbohydrates today", "carbohydrates", "g", "mdi:bread-slice"),
    ("fat_today", "Fat today", "fat", "g", "mdi:oil"),
)


def device_info(tracker) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, tracker.identity)}, name=tracker.entry.title, entry_type=DeviceEntryType.SERVICE)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    tracker = entry.runtime_data
    entities: list[SensorEntity] = [TotalSensor(tracker, *spec) for spec in TOTALS]
    entities += [RemainingSensor(tracker, nutrient, name.replace(" today", ""), unit, "mdi:target")
                 for _, name, nutrient, unit, _ in TOTALS]
    entities += [SyncSensor(tracker), SelectedDaySensor(tracker), HistorySensor(tracker), RecipesSensor(tracker)]
    add(entities)


class Base(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, tracker, key: str, name: str) -> None:
        self.tracker = tracker
        self._attr_unique_id = tracker.entity_id(key)
        self._attr_name = name
        self._attr_device_info = device_info(tracker)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._refresh))

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()


class TotalSensor(Base):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, tracker, key, name, nutrient, unit, icon) -> None:
        super().__init__(tracker, key, name)
        self.nutrient = nutrient
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_suggested_display_precision = 0

    @property
    def native_value(self):
        return self.tracker.catalog.day(self.tracker.today())["totals"][self.nutrient] or 0

    @property
    def extra_state_attributes(self):
        if self.nutrient != "calories":
            return None
        day = self.tracker.catalog.day(self.tracker.today())
        return {"meals": {m: v["calories"] for m, v in day["meals"].items()},
                "entries": [f"{e['meal']}: {e['name']} ({e['calories'] or 0:g} kcal)" for e in day["entries"]][:30]}


class RemainingSensor(Base):
    """What's left of one daily goal; unknown when that goal is not set."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, tracker, nutrient: str, name: str, unit: str, icon: str) -> None:
        super().__init__(tracker, f"{nutrient}_remaining", f"{name} remaining")
        self.nutrient = nutrient
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_suggested_display_precision = 0

    def _status(self):
        return self.tracker.goal_status()

    def _item(self):
        return self._status()["progress"].get(self.nutrient)

    @property
    def native_value(self):
        item = self._item()
        return None if item is None else item["remaining"]

    @property
    def extra_state_attributes(self):
        status = self._status()
        item = status["progress"].get(self.nutrient)
        if not item:
            return {"goal": None}
        attrs = {"goal": item["goal"], "eaten": item["eaten"], "percent": item["percent"]}
        if self.nutrient == "calories" and status.get("week"):
            attrs.update(status["week"])
            attrs["daily_goal"] = status["goals"]["calories"]
        return attrs


class SyncSensor(Base):
    _attr_icon = "mdi:cloud-sync"

    def __init__(self, tracker) -> None:
        super().__init__(tracker, "myfitnesspal_sync", "MyFitnessPal sync")

    @property
    def native_value(self):
        return self.tracker.status.get("state", "idle")

    @property
    def extra_state_attributes(self):
        return {**{k: v for k, v in self.tracker.status.items() if k != "state"},
                "foods": len(self.tracker.catalog.foods),
                "diary_scanned_through": self.tracker.catalog.sync.get("diary_scanned_through")}


class SelectedDaySensor(Base):
    """Calories of the day chosen with the diary date picker; entries for the dashboard table."""

    _attr_native_unit_of_measurement = "kcal"
    _attr_icon = "mdi:notebook"
    _unrecorded_attributes = frozenset({"entries", "meals", "totals"})

    def __init__(self, tracker) -> None:
        super().__init__(tracker, "selected_day", "Selected day")

    @property
    def native_value(self):
        return round(self.tracker.catalog.day(self.tracker.shown_day())["totals"]["calories"] or 0)

    @property
    def extra_state_attributes(self):
        day = self.tracker.catalog.day(self.tracker.shown_day())
        order = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}
        foods = self.tracker.catalog.foods
        entries = sorted(day["entries"], key=lambda e: (order.get(e["meal"], 9), e["eaten_at"]))
        return {
            "date": day["day"],
            "totals": {k: round(v or 0, 1) for k, v in day["totals"].items()},
            "meals": {m: round(v["calories"] or 0) for m, v in day["meals"].items()},
            "entries": [
                {"meal": e["meal"], "name": e["name"], "portion": e["portion"],
                 "calories": round(e["calories"] or 0), "protein": round(e["protein"] or 0, 1),
                 "carbohydrates": round(e["carbohydrates"] or 0, 1), "fat": round(e["fat"] or 0, 1),
                 "ingredients": ", ".join(foods.get(self.tracker.catalog.entry_food(e["entry_id"]) or "", {})
                                          .get("ingredients", []))[:300]}
                for e in entries
            ][:80],
        }


class HistorySensor(Base):
    """Daily totals for the last 60 days, for charts."""

    _attr_native_unit_of_measurement = "kcal"
    _attr_icon = "mdi:chart-bar"
    _unrecorded_attributes = frozenset({"days"})

    def __init__(self, tracker) -> None:
        super().__init__(tracker, "daily_history", "Daily average (30 days)")

    def _days(self, count: int) -> list[dict]:
        today = dt_util.now().date()
        return self.tracker.catalog.history([(today - timedelta(days=i)).isoformat() for i in range(count - 1, -1, -1)])

    @property
    def native_value(self):
        logged = [d["calories"] for d in self._days(30) if d["calories"]]
        return round(sum(logged) / len(logged)) if logged else 0

    @property
    def extra_state_attributes(self):
        return {"days": [{k: (round(v) if isinstance(v, float) else v) for k, v in d.items()
                          if k in ("day", "calories", "protein", "carbohydrates", "fat")} for d in self._days(60)]}


class RecipesSensor(Base):
    """Recipes and saved meals with their ingredients."""

    _attr_icon = "mdi:chef-hat"
    _unrecorded_attributes = frozenset({"items"})

    def __init__(self, tracker) -> None:
        super().__init__(tracker, "recipes", "Recipes and meals")

    def _items(self):
        return sorted((f for f in self.tracker.catalog.foods.values() if f["kind"] in ("recipe", "meal")),
                      key=lambda f: (f["kind"], f["name"].lower()))

    @property
    def native_value(self):
        return len(self._items())

    @property
    def extra_state_attributes(self):
        return {"items": [
            {"kind": f["kind"], "name": f["name"], "calories": round(f["nutrients"]["calories"] or 0),
             "protein": round(f["nutrients"]["protein"] or 0), "carbohydrates": round(f["nutrients"]["carbohydrates"] or 0),
             "fat": round(f["nutrients"]["fat"] or 0), "ingredients": "; ".join(f.get("ingredients", []))[:600]}
            for f in self._items()]}
