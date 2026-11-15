import importlib.util
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "calorie_tracker"
package = types.ModuleType("calorie_tracker")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("calorie_tracker", package)


def load(name):
    spec = importlib.util.spec_from_file_location(f"calorie_tracker.{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"calorie_tracker.{name}"] = module
    spec.loader.exec_module(module)
    return module


core, goals = load("core"), load("goals")


def food(food_id, name, amount, unit, kind="food", **values):
    return {"id": food_id, "source": "t", "kind": kind, "name": name, "brand": "", "serving_amount": amount,
            "serving_unit": unit, "servings": [], "ingredients": [], "nutrients": core.nutrients(values)}


class WithinLimitsTest(unittest.TestCase):
    def setUp(self):
        self.c = core.Catalog()
        self.c.upsert(food("chicken", "Pechuga", 100, "g", calories=165, protein=31, carbohydrates=0, fat=3.6))
        self.c.upsert(food("rice", "Arroz", 100, "g", calories=130, protein=2.7, carbohydrates=28, fat=0.3))
        self.c.upsert(food("juane", "Juane", 1, "meal", "meal", calories=499, protein=34, carbohydrates=33, fat=23))
        self.c.upsert(food("unknown", "Sin datos", 1, "serving", calories=200))  # macros unknown

    def test_never_exceeds_any_limit(self):
        limits = {"calories": 600, "protein": 60, "carbohydrates": 40, "fat": 10}
        result = self.c.recommend_within(limits)
        self.assertTrue(result["items"])
        for item in result["items"]:
            for key, cap in limits.items():
                self.assertLessEqual(item["nutrients"][key], cap + 0.05, (item["name"], key))
        names = [i["food_id"] for i in result["items"]]
        self.assertNotIn("juane", names)    # 23 g fat > 10 g limit
        self.assertNotIn("unknown", names)  # can't be verified
        self.assertEqual(names[0], "chicken")  # protein-rich use of the budget ranks first

    def test_exhausted_nutrient_explains_empty_result(self):
        self.assertEqual(self.c.recommend_within({"calories": 400, "fat": 0})["items"], [])  # strictly within
        result = self.c.recommend_within({"calories": 400, "protein": 0})
        self.assertEqual(result["items"], [])
        self.assertIn("protein", result["notice"])

    def test_explicit_targets_respect_limits(self):
        free = [i["food_id"] for i in self.c.recommend({"calories": 500})["items"][:2]]
        self.assertIn("juane", free)
        capped = self.c.recommend({"calories": 500}, limits={"fat": 10})["items"]
        self.assertTrue(all(i["nutrients"]["fat"] <= 10.05 for i in capped))

    def test_portions_follow_what_the_user_eats(self):
        for grams in (150, 160, 140):  # usually ~150 g of chicken
            self.c.log(day="2026-11-01", eaten_at=datetime(2026, 11, 1), meal="lunch", food_id="chicken",
                       quantity=grams, unit="g")
        portions = [p for _, p in self.c._portions(self.c.foods["chicken"])]
        self.assertEqual(portions[0], "75 g")
        self.assertEqual(portions[-1], "300 g")  # 2x the usual 150 g
        self.assertIn("150 g", portions)


class SinceFilterTest(unittest.TestCase):
    def test_only_foods_eaten_since(self):
        c = core.Catalog()
        c.upsert(food("lomo", "Lomo saltado", 1, "plato", calories=600, protein=35, carbohydrates=50, fat=25))
        c.upsert(food("tortilla", "Tortilla española", 1, "ración", calories=550, protein=20, carbohydrates=40, fat=30))
        c.log(day="2021-05-01", eaten_at=datetime(2021, 5, 1), meal="dinner", food_id="lomo")       # Peru
        c.log(day="2026-10-01", eaten_at=datetime(2026, 10, 1), meal="dinner", food_id="tortilla")  # Spain
        arepa = c.save_manual("Arepa", "", 1, "unidad", {"calories": 180, "protein": 4, "carbohydrates": 30, "fat": 5})
        targets = {"calories": 580}
        everything = {i["food_id"] for i in c.recommend(targets)["items"]}
        self.assertEqual(everything, {"lomo", "tortilla", arepa["id"]})
        recent = c.recommend(targets, since="2026-01-01")
        self.assertEqual({i["food_id"] for i in recent["items"]}, {"tortilla", arepa["id"]})  # saved today counts
        self.assertEqual(recent["only_eaten_since"], "2026-01-01")
        within = c.recommend_within({"calories": 700, "fat": 40}, since="2026-01-01")["items"]
        self.assertNotIn("lomo", {i["food_id"] for i in within})


class DuplicateNamesTest(unittest.TestCase):
    def test_same_name_suggested_once(self):
        c = core.Catalog()
        for i, brand in ((1, "Brillante"), (2, "Sabroz")):
            item = food(f"q{i}", "Quinoa real", 100, "g", calories=120 + i, protein=4, carbohydrates=21, fat=2)
            c.upsert({**item, "brand": brand})
        names = [i["name"] for i in c.recommend({"calories": 300})["items"]]
        self.assertEqual(names.count("Quinoa real"), 1)


class GoalsTest(unittest.TestCase):
    G = {"calories": 2000, "protein": 150, "carbohydrates": 200, "fat": 60}

    def test_meal_split(self):
        totals = {"calories": 500, "protein": 30, "carbohydrates": 50, "fat": 20}
        lunch = goals.meal_targets(self.G, totals, {"breakfast"}, "lunch")
        self.assertEqual(lunch["limits"]["calories"], 750)  # 1500 left over lunch + dinner
        snack = goals.meal_targets(self.G, totals, {"breakfast"}, "snack")
        self.assertEqual(snack["limits"]["calories"], 225)
        over = goals.meal_targets(self.G, {**totals, "fat": 70}, set(), None)
        self.assertEqual(over["limits"]["fat"], 0)

    def test_alerts_rate_limited_and_once_per_level(self):
        t0 = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
        state, msg = goals.check_alerts(self.G, {"calories": 1850}, {}, "2026-11-16", t0)
        self.assertIn("Calorías", msg)
        state, msg = goals.check_alerts(self.G, {"calories": 1860}, state, "2026-11-16", t0 + timedelta(minutes=5))
        self.assertIsNone(msg)  # same level already notified
        state, msg = goals.check_alerts(self.G, {"calories": 2100, "fat": 58}, state, "2026-11-16",
                                        t0 + timedelta(minutes=10))
        self.assertIsNone(msg)
        self.assertTrue(state["pending"])  # held back by the 30-minute limit
        state, msg = goals.check_alerts(self.G, {"calories": 2100, "fat": 58}, state, "2026-11-16",
                                        t0 + timedelta(minutes=31))
        self.assertIn("superado", msg)
        self.assertIn("Grasa", msg)  # both changes in one notification
        state, msg = goals.check_alerts(self.G, {"calories": 100}, state, "2026-11-17", t0 + timedelta(days=1))
        self.assertIsNone(msg)
        self.assertEqual(state["notified"], {})  # new day resets


class WeeklyCaloriesTest(unittest.TestCase):
    def test_allowance_moves_with_the_week(self):
        # Thursday (4 days left incl. today), daily goal 2000 → week 14000.
        light = goals.week_budget(2000, eaten_before_today=4800, eaten_today=0, days_left=4)
        self.assertEqual(light["today_allowance"], 2300)  # under-ate Mon-Wed → more today
        heavy = goals.week_budget(2000, eaten_before_today=7600, eaten_today=500, days_left=4)
        self.assertEqual(heavy["today_allowance"], 1600)
        self.assertEqual(heavy["week_remaining"], 5900)
        # Flex keeps 60% of 2000 for each of the 3 later days: 6400 - 3600 - 500.
        self.assertEqual(heavy["today_flex_max"], 2300)
        self.assertEqual(heavy["per_day_after_today"], 1967)

    def test_fallback_uses_weekly_slack(self):
        c = core.Catalog()
        c.upsert(food("juane", "Juane", 1, "meal", "meal", calories=499, protein=34, carbohydrates=33, fat=23))
        G = {"calories": 2000, "protein": 150, "carbohydrates": 250, "fat": 80}
        totals = {"calories": 1700, "protein": 60, "carbohydrates": 100, "fat": 30}
        basis = goals.meal_targets(G, totals, {"breakfast", "lunch"}, "dinner", {"calories": 900})
        self.assertEqual(basis["limits"]["calories"], 300)
        strict = c.recommend_within(basis["limits"])["items"][0]
        self.assertEqual(strict["servings"], 0.5)  # only half fits today's 300 kcal
        flexed = c.recommend_within(basis["flex_limits"])["items"][0]
        self.assertGreater(flexed["nutrients"]["calories"], 300)  # weekly slack allows more
        self.assertLessEqual(flexed["nutrients"]["calories"], 900)
        self.assertEqual(basis["flex_limits"]["fat"], basis["limits"]["fat"])  # macros stay daily


if __name__ == "__main__":
    unittest.main()
