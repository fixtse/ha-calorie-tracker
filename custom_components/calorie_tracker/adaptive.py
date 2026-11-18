"""Home Assistant scale adapter for the optional, per-user TDEE model."""
from __future__ import annotations

from datetime import datetime

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .tdee import calculate, kilograms, numeric


def bodymiscale_entities(hass, entry_id):
    """Resolve registry identities, never guess entity IDs from a person's display name."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if not entry or entry.domain != "bodymiscale":
        return {}
    name = {**entry.data, **entry.options}.get("name")
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry_id)
    result = {}
    for key in ("weight", "body_fat", "last_measurement_time"):
        matches = [item.entity_id for item in entities if item.platform == "bodymiscale"
                   and not item.disabled and item.domain == "sensor"
                   and (item.unique_id == f"bodymiscale_{name}_{key}" if name else item.unique_id.endswith("_" + key))]
        if len(matches) == 1:
            result[key] = matches[0]
    return result


class AdaptiveTDEE:
    def __init__(self, tracker):
        self.tracker = tracker
        self._cache = None
        self._day = None
        self._cancel = None
        self._unsubscribe = None
        self._registry_unsubscribe = None
        self._watching = set()

    @property
    def state(self):
        return self.tracker.catalog.sync.setdefault("adaptive_tdee", {})

    @property
    def settings(self):
        return {**self.tracker.entry.options, **self.state.get("controls", {})}

    def invalidate(self):
        self._cache = None

    def result(self):
        day = self.tracker.today()
        if self._cache is None or self._day != day:
            self._cache = calculate(self.settings, self.state, self.tracker.catalog.entries, day)
            self._day = day
        return self._cache

    async def controls(self, values):
        async with self.tracker.lock:
            self.state.setdefault("controls", {}).update(values)
            await self.tracker.save()

    async def record(self, kg, measured_at, body_fat=None, source="manual"):
        if not kg or not 0 < kg <= 1000 or measured_at.tzinfo is None:
            raise ValueError("Provide a positive weight and a timezone-aware measurement time")
        if measured_at > dt_util.utcnow():
            raise ValueError("A measurement cannot be in the future")
        day = dt_util.as_local(measured_at).date().isoformat()
        async with self.tracker.lock:
            weights = self.state.setdefault("weights", {})
            previous = weights.get(day)
            if previous and datetime.fromisoformat(previous["measured_at"]) > measured_at:
                return  # late packets must not replace that day's latest weighing
            weights[day] = {"kg": kg, "measured_at": measured_at.isoformat(), "source": source}
            self.state.setdefault("initial_weight", kg)
            self.state.setdefault("start_date", day)
            if body_fat is not None and 0 < body_fat < 100:
                self.state.setdefault("initial_body_fat", body_fat)
            await self.tracker.save()

    def entities(self):
        settings = self.settings
        if settings.get("tdee_bodymiscale_entry"):
            return bodymiscale_entities(self.tracker.hass, settings["tdee_bodymiscale_entry"])
        return {key: value for key, value in {
            "weight": settings.get("tdee_weight_entity"),
            "body_fat": settings.get("tdee_body_fat_entity"),
            "last_measurement_time": settings.get("tdee_measurement_entity"),
        }.items() if value}

    async def capture(self, _now=None):
        self._cancel = None
        if not self.settings.get("tdee_enabled"):
            return
        entities = self.entities()
        hass = self.tracker.hass
        weight = hass.states.get(entities.get("weight", ""))
        if weight is None or weight.attributes.get("restored"):
            return
        kg = kilograms(weight.state, weight.attributes.get("unit_of_measurement"))
        if kg is None or kg > 1000:
            return
        stamp_entity = entities.get("last_measurement_time")
        if self.settings.get("tdee_bodymiscale_entry") and not stamp_entity:
            return  # Never fall back to a raw shared scale for a BodyMiScale profile.
        if stamp_entity:
            stamp = hass.states.get(stamp_entity)
            measured_at = dt_util.parse_datetime(stamp.state) if stamp else None
            if not measured_at or measured_at.tzinfo is None or measured_at > dt_util.utcnow():
                return
            source = stamp_entity
            # A restored sensor or an idle scale advertisement is not a new weigh-in.
            last = self.state.get("last_measurements", {}).get(source)
            if last and measured_at <= datetime.fromisoformat(last):
                # Body composition can settle just after the weight/timestamp entities.
                fat_state = hass.states.get(entities.get("body_fat", ""))
                fat = numeric(fat_state.state) if fat_state else None
                if measured_at == datetime.fromisoformat(last) and "initial_body_fat" not in self.state and fat is not None and 0 < fat < 100:
                    async with self.tracker.lock:
                        self.state["initial_body_fat"] = fat
                        await self.tracker.save()
                return
        else:
            measured_at, source = weight.last_updated, weight.entity_id
        fat_state = hass.states.get(entities.get("body_fat", ""))
        fat = numeric(fat_state.state) if fat_state else None
        await self.record(kg, measured_at, fat, source)
        if stamp_entity:
            async with self.tracker.lock:
                self.state.setdefault("last_measurements", {})[source] = measured_at.isoformat()
                await self.tracker.save()

    async def setup(self):
        @callback
        def changed(event):
            if event.data.get("old_state") is None:
                return  # a generic entity being restored is not a measurement
            if self._cancel:
                self._cancel()
            self._cancel = async_call_later(self.tracker.hass, 2, self.capture)

        @callback
        def watch(_event=None):
            ids = set(self.entities().values())
            if ids == self._watching:
                return
            if self._unsubscribe:
                self._unsubscribe()
            self._watching = ids
            self._unsubscribe = async_track_state_change_event(self.tracker.hass, list(ids), changed) if ids else None

        watch()
        self._registry_unsubscribe = self.tracker.hass.bus.async_listen("entity_registry_updated", watch)
        self.tracker.entry.async_on_unload(self.close)
        # Only timestamped sources can safely contribute their last reading after restart.
        if self.entities().get("last_measurement_time"):
            await self.capture()

    @callback
    def close(self):
        for unsubscribe in (self._unsubscribe, self._registry_unsubscribe):
            if unsubscribe:
                unsubscribe()
        self._unsubscribe = self._registry_unsubscribe = None
        self._watching = set()
        if self._cancel:
            self._cancel()
            self._cancel = None
