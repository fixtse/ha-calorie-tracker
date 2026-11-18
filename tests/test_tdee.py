"""Synthetic formula fixtures; never commit the user's workbook or measurements."""
import importlib.util
from pathlib import Path
import unittest
from datetime import date, timedelta

spec = importlib.util.spec_from_file_location("tdee_model", Path(__file__).resolve().parents[1] /
                                            "custom_components/calorie_tracker/tdee.py")
tdee = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tdee)


class TDEETest(unittest.TestCase):
    def test_initial_formula_macro_bands_and_goal_changes(self):
        settings = {"tdee_enabled": True, "tdee_initial_weight": 80, "tdee_initial_body_fat": 15,
                    "tdee_start_date": "2026-01-01", "tdee_weekly_goal": -.25}
        result = tdee.calculate(settings, {}, [], "2026-01-01")["summary"]
        expected = (370 + 21.6 * 80 * .85) * 1.5
        self.assertAlmostEqual(result["tdee"], expected)
        self.assertAlmostEqual(result["targets"]["calories"], expected - 275)
        self.assertEqual(result["targets"]["protein"], 176)
        self.assertAlmostEqual(result["targets"]["fat"], (expected - 275) * .25 / 9)
        for fat, protein_factor, fat_factor in ((19.99, 2.2, .25), (20, 1.76, .25),
                                                (25, 1.76, .25), (25.01, 1.606, .25)):
            targets, ranges = tdee.macros(expected, 80, fat, 0)
            self.assertAlmostEqual(targets["protein"], 80 * protein_factor)
            self.assertAlmostEqual(targets["fat"], expected * fat_factor / 9)
            energy = targets["protein"] * 4 + targets["fat"] * 9 + targets["carbohydrates"] * 4
            self.assertAlmostEqual(energy, expected)
        settings["tdee_weekly_goal"] = .5
        changed = tdee.calculate(settings, {}, [], "2026-01-01")["summary"]
        self.assertEqual(changed["tdee"], result["tdee"])
        self.assertAlmostEqual(changed["targets"]["calories"] - result["targets"]["calories"], 825)

    def history(self):
        beginning = date(2026, 1, 1)
        settings = {"tdee_enabled": True, "tdee_initial_weight": 80, "tdee_initial_body_fat": 20,
                    "tdee_start_date": beginning.isoformat()}
        state = {"weights": {}}
        entries = []
        # Weekly means: 80, 79.8, 79.6, 79.4. Complete weeks only.
        for index in range(28):
            day = (beginning + timedelta(days=index)).isoformat()
            state["weights"][day] = {"kg": 80 - (index // 7) * .2}
            entries.append({"day": day, "nutrients": {"calories": 2200}})
        return settings, state, entries

    def test_week_four_uses_completed_weeks_and_ignores_first_week(self):
        settings, state, entries = self.history()
        before = tdee.calculate(settings, state, entries, "2026-01-21")["summary"]
        self.assertEqual(before["status"], "initial_estimate")
        result = tdee.calculate(settings, state, entries, "2026-01-22")
        self.assertEqual(result["summary"]["status"], "adaptive")
        self.assertEqual(result["summary"]["calibration_weeks"], 2)
        self.assertAlmostEqual(result["weeks"][0]["smoothed_tdee"], 2200)
        self.assertAlmostEqual(result["summary"]["tdee"], 2200 + .2 * tdee.KCAL_PER_KG / 7)
        # Today is deliberately extreme: a partial week must not change the headline estimate.
        entries[-7]["nutrients"]["calories"] = 9000
        self.assertAlmostEqual(tdee.calculate(settings, state, entries, "2026-01-22")["summary"]["tdee"],
                               result["summary"]["tdee"])

    def test_display_weight_change_uses_recorded_weights_not_filled_helpers(self):
        settings, state, entries = self.history()
        for offset, kg in enumerate((79.9, 79.7, 79.5, 79.7, 79.8)):
            state["weights"][f"2026-01-{8 + offset:02d}"] = {"kg": kg}
        del state["weights"]["2026-01-13"]
        del state["weights"]["2026-01-14"]
        week = tdee.calculate(settings, state, entries, "2026-01-15")["weeks"][1]
        self.assertEqual(week["weight_days"], 5)
        self.assertAlmostEqual(week["average_weight_kg"], 79.72)
        self.assertAlmostEqual(week["weight_change_kg"], -.28)
        filled = (79.9 + 79.7 + 79.5 + 79.7 + 79.8 * 3) / 7
        self.assertAlmostEqual(week["tdee_average_weight_kg"], filled)
        self.assertAlmostEqual(week["tdee_weight_change_kg"], filled - 80)
        self.assertAlmostEqual(week["weekly_tdee"], 2200 - (filled - 80) * tdee.KCAL_PER_KG / 7)

    def test_missing_diary_day_and_missing_weight_week_are_not_zero(self):
        settings, state, entries = self.history()
        entries.pop(8)
        result = tdee.calculate(settings, state, entries, "2026-01-22")
        self.assertEqual(result["summary"]["status"], "adaptive")
        self.assertEqual(result["weeks"][1]["calorie_days"], 6)
        self.assertIsNotNone(result["weeks"][1]["weekly_tdee"])
        for day in list(state["weights"])[7:14]:
            del state["weights"][day]
        result = tdee.calculate(settings, state, entries, "2026-01-22")
        self.assertIsNone(result["weeks"][1]["average_weight_kg"])
        self.assertIsNone(result["weeks"][2]["weekly_tdee"])
        self.assertIsNone(next(day for day in result["days"] if day["day"] == "2026-01-09")["calories"])

    def test_disabled_missing_stats_future_and_invalid_targets(self):
        self.assertEqual(tdee.calculate({}, {}, [], "2026-01-01")["summary"]["status"], "disabled")
        self.assertEqual(tdee.calculate({"tdee_enabled": True}, {}, [], "2026-01-01")["summary"]["status"], "waiting_for_body_stats")
        settings, state, entries = self.history()
        self.assertEqual(tdee.calculate(settings, state, entries, "2025-12-01")["summary"]["status"], "waiting_for_start_date")
        settings["tdee_weekly_goal"] = -10
        self.assertEqual(tdee.calculate(settings, state, entries, "2026-01-22")["summary"]["status"], "invalid_targets")
        self.assertIsNone(tdee.calculate(settings, state, entries, "2026-01-22")["summary"]["targets"])

    def test_weight_units_and_unavailable_states(self):
        self.assertAlmostEqual(tdee.kilograms(176.3698097, "lb"), 80, places=5)
        self.assertEqual(tdee.kilograms(80000, "g"), 80)
        for value, unit in (("unavailable", "kg"), ("unknown", "kg"), ("nan", "kg"), ("inf", "kg"), (-1, "kg"), (80, "unknown")):
            self.assertIsNone(tdee.kilograms(value, unit))

    def test_logged_zero_is_valid_and_unknown_calories_count_as_missing_days(self):
        settings, state, entries = self.history()
        entries[8]["nutrients"]["calories"] = 0
        result = tdee.calculate(settings, state, entries, "2026-01-22")
        self.assertEqual(result["weeks"][1]["calorie_days"], 7)
        self.assertIsNotNone(result["weeks"][1]["weekly_tdee"])
        entries[8]["nutrients"]["calories"] = None
        result = tdee.calculate(settings, state, entries, "2026-01-22")
        self.assertEqual(result["weeks"][1]["calorie_days"], 6)
        self.assertIsNotNone(result["weeks"][1]["weekly_tdee"])
        for index in (9, 10, 11):
            entries[index]["nutrients"]["calories"] = None
        result = tdee.calculate(settings, state, entries, "2026-01-22")
        self.assertEqual(result["weeks"][1]["calorie_days"], 3)
        self.assertIsNone(result["weeks"][1]["weekly_tdee"])

    def test_up_to_three_missing_calorie_days_are_allowed(self):
        for missing in range(8):
            with self.subTest(missing=missing):
                settings, state, entries = self.history()
                absent = {entry["day"] for entry in entries[7:7 + missing]}
                entries = [entry for entry in entries if entry["day"] not in absent]
                result = tdee.calculate(settings, state, entries, "2026-01-22")
                week = result["weeks"][1]
                self.assertEqual(week["calorie_days"], 7 - missing)
                self.assertEqual(week["eligible"], missing <= 3)
                if missing <= 3:
                    self.assertAlmostEqual(week["average_calories"], 2200)
                    self.assertAlmostEqual(week["weekly_tdee"], 2200 + .2 * tdee.KCAL_PER_KG / 7)
                    self.assertEqual(result["summary"]["status"], "adaptive")
                else:
                    self.assertIsNone(week["weekly_tdee"])
                    self.assertEqual(result["summary"]["status"], "initial_estimate")

    def test_four_available_days_use_their_mean_not_seven_day_zero_fill(self):
        settings, state, entries = self.history()
        entries = entries[:7] + entries[10:]
        for entry, calories in zip(entries[7:11], (1800, 2000, 2400, 2600)):
            entry["nutrients"]["calories"] = calories
        week = tdee.calculate(settings, state, entries, "2026-01-22")["weeks"][1]
        self.assertEqual(week["calorie_days"], 4)
        self.assertAlmostEqual(week["average_calories"], 2200)
        self.assertAlmostEqual(week["weekly_tdee"], 2200 + .2 * tdee.KCAL_PER_KG / 7)
        # Four logged days in an unfinished week must still wait for the week boundary.
        current = tdee.calculate(settings, state, entries, "2026-01-25")["weeks"][3]
        self.assertEqual(current["calorie_days"], 4)
        self.assertFalse(current["complete"])
        self.assertFalse(current["eligible"])
