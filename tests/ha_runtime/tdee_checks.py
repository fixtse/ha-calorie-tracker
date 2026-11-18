"""Exercise the adapter against actual HA registries, entities, flows and storage."""
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util


async def verify_tdee(hass, alice, bob, module):
    registry = er.async_get(hass)
    profiles = []
    sensors = []
    for name in ("body_alice", "body_bob"):
        entry = ConfigEntry(version=1, minor_version=1, domain="bodymiscale", title=name,
                            data={"name": name}, options={}, source="user", unique_id=None,
                            discovery_keys={}, subentries_data=[])
        # Test the public registry contract without installing or running a physical scale.
        with patch.object(hass.config_entries, "async_setup", new=AsyncMock(return_value=True)):
            await hass.config_entries.async_add(entry)
        profile = {}
        for key in ("weight", "body_fat", "last_measurement_time"):
            profile[key] = registry.async_get_or_create(
                "sensor", "bodymiscale", f"bodymiscale_{name}_{key}", config_entry=entry,
                suggested_object_id=f"test_{name}_{key}").entity_id
        profiles.append(entry)
        sensors.append(profile)
    now = dt_util.utcnow()
    for tracker_entry, body_entry, source, kg, fat in ((alice, profiles[0], sensors[0], 80, 15),
                                                       (bob, profiles[1], sensors[1], 90, 30)):
        hass.states.async_set(source["weight"], kg, {"unit_of_measurement": "kg"})
        hass.states.async_set(source["body_fat"], fat, {"unit_of_measurement": "%"})
        hass.states.async_set(source["last_measurement_time"], (now - timedelta(hours=2)).isoformat())
        hass.config_entries.async_update_entry(tracker_entry, options={
            **tracker_entry.options, "tdee_enabled": True, "tdee_bodymiscale_entry": body_entry.entry_id})
        tracker_entry.runtime_data.adaptive.invalidate()
        await tracker_entry.runtime_data.adaptive.setup()
    async def call(service, **data):
        return await hass.services.async_call("calorie_tracker", service, data, blocking=True, return_response=True)
    summary = (await call("get_tdee", user_id=alice.entry_id))["summary"]
    assert summary["initial_weight_kg"] == 80 and summary["initial_body_fat"] == 15, summary
    assert (await call("get_tdee", user_id=bob.entry_id))["summary"]["initial_weight_kg"] == 90
    # Publishing old values after a restart/idle advertisement cannot update today's weight.
    hass.states.async_set(sensors[0]["weight"], 81, {"unit_of_measurement": "kg"})
    await alice.runtime_data.adaptive.capture()
    assert alice.runtime_data.adaptive.result()["summary"]["latest_weight_kg"] == 80
    hass.states.async_set(sensors[0]["last_measurement_time"], (now - timedelta(hours=1)).isoformat())
    await alice.runtime_data.adaptive.capture()
    assert alice.runtime_data.adaptive.result()["summary"]["latest_weight_kg"] == 81
    assert len(alice.runtime_data.adaptive.state["weights"]) == 1
    assert bob.runtime_data.adaptive.result()["summary"]["latest_weight_kg"] == 90
    # Registry identity continues to resolve after an entity rename.
    renamed = "sensor.renamed_body_alice_weight"
    registry.async_update_entity(sensors[0]["weight"], new_entity_id=renamed)
    assert alice.runtime_data.adaptive.entities()["weight"] == renamed
    # Actual HA controls must update targets immediately without changing the fixed baseline.
    automatic = registry.async_get_entity_id("switch", "calorie_tracker", alice.runtime_data.entity_id("tdee_auto_goals"))
    goal = registry.async_get_entity_id("number", "calorie_tracker", alice.runtime_data.entity_id("weekly_weight_change_goal"))
    await hass.services.async_call("switch", "turn_on", {"entity_id": automatic}, blocking=True)
    before = (await call("get_goals", user_id=alice.entry_id))["goals"]["calories"]
    await hass.services.async_call("number", "set_value", {"entity_id": goal, "value": .25}, blocking=True)
    after = (await call("get_goals", user_id=alice.entry_id))["goals"]["calories"]
    assert abs(after - before - 275) < .01, (before, after)
    assert alice.runtime_data.adaptive.result()["summary"]["initial_weight_kg"] == 80
    assert not bob.runtime_data.adaptive.result()["summary"]["auto_goals"]
    await call("set_goals", user_id=alice.entry_id, protein=130)
    assert not alice.runtime_data.adaptive.result()["summary"]["auto_goals"]
    assert (await call("get_goals", user_id=alice.entry_id))["goals"]["protein"] == 130
    restored = module.Tracker(hass, alice)
    await restored.load()
    assert len(restored.adaptive.state["weights"]) == 1
    assert restored.adaptive.settings["tdee_weekly_goal"] == .25
    assert not restored.adaptive.settings["tdee_auto_goals"]
    assert len((await call("get_tdee", user_id=alice.entry_id, kind="days", limit=1))["items"]) == 1
    # The supplied dashboard uses native HA markdown templates and these entity names.
    import importlib.util
    from pathlib import Path
    from homeassistant.helpers.template import Template
    spec = importlib.util.spec_from_file_location("tdee_dashboard", Path(__file__).resolve().parents[2] / "dashboard/build_tdee_dashboard.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    await hass.async_block_till_done()
    definition = builder.dashboard("alice")
    weekly_table = definition["views"][0]["cards"][-1]
    assert weekly_table["type"] == "custom:flex-table-card"
    assert all(column["data"] == "weeks" for column in weekly_table["columns"])
    for card in definition["views"][0]["cards"]:
        if card["type"] == "markdown":
            rendered = Template(card["content"], hass).async_render()
            assert isinstance(rendered, str) and rendered.strip()
    # Missing sources must be rejected through the actual options UI.
    flow = await hass.config_entries.options.async_init(alice.entry_id)
    assert flow["type"] == "menu", flow
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "tdee"})
    assert flow["type"] == "form", flow
    rejected = await hass.config_entries.options.async_configure(flow["flow_id"], {"tdee_enabled": True, "tdee_bodymiscale_entry": ""})
    assert rejected["errors"]["base"] == "missing_weight_source", rejected
    hass.config_entries.async_update_entry(alice, options={**alice.options, "history_start": "2026-01-01"})
    saved = await hass.config_entries.options.async_configure(flow["flow_id"], {
        "tdee_enabled": True, "tdee_auto_goals": False,
        "tdee_bodymiscale_entry": profiles[0].entry_id, "tdee_weekly_goal": -.15})
    assert saved["type"] == "create_entry", saved
    await hass.async_block_till_done()
    assert alice.options["history_start"] == "2026-01-01"
    assert alice.runtime_data.adaptive.settings["tdee_weekly_goal"] == -.15
    assert len(alice.runtime_data.adaptive.state["weights"]) == 1
    for tracker_entry in (alice, bob):
        tracker_entry.runtime_data.adaptive.close()
    print("Adaptive TDEE: BodyMiScale user mapping, timestamp dedupe, controls, persistence and options passed")
