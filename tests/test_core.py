"""Run: python3 -m unittest discover -s tests (no Home Assistant needed)."""

import importlib.util
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "calorie_tracker"
package = types.ModuleType("calorie_tracker")
package.__path__ = [str(ROOT)]
sys.modules["calorie_tracker"] = package
spec = importlib.util.spec_from_file_location("calorie_tracker.core", ROOT / "core.py")
core = importlib.util.module_from_spec(spec)
sys.modules["calorie_tracker.core"] = core
spec.loader.exec_module(core)


def food(food_id, name, amount, unit, kind="food", **values):
    return {"id": food_id, "source": "test", "kind": kind, "name": name, "brand": "", "serving_amount": amount,
            "serving_unit": unit, "servings": [], "ingredients": [], "nutrients": core.nutrients(values)}


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.c = core.Catalog()
        self.c.upsert(food("egg", "Clara de huevo", 100, "gramo", calories=51, protein=11, carbohydrates=0.5, fat=0.5))
        self.c.upsert(food("juane", "Juane", 1, "meal", "meal", calories=499, protein=34, carbohydrates=33, fat=23))

    def log(self, **kw):
        return self.c.log(day="2026-11-15", eaten_at=datetime(2026, 11, 15, 9), **kw)

    def test_quantity_and_spanish_meal(self):
        entry = self.log(meal="Desayuno", food_id="egg", quantity=250, unit="g")
        self.assertEqual((entry["meal"], entry["portion"]), ("breakfast", "250 gramo"))
        self.assertEqual(entry["nutrients"]["calories"], 127.5)
        self.assertEqual(self.log(meal="cena", food_id="egg", servings=1.5)["portion"], "150 gramo")
        with self.assertRaises(ValueError):
            self.log(meal="cena", food_id="egg", quantity=1, unit="cup")
        with self.assertRaises(ValueError):
            self.log(meal="brunch", food_id="egg")

    def test_recommend_any_targets(self):
        best = self.c.recommend({"calories": 500, "protein": 35})["items"][0]
        self.assertEqual((best["food_id"], best["match"]), ("juane", True))
        egg = self.c.recommend({"proteina": 22})["items"][0]
        self.assertEqual((egg["food_id"], egg["portion"]), ("egg", "200 gramo"))
        with self.assertRaises(ValueError):
            self.c.recommend({})

    def test_diary_totals_and_delete(self):
        a = self.log(meal="lunch", food_id="juane")
        self.log(meal="snack", name="Coffee", values={"calories": 60})
        self.assertEqual(self.c.day("2026-11-15")["totals"]["calories"], 559)
        self.assertEqual(self.c.search("", None, 0, 5)["items"][0]["id"], "juane")  # most used first
        with self.assertRaises(KeyError):
            self.c.delete([a["id"], "missing"])
        self.assertEqual(self.c.delete([a["id"]]), 1)
        self.assertEqual(self.c.range("2026-11-01", "2026-11-30")["logged_days"], 1)


if __name__ == "__main__":
    unittest.main()
