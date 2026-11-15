"""Daily goals: remaining amounts, per-meal targets and near/over alert decisions (no HA imports)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

GOAL_KEYS = ("calories", "protein", "carbohydrates", "fat")
LABELS = {"calories": "Calorías", "protein": "Proteína", "carbohydrates": "Carbohidratos", "fat": "Grasa"}
UNITS = {"calories": "kcal", "protein": "g", "carbohydrates": "g", "fat": "g"}
MAIN_MEALS = ("breakfast", "lunch", "dinner")
SNACK_SHARE = 0.15  # a snack gets this share of what is left for the day
LEVELS = ("ok", "near", "over")
ALERT_INTERVAL = timedelta(minutes=30)


WEEK_FLOOR = 0.6  # weekly flexibility must leave at least this share of the daily goal per remaining day


def week_budget(daily_goal: float, eaten_before_today: float, eaten_today: float, days_left: int) -> dict[str, Any]:
    """Weekly calorie budget: today's allowance spreads what's left of the week over the days left.

    days_left counts today. Over-eating earlier lowers later days and under-eating raises them.
    """
    weekly = daily_goal * 7
    left_before_today = weekly - eaten_before_today
    allowance = left_before_today / max(days_left, 1)
    reserve = WEEK_FLOOR * daily_goal * (days_left - 1)
    return {
        "weekly_goal": round(weekly), "week_eaten": round(eaten_before_today + eaten_today),
        "week_remaining": round(left_before_today - eaten_today), "days_left": days_left,
        "today_allowance": round(allowance), "today_remaining": round(allowance - eaten_today),
        # Most today can take while leaving WEEK_FLOOR of the daily goal for every later day.
        "today_flex_max": round(max(left_before_today - reserve - eaten_today, 0)),
        "per_day_after_today": round((left_before_today - eaten_today) / (days_left - 1)) if days_left > 1 else None,
    }


def clean_goals(goals: dict[str, Any] | None) -> dict[str, float]:
    """Keep positive numeric goals only; 0 or missing means no goal."""
    return {k: float(v) for k, v in (goals or {}).items() if k in GOAL_KEYS and isinstance(v, (int, float)) and v > 0}


def progress(goals: dict[str, float], totals: dict[str, float | None]) -> dict[str, dict[str, float]]:
    result = {}
    for key, goal in clean_goals(goals).items():
        eaten = totals.get(key) or 0
        result[key] = {"goal": goal, "eaten": round(eaten, 1), "remaining": round(goal - eaten, 1),
                       "percent": round(eaten / goal * 100)}
    return result


def meal_targets(goals: dict[str, float], totals: dict[str, float | None], logged_meals: set[str],
                 meal: str | None, flex_remaining: dict[str, float] | None = None) -> dict[str, Any]:
    """Limits for one meal: its share of what's left today, by main meals not yet logged.

    Used as upper bounds, not amounts to hit. Exhausted nutrients get a 0 limit.
    """
    remaining = {k: max(v["remaining"], 0) for k, v in progress(goals, totals).items()}
    if not remaining:
        raise ValueError("No goals set; set goals first or give explicit targets")
    if meal == "snack":
        share, note = SNACK_SHARE, f"{round(SNACK_SHARE * 100)}% of what's left today"
    else:
        pending = [m for m in MAIN_MEALS if m not in logged_meals or m == meal]
        count = max(len(pending), 1)
        share, note = 1 / count, f"what's left today split across {count} remaining main meal(s)"
    limits = {k: round(v * share, 1) for k, v in remaining.items()}
    result = {"meal": meal, "basis": note, "remaining_today": remaining, "limits": limits}
    if flex_remaining:
        # Looser fallback limits (e.g. weekly calories); never below the normal ones.
        result["flex_limits"] = {**limits, **{k: max(round(v, 1), limits.get(k, 0)) for k, v in flex_remaining.items()}}
    return result


def level(percent: float, near: float) -> str:
    return "over" if percent > 100 else "near" if percent >= near else "ok"


def check_alerts(goals: dict[str, float], totals: dict[str, float | None], state: dict[str, Any], day: str,
                 now: datetime, near_percent: float = 90, week: dict[str, Any] | None = None) -> tuple[dict[str, Any], str | None]:
    """Decide whether to notify. Returns (new_state, message or None).

    Each goal notifies once per level per day (near, then over). At most one notification
    is sent per ALERT_INTERVAL; changes held back are included in the next allowed one.
    """
    if state.get("day") != day:
        state = {"day": day, "notified": {}, "last_sent": state.get("last_sent")}
    state = {**state, "notified": dict(state.get("notified", {}))}
    changes = []
    for key, item in progress(goals, totals).items():
        current = level(item["percent"], near_percent)
        if LEVELS.index(current) > LEVELS.index(state["notified"].get(key, "ok")):
            changes.append((key, current, item))
    if not changes:
        return state, None
    last = state.get("last_sent")
    if last and now - datetime.fromisoformat(last) < ALERT_INTERVAL:
        return {**state, "pending": True}, None
    lines = []
    for key, current, item in changes:
        unit = UNITS[key]
        if current == "over":
            what = "meta alcanzada" if key == "protein" else "superado"
            lines.append(f"{LABELS[key]}: {what}, {item['eaten']:g}/{item['goal']:g} {unit} ({item['percent']}%)")
        else:
            lines.append(f"{LABELS[key]}: cerca del objetivo, {item['eaten']:g}/{item['goal']:g} {unit} "
                         f"({item['percent']}%, quedan {item['remaining']:g} {unit})")
        if key == "calories" and week:
            after = week.get("per_day_after_today")
            lines[-1] += (f" · semana: {week['week_eaten']:g}/{week['weekly_goal']:g} kcal"
                          + (f", quedan ~{after:g} kcal/día" if after is not None else ""))
        state["notified"][key] = current
    state["last_sent"] = now.isoformat()
    state.pop("pending", None)
    return state, "\n".join(lines)
