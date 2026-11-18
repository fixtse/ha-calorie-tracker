"""Date picker choosing which diary day the dashboard shows."""

from datetime import date

from homeassistant.components.date import DateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_UPDATED
from .sensor import device_info


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([DiaryDate(entry.runtime_data)])


class DiaryDate(DateEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Diary date"
    _attr_unique_id = f"{DOMAIN}_diary_date"
    _attr_icon = "mdi:calendar"

    def __init__(self, tracker) -> None:
        self.tracker = tracker
        self._attr_unique_id = tracker.entity_id("diary_date")
        self._attr_device_info = device_info(tracker)

    @property
    def native_value(self) -> date:
        return date.fromisoformat(self.tracker.shown_day())

    async def async_set_value(self, value: date) -> None:
        self.tracker.select_day(value)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._refresh))

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()
