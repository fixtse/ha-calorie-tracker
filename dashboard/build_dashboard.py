import argparse
import json
from pathlib import Path
import re

from build_tdee_dashboard import dashboard as tdee_dashboard
SEL = "sensor.calorie_tracker_selected_day"
def tpl_card(name, expr, icon, color):
    return {"type": "custom:mushroom-template-card", "primary": name, "icon": icon, "icon_color": color,
            "secondary": "{{ " + expr + " }}", "entity": SEL, "tap_action": {"action": "none"}}
macros = lambda key, unit: f"(state_attr('{SEL}','totals') or {{}}).get('{key}', 0) | round(0) | int ~ ' {unit}'"
meal_names = "{'breakfast':'Desayuno','lunch':'Almuerzo','dinner':'Cena','snack':'Snack'}"
diario = {
    "title": "Diario", "path": "diario", "icon": "mdi:notebook", "type": "sections", "max_columns": 3,
    "sections": [
        {"type": "grid", "cards": [
            {"type": "heading", "heading": "Día", "heading_style": "title"},
            {"type": "horizontal-stack", "cards": [
                {"type": "custom:mushroom-entity-card", "entity": "button.calorie_tracker_previous_day", "_press": "previous_day", "name": "Anterior",
                 "icon": "mdi:chevron-left", "layout": "vertical", "primary_info": "name", "secondary_info": "none"},
                {"type": "custom:mushroom-entity-card", "entity": "button.calorie_tracker_today", "_press": "today", "name": "Hoy",
                 "icon": "mdi:calendar-today", "layout": "vertical", "primary_info": "name", "secondary_info": "none"},
                {"type": "custom:mushroom-entity-card", "entity": "button.calorie_tracker_next_day", "_press": "next_day", "name": "Siguiente",
                 "icon": "mdi:chevron-right", "layout": "vertical", "primary_info": "name", "secondary_info": "none"},
            ]},
            {"type": "entities", "entities": [{"entity": "date.calorie_tracker_diary_date", "name": "Fecha"}]},
            {"type": "custom:mushroom-template-card", "entity": "button.calorie_tracker_copy_day_to_today", "primary": "Copiar este día a hoy",
             "secondary": "{% set d = states('date.calorie_tracker_diary_date') %}{% if d == now().date() | string %}Elige otro día para copiarlo{% else %}Registra las comidas del {{ d }} hoy{% endif %}",
             "icon": "mdi:content-copy", "icon_color": "blue",
             "tap_action": {"action": "perform-action", "perform_action": "button.press", "target": {"entity_id": "button.calorie_tracker_copy_day_to_today"},
                            "confirmation": {"text": "¿Copiar todas las comidas de este día a hoy?"}}},
            {"type": "grid", "columns": 2, "square": False, "cards": [
                tpl_card("Calorías", f"states('{SEL}') ~ ' kcal'", "mdi:fire", "deep-orange"),
                tpl_card("Proteína", macros("protein", "g"), "mdi:food-steak", "red"),
                tpl_card("Carbohidratos", macros("carbohydrates", "g"), "mdi:bread-slice", "amber"),
                tpl_card("Grasa", macros("fat", "g"), "mdi:oil", "yellow"),
            ]},
            {"type": "markdown", "content":
                "{% set m = state_attr('" + SEL + "','meals') or {} %}{% set n = " + meal_names + " %}"
                "{% if m %}{% for k in ['breakfast','lunch','dinner','snack'] if k in m %}"
                "**{{ n[k] }}:** {{ m[k] }} kcal{% if not loop.last %} · {% endif %}{% endfor %}"
                "{% else %}_Sin registros este día._{% endif %}"},
        ]},
        {"type": "grid", "column_span": 2, "cards": [
            {"type": "heading", "heading": "Comidas del día", "heading_style": "title"},
            {"type": "custom:flex-table-card", "entities": {"include": SEL}, "sort_by": "order+",
             "css": {"tbody tr:nth-child(odd)": "background-color: rgba(127,127,127,0.06)"},
             "columns": [
                 {"name": "order", "data": "entries", "modify": "({'breakfast':1,'lunch':2,'dinner':3,'snack':4})[x.meal]", "hidden": True},
                 {"name": "Comida", "data": "entries", "modify": "({'breakfast':'Desayuno','lunch':'Almuerzo','dinner':'Cena','snack':'Snack'})[x.meal]"},
                 {"name": "Alimento", "data": "entries", "modify": "x.ingredients ? x.name + '<br><small>' + x.ingredients + '</small>' : x.name"},
                 {"name": "Porción", "data": "entries", "modify": "x.portion"},
                 {"name": "kcal", "data": "entries", "modify": "x.calories", "align": "right"},
                 {"name": "P", "data": "entries", "modify": "x.protein", "align": "right"},
                 {"name": "C", "data": "entries", "modify": "x.carbohydrates", "align": "right"},
                 {"name": "G", "data": "entries", "modify": "x.fat", "align": "right"},
             ]},
            {"type": "custom:apexcharts-card", "header": {"show": True, "title": "Calorías — últimos 60 días", "show_states": True, "colorize_states": True},
             "graph_span": "60d", "span": {"end": "day"}, "apex_config": {"chart": {"height": 260}, "dataLabels": {"enabled": False}},
             "series": [{"entity": "sensor.calorie_tracker_daily_average_30_days", "name": "Promedio 30 días", "type": "column", "color": "#ff7043",
                         "show": {"in_header": "raw"},
                         "data_generator": "return entity.attributes.days.map(d => [new Date(d.day + 'T12:00:00').getTime(), d.calories]);"}]},
        ]},
        {"type": "grid", "cards": [
            {"type": "heading", "heading": "Objetivos", "heading_style": "subtitle"},
            {"type": "grid", "columns": 2, "square": False, "cards": [
                {"type": "custom:mushroom-template-card", "entity": "sensor.calorie_tracker_calories_remaining", "primary": "Calorías (hoy)", "icon": "mdi:fire", "icon_color": "{% set p = state_attr('sensor.calorie_tracker_calories_remaining','percent') %}{% if p is none %}grey{% elif p > 100 %}red{% elif p >= 90 %}orange{% else %}green{% endif %}", "secondary": "{% set a = states['sensor.calorie_tracker_calories_remaining'].attributes %}{% if a.goal %}{{ a.eaten | round(0) | int }} / {{ a.goal | round(0) | int }} · quedan {{ states('sensor.calorie_tracker_calories_remaining') | float(0) | round(0) | int }}{% else %}Sin objetivo{% endif %}", "tap_action": {"action": "more-info"}},
                {"type": "custom:mushroom-template-card", "entity": "sensor.calorie_tracker_protein_remaining", "primary": "Proteína", "icon": "mdi:food-steak", "icon_color": "{% set p = state_attr('sensor.calorie_tracker_protein_remaining','percent') %}{% if p is none %}grey{% elif p > 100 %}red{% elif p >= 90 %}orange{% else %}green{% endif %}", "secondary": "{% set a = states['sensor.calorie_tracker_protein_remaining'].attributes %}{% if a.goal %}{{ a.eaten | round(0) | int }} / {{ a.goal | round(0) | int }} · quedan {{ states('sensor.calorie_tracker_protein_remaining') | float(0) | round(0) | int }}{% else %}Sin objetivo{% endif %}", "tap_action": {"action": "more-info"}},
                {"type": "custom:mushroom-template-card", "entity": "sensor.calorie_tracker_carbohydrates_remaining", "primary": "Carbohidratos", "icon": "mdi:bread-slice", "icon_color": "{% set p = state_attr('sensor.calorie_tracker_carbohydrates_remaining','percent') %}{% if p is none %}grey{% elif p > 100 %}red{% elif p >= 90 %}orange{% else %}green{% endif %}", "secondary": "{% set a = states['sensor.calorie_tracker_carbohydrates_remaining'].attributes %}{% if a.goal %}{{ a.eaten | round(0) | int }} / {{ a.goal | round(0) | int }} · quedan {{ states('sensor.calorie_tracker_carbohydrates_remaining') | float(0) | round(0) | int }}{% else %}Sin objetivo{% endif %}", "tap_action": {"action": "more-info"}},
                {"type": "custom:mushroom-template-card", "entity": "sensor.calorie_tracker_fat_remaining", "primary": "Grasa", "icon": "mdi:oil", "icon_color": "{% set p = state_attr('sensor.calorie_tracker_fat_remaining','percent') %}{% if p is none %}grey{% elif p > 100 %}red{% elif p >= 90 %}orange{% else %}green{% endif %}", "secondary": "{% set a = states['sensor.calorie_tracker_fat_remaining'].attributes %}{% if a.goal %}{{ a.eaten | round(0) | int }} / {{ a.goal | round(0) | int }} · quedan {{ states('sensor.calorie_tracker_fat_remaining') | float(0) | round(0) | int }}{% else %}Sin objetivo{% endif %}", "tap_action": {"action": "more-info"}}
            ]},
            {"type": "markdown", "content": "{% set a = states.sensor.calorie_tracker_calories_remaining.attributes %}{% if a.weekly_goal %}**Semana:** {{ a.week_eaten }} / {{ a.weekly_goal }} kcal · quedan {{ a.week_remaining }} kcal en {{ a.days_left }} día(s){% if a.per_day_after_today %} · ~{{ a.per_day_after_today }} kcal/día después de hoy{% endif %}{% endif %}"},
            {"type": "heading", "heading": "Hoy", "heading_style": "subtitle"},
            {"type": "tile", "entity": "sensor.calorie_tracker_calories_today", "name": "Calorías hoy"},
            {"type": "heading", "heading": "MyFitnessPal", "heading_style": "subtitle"},
            {"type": "tile", "entity": "sensor.calorie_tracker_myfitnesspal_sync", "name": "Sincronización"},
            {"type": "tile", "entity": "button.calorie_tracker_sync_myfitnesspal", "name": "Sincronizar ahora"},
        ]},
    ],
}
recetas = {
    "title": "Recetas", "path": "recetas", "icon": "mdi:chef-hat", "type": "panel",
    "cards": [{"type": "custom:flex-table-card", "title": "Recetas y comidas guardadas", "entities": {"include": "sensor.calorie_tracker_recipes_and_meals"},
               "enable_search": True, "sort_by": "Nombre+",
               "columns": [
                   {"name": "Tipo", "data": "items", "modify": "x.kind == 'recipe' ? 'Receta' : 'Comida'"},
                   {"name": "Nombre", "data": "items", "modify": "x.name"},
                   {"name": "kcal", "data": "items", "modify": "x.calories", "align": "right"},
                   {"name": "P", "data": "items", "modify": "x.protein", "align": "right"},
                   {"name": "C", "data": "items", "modify": "x.carbohydrates", "align": "right"},
                   {"name": "G", "data": "items", "modify": "x.fat", "align": "right"},
                   {"name": "Ingredientes", "data": "items", "modify": "x.ingredients.split('; ').join('<br>')"},
               ]}],
}
def fix(o):
    if isinstance(o, dict):
        if "_press" in o:
            b = o.pop("_press")
            o["tap_action"] = {"action": "perform-action", "perform_action": "button.press", "target": {"entity_id": f"button.calorie_tracker_{b}"}}
        for v in o.values(): fix(v)
    elif isinstance(o, list):
        for v in o: fix(v)
fix(diario)


def dashboard(prefix="calorie_tracker"):
    if not re.fullmatch(r"[a-z0-9_]+", prefix):
        raise ValueError("Use the entity prefix shown in Home Assistant")
    definition = json.loads(json.dumps({"title": "Calorías", "views": [diario, recetas]}).replace(
        "calorie_tracker_", prefix + "_"))
    cards = tdee_dashboard(prefix)["views"][0]["cards"]
    definition["views"][0]["sections"].extend([
        {"type": "grid", "cards": cards[:3]},
        {"type": "grid", "cards": cards[3:]},
    ])
    return definition


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a per-user diary, recipes and TDEE dashboard.")
    parser.add_argument("--prefix", default="calorie_tracker", help="Entity prefix shown in HA")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("dashboard.json"))
    args = parser.parse_args()
    args.output.write_text(json.dumps(dashboard(args.prefix), ensure_ascii=False, indent=2) + "\n")
