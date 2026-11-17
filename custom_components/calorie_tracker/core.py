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
    "g": "g", "gr": "g", "grs": "g", "gramm": "g", "gramme": "g", "grammes": "g", "gram": "g", "grams": "g", "gramo": "g", "gramos": "g", "gram(s)": "g",
    "ml": "ml", "milliliter": "ml", "milliliters": "ml", "mililitro": "ml", "mililitros": "ml",
    "kg": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg", "kilogramo": "kg", "kilogramos": "kg",
    "oz": "oz", "ounce": "oz", "ounces": "oz", "onza": "oz", "onzas": "oz",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb", "libra": "lb", "libras": "lb",
    "l": "l", "liter": "l", "liters": "l", "litre": "l", "litres": "l", "litro": "l", "litros": "l",
    "serving": "serving", "servings": "serving", "serving(s)": "serving", "porcion": "serving",
    "porción": "serving", "racion": "serving", "ración": "serving",
    "unit": "unit", "units": "unit", "unidad": "unit", "unidades": "unit", "pieza": "unit",
    "piezas": "unit", "piece": "unit", "pieces": "unit",
    "cup": "cup", "cups": "cup", "taza": "cup", "tazas": "cup",
    "tbsp": "tbsp", "tablespoon": "tbsp", "cucharada": "tbsp", "cucharadas": "tbsp",
    "tsp": "tsp", "teaspoon": "tsp", "cucharadita": "tsp", "cucharaditas": "tsp",
}


# Size of each unit in the base unit of its kind (grams for weight, millilitres for volume).
_WEIGHT = {"g": 1.0, "kg": 1000.0, "oz": 28.3495, "lb": 453.592}
_VOLUME = {"ml": 1.0, "l": 1000.0}


def _convert_family(have: str, want: str) -> tuple[float, float] | None:
    """Sizes of `have` and `want` in their shared base unit, if they measure the same thing."""
    for table in (_WEIGHT, _VOLUME):
        if have in table and want in table:
            return table[have], table[want]
    return None


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

    def save_manual(self, name: str, brand: str, amount: float, unit: str, values: dict,
                    kind: str = "food", ingredients: list[str] | None = None,
                    instructions: list[str] | None = None, recipe_servings: float = 1) -> dict:
        food_id = "manual:" + slug(f"{brand} {name} {unit_key(unit)}")
        if kind != "food":
            food_id = f"manual-{kind}:" + slug(f"{brand} {name} {unit_key(unit)}")
        return self.upsert({
            "id": food_id, "source": "manual", "kind": kind, "name": name.strip(),
            "brand": brand.strip(), "serving_amount": amount, "serving_unit": unit.strip(),
            "servings": [], "ingredients": ingredients or [], "nutrients": nutrients(values),
            "instructions": instructions or [], "recipe_servings": recipe_servings,
            "saved": datetime.now().date().isoformat(),
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

    def last_eaten(self) -> dict[str, str]:
        """Most recent diary day per food id (cached until the diary changes)."""
        key = (len(self.entries), self.entries[-1]["id"] if self.entries else None)
        if getattr(self, "_last_key", None) != key:
            last: dict[str, str] = {}
            for entry in self.entries:
                food_id = entry.get("food_id")
                if food_id and entry["day"] > last.get(food_id, ""):
                    last[food_id] = entry["day"]
            self._last_cache, self._last_key = last, key
        return self._last_cache

    def available_since(self, food: dict[str, Any], since: str | None) -> bool:
        """Eaten on/after `since` (or a custom food saved since then): likely still obtainable."""
        if not since:
            return True
        return self.last_eaten().get(food["id"], "") >= since or food.get("saved", "") >= since

    def _candidates(self, kind: str | None, query: str, needed: set[str], since: str | None = None):
        words = fold(query).split()
        for food in self.foods.values():
            if not self.available_since(food, since):
                continue
            if kind and food["kind"] != kind:
                continue
            if words and not all(w in fold(food["name"] + " " + food.get("brand", "")) for w in words):
                continue
            values = food["nutrients"]
            if not values.get("calories") or any(values.get(k) is None for k in needed):
                continue  # limits can't be verified without these values
            yield food

    @staticmethod
    def _within(values: dict, factor: float, limits: dict[str, float]) -> bool:
        return all(values[k] * factor <= cap + 0.05 for k, cap in limits.items())  # rounding only

    def _pick(self, ranked: list, limit: int, matcher) -> list[dict[str, Any]]:
        ranked.sort(key=lambda r: r[:2])
        seen, picks = set(), []
        for score, usage, food_id, factor, portion in ranked:
            food = self.foods[food_id]
            # MyFitnessPal often has several entries (or brand labels) for one dish; suggest each name once.
            key = " ".join(fold(food["name"]).split())
            if food_id in seen or key in seen:
                continue
            seen.update((food_id, key))
            picks.append({
                "food_id": food_id, "name": food["name"], "brand": food.get("brand", ""), "kind": food["kind"],
                "portion": portion, "servings": round(factor, 3),
                "nutrients": {k: v for k, v in scale(food["nutrients"], factor).items() if k in TARGETS},
                **matcher(score),
            })
            if len(picks) == limit:
                break
        return picks

    def recommend(self, targets: dict[str, float], kind: str | None = None, query: str = "",
                  limit: int = 5, tolerance: float = 0.15, limits: dict[str, float] | None = None,
                  since: str | None = None) -> dict[str, Any]:
        """Foods whose portion is closest to explicit targets (any of calories/protein/carbs/fat).

        Error is the mean relative difference, with a small absolute floor so 0 g targets work.
        Optional limits (e.g. what's left of today's goals) are hard caps a portion may not exceed.
        """
        targets = {TARGET_ALIASES.get(k, k): float(v) for k, v in targets.items() if v is not None}
        targets = {k: v for k, v in targets.items() if k in TARGETS and v >= 0}
        if not targets:
            raise ValueError("Give at least one target: calories, protein, carbohydrates or fat")
        limits = {k: max(v, 0) for k, v in (limits or {}).items() if k in TARGETS}
        floor = {"calories": 25.0, "protein": 3.0, "carbohydrates": 3.0, "fat": 2.0}
        ranked = []
        for food in self._candidates(kind, query, set(targets) | set(limits), since):
            values = food["nutrients"]
            for factor, portion in self._portions(food):
                if not self._within(values, factor, limits):
                    continue
                error = sum(abs(values[k] * factor - v) / max(v, floor[k]) for k, v in targets.items()) / len(targets)
                ranked.append((error + self._novelty(food), -self._usage(food), food["id"], factor, portion))
        picks = self._pick(ranked, limit, lambda e: {})
        for pick in picks:
            error = sum(abs((pick["nutrients"].get(k) or 0) - v) / max(v, floor[k]) for k, v in targets.items()) / len(targets)
            pick.update({"match": error <= tolerance, "difference_pct": round(error * 100)})
        result = {"mode": "targets", "targets": targets, "items": picks,
                  "notice": "Portions are scaled from the saved serving. Log with food_id and servings."}
        if limits:
            result["limits"] = limits
        if since:
            result["only_eaten_since"] = since
        return result

    def recommend_within(self, limits: dict[str, float], kind: str | None = None, query: str = "",
                         limit: int = 5, fill: float = 0.85, since: str | None = None) -> dict[str, Any]:
        """General suggestions that stay within every limit (e.g. this meal's share of remaining goals).

        A portion never exceeds any limit. Among those, portions using about `fill` of the budget
        rank first (calories when limited, else the average share), with a bonus for protein when
        protein is limited, since it is usually the goal to reach rather than avoid.
        """
        limits = {k: max(float(v), 0) for k, v in limits.items() if k in TARGETS}
        if not limits:
            raise ValueError("No limits to recommend within; set goals or give explicit targets")
        ranked = []
        for food in self._candidates(kind, query, set(limits), since):
            values = food["nutrients"]
            for factor, portion in self._portions(food):
                if not self._within(values, factor, limits):
                    continue
                shares = {k: values[k] * factor / cap for k, cap in limits.items() if cap > 0}
                if not shares:
                    continue
                used = shares.get("calories", sum(shares.values()) / len(shares))
                if used < 0.25:
                    continue  # too small to be a meal suggestion
                score = abs(used - fill) - 0.3 * shares.get("protein", 0) + self._novelty(food)
                ranked.append((score, -self._usage(food), food["id"], factor, portion))
        picks = self._pick(ranked, limit, lambda s: {})
        for pick in picks:
            pick["share_of_budget_pct"] = {k: round(pick["nutrients"][k] / cap * 100)
                                           for k, cap in limits.items() if cap > 0 and pick["nutrients"].get(k) is not None}
        result = {"mode": "within_limits", "limits": limits, "items": picks,
                  "notice": "Every option stays within the limits; none needs to use them fully."}
        if not picks:
            exhausted = [k for k, v in limits.items() if v <= 0]
            result["notice"] = ("Nothing fits: " + ", ".join(exhausted) + " already used up today."
                                if exhausted else "No registered food fits within these limits.")
        if since:
            result["only_eaten_since"] = since
        return result

    @staticmethod
    def _usage(food: dict[str, Any]) -> int:
        return food.get("uses", 0) * 5 + food.get("mfp_uses", 0)

    def _typical(self, food_id: str) -> float | None:
        """Median portion (in base servings) the user actually logged for this food."""
        if getattr(self, "_typical_size", None) != len(self.entries):
            factors: dict[str, list[float]] = {}
            for entry in self.entries:
                food = self.foods.get(entry.get("food_id") or "")
                logged, base = entry["nutrients"].get("calories"), food and food["nutrients"].get("calories")
                if food and logged and base:
                    factors.setdefault(food["id"], []).append(logged / base)
            self._typical_cache = {k: sorted(v)[len(v) // 2] for k, v in factors.items()}
            self._typical_size = len(self.entries)
        return self._typical_cache.get(food_id)

    def _portions(self, food: dict[str, Any]) -> list[tuple[float, str]]:
        """Realistic portions: around what the user usually eats of this food (½× to 2×).

        Foods never logged fall back to the base serving's multiples, or 25 g steps up to 300 g
        for weight-based foods.
        """
        unit, amount = unit_key(food["serving_unit"]), food["serving_amount"]
        weight = unit in ("g", "ml") and amount > 0
        typical = self._typical(food["id"])
        if typical:
            factors = sorted({round(typical * m, 3) for m in (0.5, 0.75, 1, 1.25, 1.5, 2)})
        elif weight:
            factors = [q / amount for q in range(25, 301, 25)]
        else:
            factors = [0.5, 1, 1.5, 2]
        portions = []
        for factor in factors:
            if weight:
                grams = max(round(factor * amount / 5) * 5, 5)
                portions.append((grams / amount, f"{grams:g} {food['serving_unit']}"))
            else:
                count = max(round(factor * 4) / 4, 0.25)  # quarter servings
                portions.append((count, f"{count:g} × {serving_label(amount, food['serving_unit'])}"))
        return list(dict.fromkeys(portions))

    def _novelty(self, food: dict[str, Any]) -> float:
        """Small ranking penalty for foods the user has never eaten."""
        return 0 if self._usage(food) or self._typical(food["id"]) else 0.1

    def food(self, food_id: str) -> dict[str, Any]:
        food = self.foods.get(food_id)
        if not food:
            raise KeyError("Food not found; search the catalog first")
        return {
            **self.brief(food), "source": food["source"], "nutrients_per_serving": food["nutrients"],
            "servings": food.get("servings", []), "ingredients": food.get("ingredients", []),
            "instructions": food.get("instructions", []), "recipe_servings": food.get("recipe_servings", 1),
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
        # A weight (or volume) serving converts to any other unit of the same kind: 30 g, 1 oz, 0.5 kg.
        for option in options:
            family = _convert_family(unit_key(option["unit"]), wanted)
            if family and option["amount"] > 0:
                base_per_serving = option["amount"] * family[0]
                return quantity * family[1] / base_per_serving * option["multiplier"], f"{quantity:g} {unit}"
        known = ", ".join(sorted({o["unit"] for o in options}))
        raise ValueError(f"Unit '{unit}' is not available for this food; use one of: {known}, or servings "
                         "(this food has no weight, so exact grams can't be computed; ask for its serving size instead)")

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
        """Replace one day's entries from an external diary, so re-syncs never duplicate them.

        Entries edited here are kept as edited; the incoming copy of them is ignored.
        """
        edited = {e["id"] for e in self.entries if e.get("edited")}
        self.entries = [e for e in self.entries
                        if e.get("edited") or not (e["day"] == day and e.get("source") == source)]
        self.entries.extend({**entry, "source": source, "day": day} for entry in entries if entry["id"] not in edited)

    def update_entry(self, entry_id: str, *, meal: str | None = None, eaten_at: datetime | None = None,
                     name: str | None = None, servings: float | None = None, quantity: float | None = None,
                     unit: str | None = None, values: dict | None = None) -> dict[str, Any]:
        """Change one diary entry. A new portion recomputes nutrients from its food; values override them."""
        entry = next((e for e in self.entries if e["id"] == entry_id), None)
        if not entry:
            raise KeyError("Entry not found; read the diary first")
        changed = dict(entry)
        if meal:
            changed["meal"] = meal_key(meal)
        if eaten_at:
            changed["eaten_at"], changed["day"] = eaten_at.isoformat(), eaten_at.date().isoformat()
        if name:
            changed["name"] = name
        if servings is not None or quantity is not None:
            food = self.foods.get(entry.get("food_id") or "")
            if not food:
                raise ValueError("This entry has no catalog food; set calories and macros directly instead")
            factor, changed["portion"] = self.factor(food, servings, quantity, unit)
            changed["nutrients"] = scale(food["nutrients"], factor)
        if values:
            fixed = nutrients(values)
            changed["nutrients"] = {k: fixed[k] if k in values else v for k, v in changed["nutrients"].items()}
        changed["edited"] = True
        entry.clear()
        entry.update(changed)
        return entry

    def copy_day(self, source: str, target: str, meals: list[str] | None = None,
                 to_meal: str | None = None) -> list[dict[str, Any]]:
        """Log a copy of a day's entries (optionally some meals only) on another day, keeping times."""
        wanted = {meal_key(m) for m in meals} if meals else None
        items = sorted((e for e in self.entries if e["day"] == source and (not wanted or e["meal"] in wanted)),
                       key=lambda e: e["eaten_at"])
        if not items:
            raise ValueError(f"Nothing to copy on {source}")
        copies = []
        for item in items:
            clock = item["eaten_at"][10:] if len(item["eaten_at"]) > 10 else "T12:00:00"
            copy = {k: v for k, v in item.items() if k not in ("source", "edited", "mfp_meal")}
            copy.update(id=uuid.uuid4().hex[:12], day=target, eaten_at=target + clock, copied_from=source,
                        meal=meal_key(to_meal) if to_meal else item["meal"], nutrients=dict(item["nutrients"]))
            if item.get("food_id") in self.foods:
                self.foods[item["food_id"]]["uses"] = self.foods[item["food_id"]].get("uses", 0) + 1
            copies.append(copy)
        self.entries.extend(copies)
        return copies

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
