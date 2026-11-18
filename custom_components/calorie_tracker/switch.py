"""Independent opt-ins for tracking and automatic calorie/macro targets."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL_UPDATED
from .sensor import device_info


async def async_setup_entry(hass, entry, add):
    add([AdaptiveSwitch(entry.runtime_data, "tdee_enabled", "Adaptive TDEE"),
         AdaptiveSwitch(entry.runtime_data, "tdee_auto_goals", "Automatic nutrition goals")])


class AdaptiveSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(self, tracker, key, name):
        self.tracker, self.key = tracker, key
        self._attr_name = name
        self._attr_unique_id = tracker.entity_id(key)
        self._attr_device_info = device_info(tracker)

    @property
    def is_on(self):
        return bool(self.tracker.adaptive.settings.get(self.key, False))

    async def async_turn_on(self, **kwargs):
        await self.tracker.adaptive.controls({self.key: True})
        if self.key == "tdee_enabled":
            await self.tracker.adaptive.capture()

    async def async_turn_off(self, **kwargs):
        await self.tracker.adaptive.controls({self.key: False})

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self.refresh))

    @callback
    def refresh(self):
        self.async_write_ha_state()
