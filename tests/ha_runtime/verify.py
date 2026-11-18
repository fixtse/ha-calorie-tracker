"""Real HA multi-user isolation smoke check. Never uses a live HA instance."""
import asyncio
import importlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace

from homeassistant.bootstrap import async_setup_hass
from homeassistant.runner import RuntimeConfig
from homeassistant.setup import async_setup_component
from homeassistant.helpers import entity_registry as er
from homeassistant.exceptions import ServiceValidationError


async def main():
    with tempfile.TemporaryDirectory(prefix="calorie-users-ha-") as directory:
        components = Path(directory, "custom_components")
        components.mkdir()
        components.joinpath("__init__.py").touch()
        shutil.copytree(Path(__file__).resolve().parents[2] / "custom_components/calorie_tracker",
                        components / "calorie_tracker")
        Path(directory, "configuration.yaml").write_text(
            "homeassistant:\n  name: Calorie isolation test\n  latitude: 0\n  longitude: 0\n"
            "  elevation: 0\n  unit_system: metric\n  time_zone: UTC\n  currency: EUR\n  country: ES\n")
        hass = await async_setup_hass(RuntimeConfig(config_dir=directory))
        assert hass is not None
        try:
            assert await async_setup_component(hass, "calorie_tracker", {})
            module = importlib.import_module("custom_components.calorie_tracker")
            # The old installation keeps its original storage key and unique IDs.
            legacy_entry = SimpleNamespace(data={}, options={}, entry_id="legacy", title="Calorie Tracker")
            legacy = module.Tracker(hass, legacy_entry)
            legacy.catalog.save_manual("Legacy food", "", 1, "serving", {"calories": 123})
            await legacy.save()
            assert legacy.entity_id("calories_today") == "calorie_tracker_calories_today"
            assert legacy.identity == "tracker"
            entries = []
            for name in ("Alice", "Bob"):
                flow = await hass.config_entries.flow.async_init("calorie_tracker", context={"source": "user"})
                assert flow["type"] == "form", flow
                result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"name": name})
                assert result["type"] == "create_entry", result
                entry = result["result"]
                await hass.async_block_till_done()
                entries.append(entry)
            alice, bob = entries
            assert alice.runtime_data.identity != bob.runtime_data.identity
            assert alice.data["storage_key"] != bob.data["storage_key"]
            async def call(service, **data):
                return await hass.services.async_call("calorie_tracker", service, data, blocking=True, return_response=True)
            page = await call("list_users", search="ali", offset=0, limit=1)
            assert page == {"items": [{"id": alice.entry_id, "name": "Alice"}], "total": 1}, page
            assert (await call("list_users", id=bob.entry_id))["items"][0]["name"] == "Bob"
            assert (await call("list_users", offset=1, limit=1))["items"][0]["id"] == bob.entry_id
            assert (await call("list_users", id="unknown"))["total"] == 0
            # Same catalog ID and name must still resolve to separate data.
            for entry, calories in ((alice, 200), (bob, 400)):
                saved = await call("save_food", user_id=entry.entry_id, name="Soup", kind="recipe",
                                   ingredients=[f"{calories} g beans"], instructions=["Simmer."],
                                   recipe_servings=4, calories=calories)
                food_id = saved["food"]["id"]
                details = await call("get_food", user_id=entry.entry_id, food_id=food_id)
                assert details["instructions"] == ["Simmer."]
                assert details["nutrients_per_serving"]["calories"] == calories
                await call("log_food", user_id=entry.entry_id, food_id=food_id, meal="lunch", servings=2)
                diary = await call("get_diary", user_id=entry.entry_id)
                assert diary["totals"]["calories"] == 2 * calories
                await call("set_goals", user_id=entry.entry_id, protein=calories / 2)
                assert (await call("get_goals", user_id=entry.entry_id))["goals"]["protein"] == calories / 2
            alice_diary = await call("get_diary", user_id=alice.entry_id)
            bob_diary = await call("get_diary", user_id=bob.entry_id)
            await call("delete_entries", user_id=bob.entry_id, entry_ids=[alice_diary["entries"][0]["entry_id"]])
            assert (await call("get_diary", user_id=alice.entry_id))["totals"]["calories"] == 400
            assert (await call("get_diary", user_id=bob.entry_id))["totals"]["calories"] == 800
            for service, data in (("get_diary", {}), ("save_food", {"name": "Unsafe", "calories": 1}),
                                  ("get_goals", {"user_id": "unknown"})):
                try:
                    await call(service, **data)
                except ServiceValidationError:
                    pass
                else:
                    raise AssertionError(f"Unscoped/unknown user accepted: {service}")
            registry = er.async_get(hass)
            ids = [entity.unique_id for entity in registry.entities.values() if entity.platform == "calorie_tracker"]
            assert len(ids) >= 30 and len(set(ids)) == len(ids), ids
            for entry, calories in ((alice, 400), (bob, 800)):
                restored = module.Tracker(hass, entry)
                await restored.load()
                assert restored.catalog.day(restored.today())["totals"]["calories"] == calories
            restored_legacy = module.Tracker(hass, legacy_entry)
            await restored_legacy.load()
            assert restored_legacy.catalog.search("Legacy", None, 0, 10)["total"] == 1
            assert (await call("search_foods", user_id=alice.entry_id, query="Legacy"))["total"] == 0
            from tdee_checks import verify_tdee
            await verify_tdee(hass, alice, bob, module)
            # Legacy single-user calls still work with exactly one configured user.
            assert await hass.config_entries.async_unload(bob.entry_id)
            try:
                await call("get_diary")
            except ServiceValidationError:
                pass
            else:
                raise AssertionError("Unloaded user must not enable an ambiguous default")
            await hass.config_entries.async_remove(bob.entry_id)
            assert (await call("get_diary"))["totals"]["calories"] == 400
            print("Real HA multi-user flows, services, storage and entity isolation passed")
        finally:
            await hass.async_stop()


asyncio.run(main())
