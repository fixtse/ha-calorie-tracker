"""Print an optional Lovelace dashboard as JSON (also valid YAML). No HA writes."""
import argparse
import json
import re


def dashboard(prefix):
    if not re.fullmatch(r"[a-z0-9_]+", prefix):
        raise ValueError("Use the entity prefix shown in Home Assistant, e.g. alice")
    sensor = lambda suffix: f"sensor.{prefix}_{suffix}"
    history, estimate = sensor("tdee_history"), sensor("estimated_tdee")
    weekly = {"type": "custom:flex-table-card", "title": "Weekly averages and TDEE",
              "entities": {"include": history}, "sort_by": "Week-",
              "css": {"tbody tr:nth-child(odd)": "background-color: rgba(127,127,127,0.06)"},
              "columns": [
                  {"name": "Week", "data": "weeks", "modify": "x.week", "align": "right"},
                  {"name": "Start", "data": "weeks", "modify": "x.start"},
                  {"name": "Avg kg", "data": "weeks", "modify": 'x.average_weight_kg == null ? "—" : x.average_weight_kg.toFixed(2)', "align": "right"},
                  {"name": "Avg kcal", "data": "weeks", "modify": 'x.average_calories == null ? "—" : Math.round(x.average_calories)', "align": "right"},
                  {"name": "Change kg", "data": "weeks", "modify": 'x.weight_change_kg == null ? "—" : x.weight_change_kg.toFixed(2)', "align": "right"},
                  {"name": "TDEE", "data": "weeks", "modify": 'x.smoothed_tdee == null ? "—" : Math.round(x.smoothed_tdee)', "align": "right"},
                  {"name": "Weight days", "data": "weeks", "modify": 'x.weight_days + "/7"', "align": "right"},
                  {"name": "Intake days", "data": "weeks", "modify": 'x.calorie_days + "/7"', "align": "right"},
                  {"name": "Status", "data": "weeks", "modify": '!x.complete ? "In progress" : x.eligible ? "Complete" : "Incomplete data"'},
              ]}
    summary = """{% set phase = state_attr('ESTIMATE', 'status') %}
{% set labels = {'disabled': 'Tracking disabled', 'waiting_for_body_stats': 'Waiting for starting weight and body fat', 'waiting_for_start_date': 'Waiting for the starting date', 'initial_estimate': 'Initial estimate (calibration in progress)', 'adaptive': 'Based on completed weight and intake weeks', 'invalid_targets': 'Adjust the weekly goal: no valid calorie or macro targets'} %}
**Phase:** {{ labels.get(phase, 'Measurements not available') }}

**Calibrated weeks:** {{ state_attr('ESTIMATE', 'calibration_weeks') or 0 }}

Positive weekly change means gain; negative means loss. Keep starting body stats fixed. Complete diary days are needed for calibration.

{% set ranges = state_attr('ESTIMATE', 'ranges') %}
{% if ranges %}**Macro ranges (g):** Protein {{ ranges.protein[0] | round }}–{{ ranges.protein[1] | round }}, fat {{ ranges.fat[0] | round }}–{{ ranges.fat[1] | round }}, carbs {{ ranges.carbohydrates[0] | round }}–{{ ranges.carbohydrates[1] | round }}.{% endif %}""".replace("ESTIMATE", estimate)
    charts = {"type": "custom:apexcharts-card", "header": {"show": True, "title": "Daily weight and calorie intake"},
              "graph_span": "90d", "span": {"end": "day"},
              "yaxis": [{"id": "kg", "decimals": 1}, {"id": "kcal", "opposite": True, "decimals": 0}],
              "series": [{"entity": history, "name": "Weight", "yaxis_id": "kg", "type": "line", "color": "#ff9800",
                          "data_generator": "return (entity.attributes.days || []).map(x => [new Date(x.day + 'T12:00:00').getTime(), x.weight_kg]);"},
                         {"entity": history, "name": "Intake", "yaxis_id": "kcal", "type": "column", "color": "#78909c",
                          "data_generator": "return (entity.attributes.days || []).map(x => [new Date(x.day + 'T12:00:00').getTime(), x.calories]);"}]}
    return {"title": "Nutrition calculations", "views": [{"title": "Calculations", "path": "calculations", "icon": "mdi:calculator",
            "cards": [{"type": "entities", "title": "Tracking and weight-change goal", "show_header_toggle": False,
                       "entities": [f"switch.{prefix}_adaptive_tdee", f"switch.{prefix}_automatic_nutrition_goals",
                                    f"number.{prefix}_weekly_weight_change_goal"]},
                      {"type": "entities", "title": "TDEE and daily requirements", "entities": [estimate, sensor("tracked_weight")] +
                       [sensor("adaptive_" + key + "_target") for key in ("calories", "protein", "fat", "carbohydrates")]},
                      {"type": "markdown", "content": summary}, charts,
                      weekly]}]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, help="HA entity prefix, e.g. alice or calorie_tracker")
    print(json.dumps(dashboard(parser.parse_args().prefix), indent=2))
