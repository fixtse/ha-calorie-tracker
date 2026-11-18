"""Framework-free Calculations-sheet model. All weights are stored in kilograms."""
from __future__ import annotations

from datetime import date, timedelta
from math import isfinite
from statistics import mean

KCAL_PER_KG = 3500 * 2.20462  # CALCULATIONS!BB26, distinct from its 7700 goal conversion.


def numeric(value):
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (ValueError, TypeError):
        return None


def kilograms(value, unit):
    value = numeric(value)
    factor = {"kg": 1, "g": .001, "lb": .45359237, "lbs": .45359237,
              "oz": .028349523125, "st": 6.35029318}.get(str(unit).lower())
    return value * factor if value is not None and factor and value > 0 else None


def macros(tdee, weight, body_fat, weekly_goal):
    """Q6, Q9:Q12 and T10:T12, with numeric body-fat comparisons."""
    calories = tdee + weekly_goal * 7700 / 7
    protein = weight * (2.2 if body_fat < 20 else 1.76 if body_fat <= 25 else 1.606)
    fat = calories * (.22 if body_fat < 25 else .25) / 9
    carbs = (calories - protein * 4 - fat * 9) / 4
    targets = {"calories": calories, "protein": protein, "fat": fat, "carbohydrates": carbs}
    if any(not isfinite(v) or v < 0 for v in targets.values()) or calories <= 0:
        return None, None
    ranges = {"protein": [weight * (1.76 if body_fat < 20 else 1.606),
                           weight * (2.64 if body_fat < 20 else 2.2)],
              "fat": [calories * .15 / 9, calories * .30 / 9],
              "carbohydrates": [carbs * .8, carbs * 1.2]}
    return targets, ranges


def calculate(settings, state, entries, today: str):
    """Return a reproducible estimate and bounded dashboard history, never invent intake.

    Weeks start at the configured starting date (not necessarily Monday). Within-week
    weights carry forward as the sheet does. Intake requires seven logged days; missing
    days and whole missing weight weeks cannot calibrate the model. The first week's
    estimate is shown but excluded from the adaptive average, matching AY28 onward.
    """
    weights = state.get("weights", {})
    initial_weight = numeric(settings.get("tdee_initial_weight")) or numeric(state.get("initial_weight"))
    body_fat = numeric(settings.get("tdee_initial_body_fat")) or numeric(state.get("initial_body_fat"))
    start = settings.get("tdee_start_date") or state.get("start_date")
    summary = {"enabled": bool(settings.get("tdee_enabled", False)),
               "auto_goals": bool(settings.get("tdee_auto_goals", False)),
               "weekly_goal_kg": float(settings.get("tdee_weekly_goal", 0)),
               "start_date": start, "initial_weight_kg": initial_weight, "initial_body_fat": body_fat,
               "status": "disabled", "tdee": None, "targets": None, "ranges": None}
    empty = {"summary": summary, "days": [], "weeks": []}
    if not summary["enabled"]:
        return empty
    if not initial_weight or not body_fat or not 0 < body_fat < 100 or not start:
        summary["status"] = "waiting_for_body_stats"
        return empty
    current, beginning = date.fromisoformat(today), date.fromisoformat(start)
    if beginning > current:
        summary["status"] = "waiting_for_start_date"
        return empty
    intake, unknown_days = {}, set()
    for entry in entries:
        day = entry["day"]
        if start <= day <= today:
            calories = numeric(entry.get("nutrients", {}).get("calories"))
            if calories is None or calories < 0:
                unknown_days.add(day)
            else:
                intake[day] = intake.get(day, 0) + calories
    for day in unknown_days:
        intake.pop(day, None)
    initial_tdee = (370 + 21.6 * initial_weight * (1 - body_fat / 100)) * 1.5
    summary["initial_tdee"] = initial_tdee
    weeks, days, estimates = [], [], []
    last_weight, previous_average, previous_has_weights = initial_weight, initial_weight, True
    for index in range((current - beginning).days // 7 + 1):
        week_start = beginning + timedelta(days=index * 7)
        complete = week_start + timedelta(days=7) <= current
        week_weights, week_intake, observed_weights = [], [], 0
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            if day > current:
                break
            key = day.isoformat()
            measured = numeric(weights.get(key, {}).get("kg"))
            if measured is not None:
                last_weight = measured
                observed_weights += 1
            week_weights.append(last_weight)
            if key in intake:
                week_intake.append(intake[key])
            days.append({"day": key, "weight_kg": measured, "calories": intake.get(key)})
        avg_weight = mean(week_weights) if observed_weights else None
        delta = avg_weight - previous_average if avg_weight is not None and previous_has_weights else None
        eligible = complete and len(week_intake) == 7 and delta is not None
        weekly_tdee = mean(week_intake) - delta * KCAL_PER_KG / 7 if eligible else None
        # Positive estimates only. Incomplete/invalid weeks cannot change the targets.
        if weekly_tdee is not None and weekly_tdee <= 0:
            weekly_tdee = None
        if index > 0 and weekly_tdee is not None:
            estimates.append(weekly_tdee)
        smoothed = mean(estimates) if estimates else weekly_tdee if index == 0 and weekly_tdee is not None else initial_tdee
        weeks.append({"week": index + 1, "start": week_start.isoformat(), "complete": complete,
                      "weight_days": observed_weights, "calorie_days": len(week_intake),
                      "average_weight_kg": avg_weight,
                      "average_calories": mean(week_intake) if week_intake else None,
                      "weight_change_kg": delta, "weekly_tdee": weekly_tdee,
                      "smoothed_tdee": smoothed, "eligible": weekly_tdee is not None})
        if complete:
            previous_has_weights = avg_weight is not None
            if avg_weight is not None:
                previous_average = avg_weight
    # The sheet waits until week four and uses the last completed estimate, not today's partial week.
    completed = [w for w in weeks if w["complete"]]
    adaptive = len(completed) >= 3 and len(estimates) >= 2
    tdee = mean(estimates) if adaptive else initial_tdee
    targets, ranges = macros(tdee, initial_weight, body_fat, summary["weekly_goal_kg"])
    summary.update({"status": "adaptive" if adaptive else "initial_estimate", "tdee": tdee,
                    "targets": targets, "ranges": ranges, "completed_weeks": len(completed),
                    "calibration_weeks": len(estimates), "latest_weight_kg": last_weight,
                    "daily_adjustment": summary["weekly_goal_kg"] * 7700 / 7})
    if targets is None:
        summary["status"] = "invalid_targets"
    return {"summary": summary, "days": days, "weeks": weeks}
