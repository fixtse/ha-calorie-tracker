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


    def test_update_entry_recomputes_and_overrides(self):
        entry = self.log(meal="cena", food_id="egg", quantity=100, unit="g")
        changed = self.c.update_entry(entry["id"], quantity=200, unit="g", meal="almuerzo")
        self.assertEqual((changed["meal"], changed["nutrients"]["calories"], changed["portion"]), ("lunch", 102, "200 gramo"))
        changed = self.c.update_entry(entry["id"], values={"protein": 30}, eaten_at=datetime(2026, 11, 14, 20))
        self.assertEqual((changed["nutrients"]["protein"], changed["nutrients"]["calories"], changed["day"]), (30, 102, "2026-11-14"))
        manual = self.log(meal="snack", name="Fruta", values={"calories": 80})
        with self.assertRaises(ValueError):
            self.c.update_entry(manual["id"], servings=2)
        with self.assertRaises(KeyError):
            self.c.update_entry("nope", meal="cena")

    def test_edited_import_survives_resync(self):
        imported = {"id": "mfp:1", "food_id": None, "name": "Pan", "meal": "breakfast", "portion": "1",
                    "eaten_at": "2026-11-15T12:00:00", "nutrients": core.nutrients({"calories": 100})}
        self.c.replace_imported("2026-11-15", "myfitnesspal", [imported])
        self.c.update_entry("mfp:1", values={"calories": 150})
        self.c.replace_imported("2026-11-15", "myfitnesspal", [imported])
        kept = [e for e in self.c.entries if e["id"] == "mfp:1"]
        self.assertEqual([e["nutrients"]["calories"] for e in kept], [150])

    def test_copy_day(self):
        self.log(meal="desayuno", food_id="egg", quantity=100, unit="g")
        self.log(meal="cena", food_id="juane", servings=1)
        copies = self.c.copy_day("2026-11-15", "2026-11-16", meals=["cena"])
        self.assertEqual([(e["name"], e["day"], e["eaten_at"]) for e in copies], [("Juane", "2026-11-16", "2026-11-16T09:00:00")])
        self.assertEqual(self.c.day("2026-11-16")["totals"]["calories"], 499)
        with self.assertRaises(ValueError):
            self.c.copy_day("2026-01-01", "2026-11-16")


    def test_exact_weights_convert_between_units(self):
        c = self.c
        for qty, unit, kcal in ((30, "g", 15.3), (50, "gramos", 25.5), (0.1, "kg", 51.0), (1, "oz", 14.5)):
            entry = c.log(day="2026-11-15", eaten_at=datetime(2026, 11, 15, 9), meal="snack",
                          food_id="egg", quantity=qty, unit=unit)
            self.assertAlmostEqual(entry["nutrients"]["calories"], kcal, delta=0.1, msg=f"{qty} {unit}")
        with self.assertRaises(ValueError) as error:
            c.log(day="2026-11-15", eaten_at=datetime(2026, 11, 15, 9), meal="snack", food_id="juane", quantity=30, unit="g")
        self.assertIn("no weight", str(error.exception))


if __name__ == "__main__":
    unittest.main()

class RecipeTest(unittest.TestCase):
    def test_recipe_persists_and_is_reusable(self):
        catalog = core.Catalog()
        plain = catalog.save_manual("Soup", "", 1, "serving", {"calories": 50})
        recipe = catalog.save_manual("Soup", "", 1, "serving", {"calories": 200},
                                     kind="recipe", ingredients=["400 g beans", "500 ml water"],
                                     instructions=["Simmer for 20 minutes."], recipe_servings=4)
        self.assertNotEqual(plain["id"], recipe["id"])
        restored = core.Catalog.load(catalog.dump())
        self.assertEqual(restored.search("Soup", "recipe", 0, 10)["total"], 1)
        details = restored.food(recipe["id"])
        self.assertEqual(details["ingredients"], ["400 g beans", "500 ml water"])
        self.assertEqual(details["instructions"], ["Simmer for 20 minutes."])
        self.assertEqual(details["recipe_servings"], 4)
        entry = restored.log(day="2026-11-18", eaten_at=datetime(2026, 11, 18, 12),
                             meal="lunch", food_id=recipe["id"], servings=2)
        self.assertEqual(entry["nutrients"]["calories"], 400)
        updated = restored.save_manual("Soup", "", 1, "serving", {"calories": 180},
                                       kind="recipe", ingredients=["300 g beans"],
                                       instructions=["Boil."], recipe_servings=3)
        self.assertEqual(updated["id"], recipe["id"])
        self.assertEqual(restored.search("Soup", "recipe", 0, 10)["total"], 1)
