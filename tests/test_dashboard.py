"""Generated examples must consistently point to the selected tracker user."""
import json
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch


class DashboardTest(unittest.TestCase):
    def test_combined_dashboard_scopes_every_card_and_includes_tdee_table(self):
        directory = Path(__file__).resolve().parents[1] / "dashboard"
        with patch.object(sys, "path", [str(directory), *sys.path]):
            builder = runpy.run_path(str(directory / "build_dashboard.py"))["dashboard"]
        definition = builder("alice")
        encoded = json.dumps(definition)
        self.assertNotIn("calorie_tracker_", encoded)
        self.assertIn("sensor.alice_selected_day", encoded)
        self.assertIn("sensor.alice_recipes_and_meals", encoded)
        self.assertIn("switch.alice_automatic_nutrition_goals", encoded)
        cards = [card for section in definition["views"][0]["sections"] for card in section["cards"]]
        table = next(card for card in cards if card.get("entities") == {"include": "sensor.alice_tdee_history"})
        self.assertEqual(table["type"], "custom:flex-table-card")
        columns = {column["name"]: column["modify"] for column in table["columns"]}
        self.assertIn("x.weight_change_kg", columns["Recorded change kg"])
        self.assertNotIn("tdee_weight_change_kg", json.dumps(columns))
        self.assertIn("sensor.calorie_tracker_selected_day", json.dumps(builder()))
        with self.assertRaises(ValueError):
            builder("alice.invalid")
