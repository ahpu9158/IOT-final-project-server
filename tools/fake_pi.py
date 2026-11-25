"""Pretends to be the Raspberry Pi gateway, so the server can be tested before the real box is ready.

  python tools/fake_pi.py --url http://localhost:8000 --token change-me

It POSTs a sensor reading every 2 s and polls for the lock state every 1 s, like the real Pi.
The fake servo follows the server, but only locks when the lid is closed (like the IR rule).
Press Enter to open/close the fake phone lid. Ctrl+C to stop. Standard library only.
"""
import argparse
import json
import math
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

p = argparse.ArgumentParser()
p.add_argument("--url", default="http://localhost:8000")
p.add_argument("--token", default="change-me")
p.add_argument("--lid-open", action="store_true", help="start with the phone lid open")
p.add_argument("--toggle-every", type=float, default=0,
               help="without a keyboard: open/close the lid every N seconds")
a = p.parse_args()

state = {"lid_closed": not a.lid_open, "locked": False, "want_locked": False}
q = "?" + urllib.parse.urlencode({"token": a.token})


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(a.url + path + q, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read() or b"null")


def servo():
    # lock only once the lid is fully closed; unlock any time
    if state["want_locked"] and state["lid_closed"] and not state["locked"]:
        state["locked"] = True
        print("  [servo] LOCKED")
    elif not state["want_locked"] and state["locked"]:
        state["locked"] = False
        print("  [servo] unlocked")


def poll_loop():
    while True:
        try:
            r = call("GET", "/api/poll")
            state["want_locked"] = r["locked"]
            servo()
        except (urllib.error.URLError, OSError) as e:
            print("  poll failed:", e)
        time.sleep(1)


def data_loop():
    t0 = time.time()
    while True:
        t = time.time() - t0
        temp = 27 + 2 * math.sin(t / 120) + random.uniform(-0.2, 0.2)
        hum = 60 + 8 * math.sin(t / 200) + random.uniform(-1, 1)
        gas = int(1500 + 300 * max(0, math.sin(t / 90)) + random.uniform(-30, 30))
        level = 0 if gas < 1700 else 1
        reading = {
            "temp": round(temp, 1), "hum": round(hum, 1), "gas": gas,
            "light": int(2300 + random.uniform(-80, 80)),
            "dist": round(60 + random.uniform(-5, 5), 1),
            "lid_closed": state["lid_closed"], "locked": state["locked"],
            "state": ["OK", "WARNING", "NOT OK"][level],
        }
        try:
            call("POST", "/api/data", reading)
            print(f"sent temp={reading['temp']} hum={reading['hum']} gas={gas} "
                  f"lid={'closed' if state['lid_closed'] else 'OPEN'} locked={state['locked']}")
        except (urllib.error.URLError, OSError) as e:
            print("  send failed:", e)
        time.sleep(2)


threading.Thread(target=poll_loop, daemon=True).start()
threading.Thread(target=data_loop, daemon=True).start()
def toggle_lid():
    if state["locked"]:
        print("  lid is locked, can't open it")
        return
    state["lid_closed"] = not state["lid_closed"]
    print("  lid now", "CLOSED" if state["lid_closed"] else "OPEN")
    servo()


print("Fake Pi running. Press Enter to open/close the phone lid.")
try:
    while True:
        try:
            input()
        except EOFError:  # no keyboard (e.g. run in the background): keep sending data
            if a.toggle_every:
                while True:
                    time.sleep(a.toggle_every)
                    toggle_lid()
            while True:
                time.sleep(3600)
        toggle_lid()
except KeyboardInterrupt:
    pass
