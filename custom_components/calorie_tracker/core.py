"""Food catalog and diary logic, independent of Home Assistant so it can be tested alone."""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

NUTRIENTS = ("calories", "protein", "carbohydrates", "fat", "fiber", "sugar", "sodium")
MEALS = ("breakfast", "lunch", "dinner", "snack")
MEAL_ALIASES = {
    "desayuno": "breakfast", "almuerzo": "lunch", "comida": "lunch", "cena": "dinner",
    "merienda": "snack", "snacks": "snack", "tentempie": "snack", "lonche": "snack",
}
TARGETS = ("calories", "protein", "carbohydrates", "fat")
TARGET_ALIASES = {"carbs": "carbohydrates", "grasa": "fat", "grasas": "fat", "proteina": "protein",
                  "proteinas": "protein", "calorias": "calories", "carbohidratos": "carbohydrates"}
KINDS = ("food", "recipe", "meal")
UNIT_ALIASES = {
    "g": "g", "gr": "g", "gram": "g", "grams": "g", "gramo": "g", "gramos": "g", "gram(s)": "g",
    "ml": "ml", "milliliter": "ml", "milliliters": "ml", "mililitro": "ml", "mililitros": "ml",
    "kg": "kg", "oz": "oz", "ounce": "oz", "ounces": "oz",
    "serving": "serving", "servings": "serving", "serving(s)": "serving", "porcion": "serving",
    "porción": "serving", "racion": "serving", "ración": "serving",
    "unit": "unit", "units": "unit", "unidad": "unit", "unidades": "unit", "pieza": "unit",
    "piezas": "unit", "piece": "unit", "pieces": "unit",
    "cup": "cup", "cups": "cup", "taza": "cup", "tazas": "cup",
    "tbsp": "tbsp", "tablespoon": "tbsp", "cucharada": "tbsp", "cucharadas": "tbsp",
    "tsp": "tsp", "teaspoon": "tsp", "cucharadita": "tsp", "cucharaditas": "tsp",
}


def unit_key(unit: str) -> str:
    text = unit.strip().lower()
    return UNIT_ALIASES.get(text, UNIT_ALIASES.get(text.rstrip("s"), text))


def meal_key(meal: str) -> str:
    key = fold(meal.strip())
    key = MEAL_ALIASES.get(key, key)
    if key not in MEALS:
        raise ValueError("meal must be breakfast/desayuno, lunch/almuerzo, dinner/cena or snack")
    return key


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return text.lower()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


def round1(value: float) -> float:
    return round(value * 10) / 10


def nutrients(values: dict[str, Any] | None) -> dict[str, float | None]:
    values = values or {}
    result: dict[str, float | None] = {}
    for key in NUTRIENTS:
        value = values.get(key)
        result[key] = float(value) if isinstance(value, (int, float)) and value >= 0 else None
    return result


def scale(values: dict[str, float | None], factor: float) -> dict[str, float | None]:
    return {key: None if values.get(key) is None else round1(values[key] * factor) for key in NUTRIENTS}


def total(items: list[dict[str, float | None]]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for key in NUTRIENTS:
        known = [item[key] for item in items if item.get(key) is not None]
        result[key] = round1(sum(known)) if known else None
    return result


def display_name(food: dict[str, Any]) -> str:
    brand = (food.get("brand") or "").strip()
    return food["name"] + (f" ({brand})" if brand and fold(brand) != fold(food["name"]) else "")


def serving_label(amount: float, unit: str) -> str:
    return f"{amount:g} {unit}".strip()


@dataclass
class Catalog:
    """All foods plus diary entries. Serialised as plain JSON in Home Assistant storage."""

    foods: dict[str, dict[str, Any]] = field(default_factory=dict)
    entries: list[dict[str, Any]] = field(default_factory=list)
    sync: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, data: dict[str, Any] | None) -> "Catalog":
        data = data or {}
        return cls(data.get("foods", {}), data.get("entries", []), data.get("sync", {}))

    def dump(self) -> dict[str, Any]:
        return {"foods": self.foods, "entries": self.entries, "sync": self.sync}

    # ---- catalog -------------------------------------------------------------------------
    def upsert(self, food: dict[str, Any]) -> dict[str, Any]:
        """Insert or update a food; keeps local usage counts. Returns the stored food."""
        previous = self.foods.get(food["id"], {})
        stored = {**previous, **food, "uses": previous.get("uses", 0)}
        if "mfp_uses" in food:
            stored["mfp_uses"] = max(food["mfp_uses"], previous.get("mfp_uses", 0))
        self.foods[food["id"]] = stored
        return stored

    def save_manual(self, name: str, brand: str, amount: float, unit: str, values: dict) -> dict:
        food_id = "manual:" + slug(f"{brand} {name} {unit_key(unit)}")
        return self.upsert({
            "id": food_id, "source": "manual", "kind": "food", "name": name.strip(),
            "brand": brand.strip(), "serving_amount": amount, "serving_unit": unit.strip(),
            "servings": [], "ingredients": [], "nutrients": nutrients(values),
        })

    def search(self, query: str, kind: str | None, offset: int, limit: int) -> dict[str, Any]:
        words = fold(query).split()
        matches = [
            food for food in self.foods.values()
            if (not kind or food["kind"] == kind)
            and all(word in fold(food["name"] + " " + food.get("brand", "")) for word in words)
        ]
        matches.sort(key=lambda f: (-(f.get("uses", 0) * 5 + f.get("mfp_uses", 0)), len(f["name"]), f["name"]))
        page = matches[offset: offset + limit]
        return {
            "items": [self.brief(food) for food in page],
            "total": len(matches),
            "next_offset": offset + len(page) if offset + len(page) < len(matches) else None,
        }

    @staticmethod
    def brief(food: dict[str, Any]) -> dict[str, Any]:
        values = food["nutrients"]
        return {
            "id": food["id"], "kind": food["kind"], "name": food["name"], "brand": food.get("brand", ""),
            "serving": serving_label(food["serving_amount"], food["serving_unit"]),
            "other_servings": [serving_label(s["amount"], s["unit"]) for s in food.get("servings", [])][:4],
            "calories": values["calories"], "protein": values["protein"],
            "carbohydrates": values["carbohydrates"], "fat": values["fat"],
        }

    def recommend(self, targets: dict[str, float], kind: str | None = None, query: str = "",
                  limit: int = 5, tolerance: float = 0.15) -> dict[str, Any]:
        """Rank foods by how closely a sensible portion matches the requested nutrient targets.

        Any subset of calories/protein/carbohydrates/fat may be given. Each food is tried at
        several portion sizes; error is the mean relative difference (with a small absolute floor
        so 0 g targets work). Foods whose best portion is within tolerance are marked as a match.
        """
        targets = {TARGET_ALIASES.get(k, k): float(v) for k, v in targets.items() if v is not None}
        targets = {k: v for k, v in targets.items() if k in TARGETS and v >= 0}
        if not targets:
            raise ValueError("Give at least one target: calories, protein, carbohydrates or fat")
        floor = {"calories": 25.0, "protein": 3.0, "carbohydrates": 3.0, "fat": 2.0}
        words = fold(query).split()
        ranked = []
        for food in self.foods.values():
            if kind and food["kind"] != kind:
                continue
            if words and not all(w in fold(food["name"] + " " + food.get("brand", "")) for w in words):
                continue
            values = food["nutrients"]
            if any(values.get(k) is None for k in targets) or not values.get("calories"):
                continue
            for factor, portion in self._portions(food):
                error = sum(abs(values[k] * factor - v) / max(v, floor[k]) for k, v in targets.items()) / len(targets)
                ranked.append((error, -(food.get("uses", 0) * 5 + food.get("mfp_uses", 0)), food["id"], factor, portion))
        ranked.sort()
        seen, picks = set(), []
        for error, _, food_id, factor, portion in ranked:
            if food_id in seen:
                continue
            seen.add(food_id)
            food = self.foods[food_id]
            picks.append({
                "food_id": food_id, "name": food["name"], "brand": food.get("brand", ""), "kind": food["kind"],
                "portion": portion, "servings": round(factor, 3),
                "nutrients": {k: v for k, v in scale(food["nutrients"], factor).items() if k in TARGETS},
                "match": error <= tolerance, "difference_pct": round(error * 100),
            })
            if len(picks) == limit:
                break
        return {"targets": targets, "items": picks,
                "notice": "Portions are scaled from the saved serving. Log with food_id and servings."}

    @staticmethod
    def _portions(food: dict[str, Any]) -> list[tuple[float, str]]:
        """Reasonable portions: halves of the base serving, or 25 g/ml steps for weight-based foods."""
        unit, amount = unit_key(food["serving_unit"]), food["serving_amount"]
        if unit in ("g", "ml") and amount > 0:
            return [(q / amount, f"{q:g} {food['serving_unit']}") for q in range(25, 501, 25)]
        base = serving_label(amount, food["serving_unit"])
        return [(n, f"{n:g} × {base}") for n in (0.5, 1, 1.5, 2, 2.5, 3)]

    def food(self, food_id: str) -> dict[str, Any]:
        food = self.foods.get(food_id)
        if not food:
            raise KeyError("Food not found; search the catalog first")
        return {
            **self.brief(food), "source": food["source"], "nutrients_per_serving": food["nutrients"],
            "servings": food.get("servings", []), "ingredients": food.get("ingredients", []),
        }

    @staticmethod
    def factor(food: dict[str, Any], servings: float | None, quantity: float | None, unit: str | None) -> tuple[float, str]:
        """Multiplier against the base serving, plus a label describing the portion."""
        base = serving_label(food["serving_amount"], food["serving_unit"])
        if quantity is None:
            count = servings or 1
            if unit_key(food["serving_unit"]) in ("g", "ml"):
                return count, f"{round(count * food['serving_amount']):g} {food['serving_unit']}"
            return count, f"{count:g} × {base}"
        wanted = unit_key(unit or food["serving_unit"])
        options = [{"amount": food["serving_amount"], "unit": food["serving_unit"], "multiplier": 1}]
        options += food.get("servings", [])
        for option in options:
            if unit_key(option["unit"]) == wanted and option["amount"] > 0:
                return quantity / option["amount"] * option["multiplier"], f"{quantity:g} {option['unit']}"
        # A weight serving can still convert between g and kg.
        for option in options:
            pair = (unit_key(option["unit"]), wanted)
            if pair in (("g", "kg"), ("kg", "g")) and option["amount"] > 0:
                grams = quantity * (1000 if wanted == "kg" else 0.001)
                return grams / option["amount"] * option["multiplier"], f"{quantity:g} {unit}"
        known = ", ".join(sorted({o["unit"] for o in options}))
        raise ValueError(f"Unit '{unit}' is not available for this food; use one of: {known}, or servings")

    # ---- diary ---------------------------------------------------------------------------
    def log(self, *, day: str, eaten_at: datetime, meal: str, food_id: str | None = None,
            name: str | None = None, values: dict | None = None, servings: float | None = None,
            quantity: float | None = None, unit: str | None = None) -> dict[str, Any]:
        meal = meal_key(meal)
        if food_id:
            food = self.foods.get(food_id)
            if not food:
                raise KeyError("Food not found; search the catalog first")
            factor, label = self.factor(food, servings, quantity, unit)
            entry_name = name or display_name(food)
            logged = scale(food["nutrients"], factor)
            food["uses"] = food.get("uses", 0) + 1
        else:
            if not name or not values or values.get("calories") is None:
                raise ValueError("Provide food_id, or a name with at least calories")
            factor, label = servings or 1, f"{servings or 1:g} × portion"
            logged = scale(nutrients(values), factor)
            entry_name = name
        entry = {
            "id": uuid.uuid4().hex[:12], "food_id": food_id, "name": entry_name, "meal": meal,
            "portion": label, "day": day, "eaten_at": eaten_at.isoformat(), "nutrients": logged,
        }
        self.entries.append(entry)
        return entry

    def replace_imported(self, day: str, source: str, entries: list[dict[str, Any]]) -> None:
        """Replace one day's entries from an external diary, so re-syncs never duplicate them."""
        self.entries = [e for e in self.entries if not (e["day"] == day and e.get("source") == source)]
        self.entries.extend({**entry, "source": source, "day": day} for entry in entries)

    def history(self, days: list[str]) -> list[dict[str, Any]]:
        """Daily totals for the given days (missing days included with zero calories)."""
        wanted = set(days)
        grouped: dict[str, list] = {d: [] for d in days}
        for entry in self.entries:
            if entry["day"] in wanted:
                grouped[entry["day"]].append(entry["nutrients"])
        return [{"day": d, **{k: v or 0 for k, v in total(grouped[d]).items()}} for d in days]

    def entry_food(self, entry_id: str) -> str | None:
        return next((e.get("food_id") for e in self.entries if e["id"] == entry_id), None)

    def delete(self, ids: list[str]) -> int:
        wanted = set(ids)
        found = {entry["id"] for entry in self.entries if entry["id"] in wanted}
        if found != wanted:
            raise KeyError("Every entry must exist in the diary; nothing was deleted")
        self.entries = [entry for entry in self.entries if entry["id"] not in wanted]
        return len(found)

    def day(self, day: str) -> dict[str, Any]:
        items = sorted((e for e in self.entries if e["day"] == day), key=lambda e: e["eaten_at"])
        by_meal = {meal: total([e["nutrients"] for e in items if e["meal"] == meal]) for meal in MEALS}
        return {
            "day": day,
            "totals": total([e["nutrients"] for e in items]),
            "meals": {meal: values for meal, values in by_meal.items() if values["calories"] is not None},
            "entries": [
                {"entry_id": e["id"], "meal": e["meal"], "name": e["name"], "portion": e["portion"],
                 "eaten_at": e["eaten_at"], "source": e.get("source", "zero"), **{k: e["nutrients"][k] for k in ("calories", "protein", "carbohydrates", "fat")}}
                for e in items
            ],
        }

    def range(self, start: str, end: str) -> dict[str, Any]:
        days: dict[str, list] = {}
        for entry in self.entries:
            if start <= entry["day"] <= end:
                days.setdefault(entry["day"], []).append(entry["nutrients"])
        summaries = [{"day": d, "entries": len(v), "totals": total(v)} for d, v in sorted(days.items())]
        average = scale(total([s["totals"] for s in summaries]), 1 / len(summaries)) if summaries else None
        return {"start": start, "end": end, "logged_days": len(summaries), "daily_average": average, "days": summaries}
