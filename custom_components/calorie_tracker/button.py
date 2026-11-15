"""Sync button and diary day navigation buttons."""

from datetime import date, timedelta

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .sensor import device_info


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    t = entry.runtime_data
    add([
        Button(t, "sync_myfitnesspal", "Sync MyFitnessPal", "mdi:cloud-download", lambda: t.start_sync()),
        Button(t, "previous_day", "Previous day", "mdi:chevron-left", lambda: t.select_day(_shift(t, -1))),
        Button(t, "next_day", "Next day", "mdi:chevron-right", lambda: t.select_day(_shift(t, 1))),
        Button(t, "today", "Today", "mdi:calendar-today", lambda: t.select_day(None)),
    ])


def _shift(tracker, days: int) -> date:
    return min(date.fromisoformat(tracker.shown_day()) + timedelta(days=days), dt_util.now().date())


class Button(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, tracker, key, name, icon, action) -> None:
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = device_info()
        self._action = action

    async def async_press(self) -> None:
        self._action()
