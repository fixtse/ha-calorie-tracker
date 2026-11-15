# Runs inside the HA container: creates (or updates) the storage dashboard over the websocket API.
import asyncio, json, os, sys, aiohttp
async def main():
    token = sys.stdin.readline().strip()
    config = json.load(open("/tmp/dashboard.json"))
    async with aiohttp.ClientSession() as s, s.ws_connect("http://127.0.0.1:8123/api/websocket") as ws:
        await ws.receive_json(); await ws.send_json({"type": "auth", "access_token": token})
        assert (await ws.receive_json())["type"] == "auth_ok"
        n = 0
        async def cmd(**msg):
            nonlocal n; n += 1
            await ws.send_json({"id": n, **msg})
            while True:
                r = await ws.receive_json()
                if r.get("id") == n: return r
        existing = (await cmd(type="lovelace/dashboards/list"))["result"]
        if not any(d["url_path"] == "calorie-tracker" for d in existing):
            r = await cmd(type="lovelace/dashboards/create", url_path="calorie-tracker", title="Calorías", icon="mdi:food-apple",
                          mode="storage", show_in_sidebar=True, require_admin=False)
            print("create:", r["success"], r.get("error"))
        r = await cmd(type="lovelace/config/save", url_path="calorie-tracker", config=config)
        print("save:", r["success"], r.get("error"))
asyncio.run(main())
