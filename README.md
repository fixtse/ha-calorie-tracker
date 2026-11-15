# Calorie Tracker (Home Assistant)

A Home Assistant integration that keeps a food catalog and food diary. It imports your
MyFitnessPal custom foods, saved meals and recipes (with ingredients), plus your MyFitnessPal
diary entries and every food in them.
Zero reads and writes it through its services. The data lives in
`/config/.storage/calorie_tracker.data`.

## Install

**HACS:** HACS → ⋮ → Custom repositories → add `https://github.com/fixtse/ha-calorie-tracker`
(type *Integration*), install **Calorie Tracker**, then restart Home Assistant.

**Manual:** copy `custom_components/calorie_tracker` into `/config/custom_components/` and restart.

Then go to **Settings → Devices & services → Add integration → Calorie Tracker**.

Works on its own, or with [Zero](https://github.com/fixtse/ha-zero-assistant), whose food tools
call this integration's services.

## MyFitnessPal cookie (the "token")

MyFitnessPal's login requires a reCAPTCHA, so the integration cannot log in with a password.
It uses a session cookie instead:

1. Open a **private window** and log in at <https://www.myfitnesspal.com>.
2. Developer tools → **Storage** (Firefox/Zen) or **Application** (Chrome) → **Cookies** →
   `https://www.myfitnesspal.com` → copy `__Secure-next-auth.session-token`.
3. Close the private window **without logging out**.
4. Paste it into the setup form, or **Re-authenticate** / **⋮ → Reconfigure**.

**Use each cookie in one place only.** MyFitnessPal rotates the cookie whenever it is used, and
reusing an older copy (for example in your normal browser session, or a script) revokes
the session. That's why you copy it from a private window that you then abandon.

The integration keeps the session alive itself. It checks the session every 6 hours, as well as
during syncs, and stores each rotated cookie. You only need a new cookie if the session ends anyway
(password change, logging out everywhere). Home Assistant then shows a notification and a
**Re-authenticate** prompt.

## Syncing

- **Button:** `button.calorie_tracker_sync_myfitnesspal` starts a background sync.
- **Service:** `calorie_tracker.sync_myfitnesspal` with an optional `since: 2020-01-01` to rescan.
- **Automatic:** daily at the time set in the options (default 04:30).

The first sync scans the diary from the **history start** option (default 2020-01-01). That takes
about an hour; progress is saved every 30 days and resumes after a restart. Later syncs only scan
from the last scanned day. Imports only add or update foods, never delete them. MyFitnessPal
diary entries are re-imported per scanned day, so re-syncs don't duplicate them, and entries you
log through Zero or the services are never touched.
`sensor.calorie_tracker_myfitnesspal_sync` shows the state (`running`, `ok`, `error`,
`auth_failed`), progress and counts.

From the server shell:

```sh
curl -X POST -H "Authorization: Bearer $HA_TOKEN" -H "Content-Type: application/json" \
  -d '{"entity_id":"button.calorie_tracker_sync_myfitnesspal"}' \
  http://127.0.0.1:8123/api/services/button/press
```

## Dashboard

`dashboard/` holds a two-view dashboard ("Calorías"): **Diario** has a day picker with
previous/next/today buttons, that day's calories and macros, a per-meal breakdown, a table of
foods with portions and ingredients, and a 60-day calorie chart. **Recetas** is a searchable table
of recipes and saved meals with their ingredients. It needs the Mushroom, flex-table-card and
apexcharts-card frontend cards. Regenerate it with `python3 dashboard/build_dashboard.py`, and
install it with `install_dashboard.py` inside the HA container (pass a token on stdin).

## Entities

- Today: `sensor.calorie_tracker_calories_today` (with per-meal and entry attributes),
  `…_protein_today`, `…_carbohydrates_today`, `…_fat_today`, and `…_calories_remaining` when a
  daily goal is set in the options.
- Day browsing: `date.calorie_tracker_diary_date`, the buttons `…_previous_day`, `…_next_day` and
  `…_today`, and `sensor.calorie_tracker_selected_day` (that day's entries in attributes).
- `sensor.calorie_tracker_daily_average_30_days` (60 days of totals in attributes),
  `sensor.calorie_tracker_recipes_and_meals` (with ingredients),
  `sensor.calorie_tracker_myfitnesspal_sync` and `button.calorie_tracker_sync_myfitnesspal`.

## Goals and alerts

Set daily goals for calories, protein, carbohydrates and fat with `calorie_tracker.set_goals`
(or ask Zero). Calories are balanced **weekly** (Monday–Sunday) by default: today's allowance is
what's left of the week (daily goal × 7) divided by the days left, so a light day leaves more for
later and a heavy one less. Use `calorie_period: day` for a strict daily limit.
`sensor.calorie_tracker_<nutrient>_remaining` shows what's left, with goal, eaten and percent
(plus the weekly budget on calories).

**Recommendations.** Without targets, `recommend_foods` suggests foods that stay **within** this
meal's share of what's left: protein/carbs/fat today, and calories from the weekly allowance.
None of the options needs to use the budget fully. Portions using about 85% of the meal's
calories, and protein-rich ones, rank first. If nothing fits, it retries with the weekly calorie
slack, which still leaves 60% of the daily goal for each remaining day. With explicit targets it
finds the closest portions that still don't exceed the remaining goals.

**Only foods you can still get.** `set_goals` with `recommend_since: 2025-03-01` limits
recommendations to foods you've eaten on or after that date, for example since moving country.
Custom foods saved after that date count too. Pass `recommend_since: ""` to remove the date.
`recommend_foods` takes `since` to override it for one request (`""` searches all history).

**Alerts.** Choose a notify service in the options (for example `notify.mobile_app_<phone>`).
Each goal notifies once when it reaches the near threshold (default 90%) and once when it's
exceeded. At most one notification is sent per 30 minutes; changes held back are included in
the next one.

## Services

All of these return data (`return_response`). Correctable errors come back as
`{"error": "..."}` when a response is requested.

| Service | Purpose |
|---|---|
| `search_foods` | Search by name/brand, most used first |
| `get_food` | All nutrients, serving sizes and ingredients |
| `recommend_foods` | No targets: options within this `meal`'s share of remaining goals. Targets (any of `calories`, `protein`, `carbohydrates`, `fat`): closest portions within remaining goals |
| `log_food` | Log `food_id` with `servings` or `quantity` + `unit`, or `name` + nutrients. `meal` accepts breakfast/desayuno, lunch/almuerzo, dinner/cena, snack |
| `get_diary` | A day's entries and totals, or a range with daily averages |
| `save_food` | Add or update a custom food |
| `delete_entries` | Remove diary entries |
| `set_goals` / `get_goals` | Daily goals (0 removes one), weekly calorie budget, `recommend_since` date, today's progress |
| `sync_myfitnesspal` | Import from MyFitnessPal |

MyFitnessPal has no public API. The integration uses the website's own endpoints, which can
change without notice. It only reads from MyFitnessPal; it never writes there.

Tests: `python3 -B -m unittest discover -s tests` (no Home Assistant needed).
