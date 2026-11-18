"""Editable weight-change goal, persisted separately for each tracker user."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL_UPDATED
from .sensor import device_info


async def async_setup_entry(hass, entry, add):
    add([WeeklyGoal(entry.runtime_data)])


class WeeklyGoal(NumberEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Weekly weight change goal"
    _attr_icon = "mdi:scale-bathroom"
    _attr_native_unit_of_measurement = "kg/week"
    _attr_native_min_value = -10
    _attr_native_max_value = 10
    _attr_native_step = .01
    _attr_mode = NumberMode.BOX

    def __init__(self, tracker):
        self.tracker = tracker
        self._attr_unique_id = tracker.entity_id("weekly_weight_change_goal")
        self._attr_device_info = device_info(tracker)

    @property
    def native_value(self):
        return self.tracker.adaptive.settings.get("tdee_weekly_goal", 0)

    async def async_set_native_value(self, value):
        await self.tracker.adaptive.controls({"tdee_weekly_goal": value})

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self.refresh))

    @callback
    def refresh(self):
        self.async_write_ha_state()
