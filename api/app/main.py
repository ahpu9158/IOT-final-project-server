"""Smart Desk Box server.

Pi API (the Raspberry Pi calls these; we never call the Pi):
  POST /api/data?token=...   sensor reading (JSON)
  GET  /api/poll?token=...   -> {"service":"ok","locked":bool,"until":epoch|null}

Web API (our own web app):
  GET  /api/latest, /api/history, /api/session, /api/session/log
  POST /api/session/start, /unlock, /emergency, /stop
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .sessions import SessionError, Sessions
from .store import MEASUREMENTS, NUMERIC_FIELDS, RANGES, InfluxStore, MemoryStore, clean

log = logging.getLogger("smartdesk")
STATIC = Path(__file__).resolve().parent.parent / "static"


class StartBody(BaseModel):
    mode: str
    minutes: int
    safeword: str | None = None


class SafewordBody(BaseModel):
    safeword: str


def make_store():
    url = os.environ.get("INFLUX_URL")
    if not url:
        log.warning("INFLUX_URL not set: using in-memory storage (data is lost on restart)")
        return MemoryStore()
    store = InfluxStore(
        url,
        os.environ["INFLUX_TOKEN"],
        os.environ.get("INFLUX_ORG", "cedt"),
        os.environ.get("INFLUX_BUCKET", "desk_raw"),
        os.environ.get("INFLUX_BUCKET_1M", "desk_1m"),
    )
    for attempt in range(30):  # InfluxDB may still be starting
        try:
            store.setup()
            break
        except Exception as e:  # noqa: BLE001
            log.warning("InfluxDB not ready (%s), retrying", e)
            time.sleep(2)
    return store


def create_app(store=None, sessions: Sessions | None = None, device_token: str | None = None) -> FastAPI:
    token = device_token or os.environ.get("DEVICE_TOKEN", "")
    if not token or token == "change-me":
        log.warning("DEVICE_TOKEN is not set to a real value")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store or make_store()
        app.state.sessions = sessions or Sessions(os.environ.get("SQLITE_PATH", "/data/app.db"))
        yield
        app.state.store.close()

    app = FastAPI(title="Smart Desk Box server", lifespan=lifespan)

    @app.exception_handler(SessionError)
    async def _session_error(_, e: SessionError):
        return JSONResponse({"ok": False, "reason": e.reason}, status_code=e.status)

    def check(t: str):
        if not token or not secrets.compare_digest(t.encode(), token.encode()):
            raise HTTPException(401, "bad token")

    # ---------------- Pi API ----------------
    @app.post("/api/data")
    async def data(req: Request, token: str = Query("")):
        check(token)
        try:
            body = await req.json()
        except ValueError:
            raise HTTPException(400, "body must be JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be a JSON object")
        parts = clean(body)
        flat = {k: v for vals in parts.values() for k, v in vals.items()}
        now = time.time()
        try:
            app.state.store.write(now, parts)
        except Exception as e:  # noqa: BLE001 - keep the Pi's lock loop alive even if the DB hiccups
            log.error("write failed: %s", e)
        app.state.sessions.on_data(flat)
        return {"ok": True, "saved": sorted(flat)}

    @app.get("/api/poll")
    def poll(token: str = Query("")):
        check(token)
        return app.state.sessions.poll()

    # ---------------- Web API ----------------
    @app.get("/api/latest")
    def latest():
        s = app.state.sessions
        return {**s.latest, "pi_online": s.pi_online()}

    @app.get("/api/history")
    def history(field: str, range: str = "24h"):
        if range not in RANGES:
            raise HTTPException(400, f"range must be one of {list(RANGES)}")
        if field not in NUMERIC_FIELDS and field not in ("lid_closed", "locked"):
            raise HTTPException(400, "unknown field")
        return app.state.store.history(field, range)

    @app.get("/api/fields")
    def fields():
        return {m: list(f) for m, f in MEASUREMENTS.items()}

    @app.get("/api/session")
    def session():
        return app.state.sessions.state()

    @app.post("/api/session/start")
    def start(b: StartBody):
        app.state.sessions.start(b.mode, b.minutes, b.safeword)
        return {"ok": True, **app.state.sessions.state()}

    @app.post("/api/session/unlock")
    def unlock(b: SafewordBody):
        app.state.sessions.unlock(b.safeword)
        return {"ok": True}

    @app.post("/api/session/emergency")
    def emergency():
        app.state.sessions.emergency()
        return {"ok": True}

    @app.post("/api/session/stop")
    def stop():
        app.state.sessions.stop()
        return {"ok": True}

    @app.get("/api/session/log")
    def session_log(limit: int = 30):
        return app.state.sessions.log(min(limit, 200))

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


app = create_app()
