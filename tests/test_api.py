"""Run: pip install -r api/requirements.txt pytest httpx && pytest -q"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from app.main import create_app  # noqa: E402
from app.sessions import Sessions  # noqa: E402
from app.store import MemoryStore, clean  # noqa: E402

TOKEN = "t0ken"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    sessions = Sessions(str(tmp_path / "t.db"), clock=clock)
    app = create_app(store=MemoryStore(clock=clock), sessions=sessions, device_token=TOKEN)
    with TestClient(app) as c:
        yield c, clock


def send(c, **kw):
    reading = {"temp": 27.0, "hum": 60, "gas": 1500, "light": 2000, "dist": 50,
               "lid_closed": True, "locked": False, "state": "OK", **kw}
    return c.post(f"/api/data?token={TOKEN}", json=reading)


def test_token_required(env):
    c, _ = env
    assert c.get("/api/poll?token=nope").status_code == 401
    assert c.post("/api/data?token=", json={}).status_code == 401
    assert c.get(f"/api/poll?token={TOKEN}").json()["service"] == "ok"


def test_clean_drops_failed_reads():
    out = clean({"temp": -1, "hum": 55, "gas": "1600", "foo": 1, "state": "WARNING"})
    assert "temp" not in out["env"] and out["env"]["hum"] == 55.0 and out["env"]["gas"] == 1600
    assert out["state"] == {"state": "WARNING", "level": 1}


def test_data_latest_history(env):
    c, _ = env
    assert send(c, temp=25.5).json()["ok"]
    assert c.get("/api/latest").json()["temp"] == 25.5
    h = c.get("/api/history?field=temp&range=1h").json()
    assert h and h[-1]["v"] == 25.5
    assert c.get("/api/history?field=bogus").status_code == 400


def test_locked_session_full_flow(env):
    c, clock = env
    send(c, lid_closed=True)
    r = c.post("/api/session/start", json={"mode": "locked", "minutes": 25, "safeword": "banana"})
    assert r.status_code == 200 and r.json()["phase"] == "locking"
    assert c.get(f"/api/poll?token={TOKEN}").json()["locked"] is True
    # countdown starts only once the Pi reports the servo locked
    assert c.get("/api/session").json()["until"] is None
    clock.t += 3
    send(c, locked=True)
    s = c.get("/api/session").json()
    assert s["phase"] == "running" and s["until"] == clock.t + 25 * 60
    # wrong then right safeword
    assert c.post("/api/session/unlock", json={"safeword": "apple"}).status_code == 401
    assert c.post("/api/session/unlock", json={"safeword": "banana"}).status_code == 200
    assert c.get(f"/api/poll?token={TOKEN}").json()["locked"] is False
    events = [e["event"] for e in c.get("/api/session/log").json()]
    assert events == ["early", "wrong_safeword", "start"]


def test_lid_open_and_offline_block_start(env):
    c, clock = env
    body = {"mode": "locked", "minutes": 10, "safeword": "abc"}
    r = c.post("/api/session/start", json=body)
    assert r.status_code == 409 and r.json()["reason"] == "box_offline"
    send(c, lid_closed=False)
    r = c.post("/api/session/start", json=body)
    assert r.status_code == 409 and r.json()["reason"] == "lid_open"
    send(c, lid_closed=True)
    assert c.post("/api/session/start", json=body).status_code == 200


def test_timer_runs_out_and_lock_timeout(env):
    c, clock = env
    send(c)
    c.post("/api/session/start", json={"mode": "locked", "minutes": 1, "safeword": "abc"})
    clock.t += 31
    send(c)  # Pi still reports unlocked -> start_failed
    assert c.get("/api/session").json()["phase"] == "idle"
    c.post("/api/session/start", json={"mode": "locked", "minutes": 1, "safeword": "abc"})
    send(c, locked=True)
    clock.t += 61
    assert c.get(f"/api/poll?token={TOKEN}").json()["locked"] is False
    events = [e["event"] for e in c.get("/api/session/log").json()]
    assert events[:3] == ["finished", "start", "start_failed"]


def test_timer_mode_and_emergency(env):
    c, clock = env
    r = c.post("/api/session/start", json={"mode": "timer", "minutes": 5})
    assert r.status_code == 200 and r.json()["phase"] == "running"
    assert c.get(f"/api/poll?token={TOKEN}").json()["locked"] is False
    assert c.post("/api/session/emergency").status_code == 409
    assert c.post("/api/session/stop").status_code == 200
    send(c)
    c.post("/api/session/start", json={"mode": "locked", "minutes": 5, "safeword": "abc"})
    assert c.post("/api/session/emergency").status_code == 200
    assert c.get("/api/session/log").json()[0]["event"] == "emergency"


def test_concurrent_requests_dont_crash(env):
    from concurrent.futures import ThreadPoolExecutor

    c, _ = env
    send(c)
    calls = [lambda: c.get("/api/latest"), lambda: c.get("/api/session"),
             lambda: c.get(f"/api/poll?token={TOKEN}"), lambda: send(c),
             lambda: c.get("/api/session/log")] * 60
    with ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(lambda f: f().status_code, calls))
    assert set(codes) == {200}


def test_history_window_for_a_session(env):
    c, clock = env
    send(c, temp=24.0)
    now = clock.t
    h = c.get(f"/api/history?field=temp&start={now - 60}&end={now + 1}").json()
    assert h and h[-1]["v"] == 24.0
    assert c.get(f"/api/history?field=temp&start={now}&end={now - 5}").status_code == 400


def test_weekly_stats(env):
    c, clock = env
    c.post("/api/session/start", json={"mode": "timer", "minutes": 30})
    clock.t += 30 * 60 + 1
    c.get("/api/session")  # timer runs out -> finished
    c.post("/api/session/start", json={"mode": "timer", "minutes": 30})
    clock.t += 10 * 60
    c.post("/api/session/stop")
    w = c.get("/api/stats/week?tz_offset_min=420").json()
    assert len(w["days"]) == 7
    assert w["sessions"] == 2 and w["finished"] == 1 and w["early"] == 1
    assert abs(w["total_min"] - 40) < 0.2 and w["streak_days"] == 1 and w["completion_rate"] == 0.5
