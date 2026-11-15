"""Minimal MyFitnessPal web client: uses the website's own JSON endpoints with a session cookie.

MyFitnessPal has no public API. These are the endpoints the website itself calls, so they can
change without notice. Only reads are performed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta
from typing import Any, Awaitable, Callable

import aiohttp
from yarl import URL

from .core import Catalog, display_name, nutrients

_LOGGER = logging.getLogger(__name__)
BASE = URL("https://www.myfitnesspal.com/")
COOKIE = "__Secure-next-auth.session-token"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
MFP_KEYS = {"protein": "protein", "carbohydrates": "carbohydrates", "carbs": "carbohydrates",
            "fat": "fat", "fiber": "fiber", "sugar": "sugar", "sodium": "sodium"}


class AuthError(Exception):
    """The session cookie is missing, expired or rejected."""


def _number(text: Any) -> float | None:
    if isinstance(text, (int, float)):
        return float(text)
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(text or ""))
    if not match:
        return None
    value = match.group(0)
    if re.fullmatch(r"-?\d+,\d{1,2}", value):
        return float(value.replace(",", "."))
    return float(value.replace(",", ""))


def _energy(contents: dict[str, Any]) -> float | None:
    energy = contents.get("energy")
    if isinstance(energy, dict):
        value = _number(energy.get("value"))
        if value is not None and str(energy.get("unit", "")).startswith("kilojoule"):
            value /= 4.184
        return value
    return _number(contents.get("calories"))


def food_from_mfp(food: dict[str, Any], uses: int = 0) -> dict[str, Any] | None:
    """Convert a MyFitnessPal food object (diary or "my foods") into a catalog food."""
    sizes = [s for s in food.get("serving_sizes") or [] if (s.get("nutrition_multiplier") or 0) > 0]
    if not sizes or not food.get("id"):
        return None
    base = next((s for s in sizes if s["nutrition_multiplier"] == 1), sizes[0])
    contents = food.get("nutritional_contents") or {}
    divisor = base["nutrition_multiplier"]
    values = {target: _number(contents.get(source)) for source, target in MFP_KEYS.items()
              if contents.get(source) is not None}
    values["calories"] = _energy(contents)
    values = {k: (v / divisor if v is not None else None) for k, v in values.items()}
    brand = (food.get("brand_name") or "").strip()
    name = (food.get("description") or "").strip()
    if brand and name.lower().startswith(brand.lower() + " - "):
        name = name[len(brand) + 3:].strip()
    return {
        "id": f"mfp:{food['id']}", "source": "myfitnesspal", "kind": "food",
        "name": name or "Unnamed food", "brand": brand,
        "serving_amount": float(base.get("value") or 1), "serving_unit": base.get("unit") or "serving",
        "servings": [
            {"amount": float(s.get("value") or 1), "unit": s.get("unit") or "serving",
             "multiplier": s["nutrition_multiplier"] / divisor}
            for s in sizes if s is not base
        ][:8],
        "ingredients": [], "nutrients": nutrients(values), "mfp_uses": uses,
    }


MEAL_NAMES = {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner", "snacks": "snack", "snack": "snack",
              "desayuno": "breakfast", "almuerzo": "lunch", "comida": "lunch", "cena": "dinner", "merienda": "snack"}


def entry_from_mfp(entry: dict[str, Any], food: dict[str, Any] | None) -> dict[str, Any] | None:
    """A MyFitnessPal diary line as a diary entry, with the nutrients MyFitnessPal recorded."""
    raw = entry.get("food") or {}
    if not entry.get("id") or not raw:
        return None
    contents = entry.get("nutritional_contents") or {}
    values = {target: _number(contents.get(source)) for source, target in MFP_KEYS.items()
              if contents.get(source) is not None}
    values["calories"] = _energy(contents)
    size = entry.get("serving_size") or {}
    count = _number(entry.get("servings")) or 1
    portion = f"{count * (_number(size.get('value')) or 1):g} {size.get('unit') or 'serving'}"
    meal = MEAL_NAMES.get(str(entry.get("meal_name", "")).strip().lower(), "snack")
    return {
        "id": f"mfp:{entry['id']}", "food_id": food["id"] if food else None,
        "name": display_name(food) if food else str(raw.get("description") or "Food"),
        "meal": meal, "mfp_meal": entry.get("meal_name"), "portion": portion,
        "eaten_at": f"{entry.get('date')}T12:00:00", "nutrients": nutrients(values),
    }


def meal_from_mfp(meal: dict[str, Any]) -> dict[str, Any] | None:
    foods = meal.get("foods") or []
    if not meal.get("meal_id") or not foods:
        return None
    values: dict[str, float] = {}
    for item in foods:
        for source, target in {"calories": "calories", **MFP_KEYS}.items():
            value = _number(item.get(source))
            if value is not None:
                values[target] = values.get(target, 0) + value
    return {
        "id": f"mfp-meal:{meal['meal_id']}", "source": "myfitnesspal", "kind": "meal",
        "name": (meal.get("description") or "Meal").strip(), "brand": "",
        "serving_amount": 1, "serving_unit": "meal", "servings": [],
        "ingredients": [str(f.get("description", "")).strip() for f in foods], "nutrients": nutrients(values),
    }


def recipe_from_html(recipe_id: str, html: str) -> dict[str, Any] | None:
    import lxml.html  # bundled with Home Assistant

    doc = lxml.html.fromstring(html)

    def text(path: str) -> str:
        found = doc.xpath(path)
        return found[0].text_content().strip() if found else ""

    name = text('//*[@id="main"]/div[3]/div[2]/h1')
    if not name:
        return None
    values = {"calories": _number(text('//*[@id="main"]/div[3]/div[2]/div[2]/div'))}
    for element, key in {"carbs": "carbohydrates", "fiber": "fiber", "sugar": "sugar", "sodium": "sodium",
                         "protein": "protein", "total_fat": "fat"}.items():
        values[key] = _number(text(f'//*[@id="{element}"]/td[1]/span[2]'))
    return {
        "id": f"mfp-recipe:{recipe_id}", "source": "myfitnesspal", "kind": "recipe", "name": name,
        "brand": "", "serving_amount": 1, "serving_unit": "serving", "servings": [],
        "recipe_servings": _number(text('//*[@id="recipe_servings"]')) or 1,
        "ingredients": [li.text_content().strip() for li in doc.xpath('//*[@id="main"]/div[4]/div/*/li')],
        "nutrients": nutrients(values),
    }


class MyFitnessPal:
    def __init__(self, session_token: str):
        self._token = session_token.strip()
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "MyFitnessPal":
        jar = aiohttp.CookieJar()
        jar.update_cookies({COOKIE: self._token}, BASE)
        self._session = aiohttp.ClientSession(
            cookie_jar=jar, headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=45),
        )
        try:
            await self.authenticate()
        except BaseException:
            await self._session.close()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session:
            await self._session.close()

    @property
    def session_token(self) -> str:
        """The current cookie value; MyFitnessPal may rotate it during a session."""
        if self._session:
            for cookie in self._session.cookie_jar:
                if cookie.key == COOKIE and cookie.value:
                    return cookie.value
        return self._token

    async def _get(self, path: str, params: dict | None = None, json: bool = True) -> Any:
        assert self._session
        for attempt in range(3):
            try:
                async with self._session.get(BASE.join(URL(path)), params=params,
                                             headers={"Accept": "application/json"} if json else None) as response:
                    if "/account/login" in str(response.url):
                        raise AuthError("MyFitnessPal session expired")
                    if response.status in (429, 502, 503, 504) and attempt < 2:
                        await asyncio.sleep(3 * (attempt + 1))
                        continue
                    response.raise_for_status()
                    return await response.json(content_type=None) if json else await response.text()
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
                if attempt == 2:
                    raise
                await asyncio.sleep(3 * (attempt + 1))
        raise RuntimeError("unreachable")

    async def authenticate(self) -> None:
        try:
            data = await self._get("user/auth_token", {"refresh": "true"})
        except (aiohttp.ContentTypeError, ValueError) as error:
            raise AuthError("MyFitnessPal rejected the session cookie") from error
        if not isinstance(data, dict) or not data.get("access_token"):
            raise AuthError("MyFitnessPal rejected the session cookie")

    async def my_foods(self) -> list[dict]:
        return await self._get("api/services/users/foods/mine", {"limit": "100"}) or []

    async def meals(self) -> list[dict]:
        return await self._get("api/services/users/meals/mine", {"limit": "100"}) or []

    async def recipe_ids(self) -> list[str]:
        ids: list[str] = []
        for page in range(1, 50):
            html = await self._get("recipe_parser", {"page": str(page), "sort_order": "recent"}, json=False)
            found = re.findall(r'href="/recipe/view/(\d+)"', html)
            new = [i for i in dict.fromkeys(found) if i not in ids]
            if not new:
                break
            ids += new
        return ids

    async def recipe(self, recipe_id: str) -> dict | None:
        return recipe_from_html(recipe_id, await self._get(f"recipe/view/{recipe_id}", json=False))

    async def diary(self, day: date) -> list[dict]:
        return await self._get("api/services/diary", {"entry_date": day.isoformat(), "types": "food_entry"}) or []


async def sync_catalog(
    client: MyFitnessPal, catalog: Catalog, start: date, end: date,
    progress: Callable[[str], Awaitable[None] | None] | None = None, delay: float = 0.3,
) -> dict[str, Any]:
    """Import custom foods, saved meals, recipes and every food seen in the diary from start..end."""
    before = set(catalog.foods)
    counts = {"custom_foods": 0, "meals": 0, "recipes": 0, "diary_days": 0, "diary_entries": 0}

    for item in await client.my_foods():
        if not item.get("deleted") and (food := food_from_mfp(item)):
            catalog.upsert(food)
            counts["custom_foods"] += 1
    for item in await client.meals():
        if meal := meal_from_mfp(item):
            catalog.upsert(meal)
            counts["meals"] += 1
    for recipe_id in await client.recipe_ids():
        try:
            if recipe := await client.recipe(recipe_id):
                catalog.upsert(recipe)
                counts["recipes"] += 1
        except Exception as error:  # one broken recipe page must not stop the sync
            _LOGGER.warning("Skipping MyFitnessPal recipe %s: %s", recipe_id, error)

    day = start
    while day <= end:
        imported = []
        for entry in await client.diary(day):
            food = food_from_mfp(entry.get("food") or {})
            if food:
                food["mfp_uses"] = catalog.foods.get(food["id"], {}).get("mfp_uses", 0) + 1
                food = catalog.upsert(food)
            if item := entry_from_mfp(entry, food):
                imported.append(item)
                counts["diary_entries"] += 1
        catalog.replace_imported(day.isoformat(), "myfitnesspal", imported)
        counts["diary_days"] += 1
        catalog.sync["diary_scanned_through"] = day.isoformat()
        if progress and counts["diary_days"] % 30 == 0:
            result = progress(f"Diary scanned through {day.isoformat()}")
            if asyncio.iscoroutine(result):
                await result
        day += timedelta(days=1)
        await asyncio.sleep(delay)

    added = sorted(catalog.foods[k]["name"] for k in set(catalog.foods) - before)
    return {**counts, "foods_total": len(catalog.foods), "new_foods": len(added), "new_food_names": added[:30]}
