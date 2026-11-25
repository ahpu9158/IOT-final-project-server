# Smart Desk Box: server

Web server, web app and time-series database for the Smart Desk Box (2110575 IoT term project).

- The **Raspberry Pi calls us**; the server never calls the Pi.
- **InfluxDB 2.7** stores sensor history (raw 30 days, 1-minute averages 1 year).
- **SQLite** stores the focus session and its log.
- **One web app** (FastAPI + Chart.js + three.js) for live data, history and focus sessions.

## Run

Needs Docker Desktop.

```bash
git clone https://github.com/ahpu9158/IOT-final-project-server.git
cd IOT-final-project-server
cp .env.example .env        # then change every value in .env
docker compose up -d --build
```

Open http://localhost:8000. InfluxDB's own UI is at http://localhost:8086 (user `admin`, password from `.env`).

No real box yet? Run the fake Pi in another terminal (Python 3, no packages needed):

```bash
python tools/fake_pi.py --url http://localhost:8000 --token <DEVICE_TOKEN from .env>
```

Press Enter in that terminal to open/close the fake phone lid.

## API for the gateway team

All Pi requests carry `?token=<DEVICE_TOKEN>`. A wrong token gets `401`.

| Request | When | Response |
|---|---|---|
| `POST /api/data?token=…` with JSON body | every 2–5 s | `{"ok":true,"saved":[…]}` |
| `GET /api/poll?token=…` | every 1–2 s | `{"service":"ok","locked":true,"until":1764150600.0}` |

Body for `/api/data` (send what you have; unknown fields are ignored, `-1` means "read failed" and is skipped):

```json
{"temp":27.1,"hum":61,"gas":1620,"light":2300,"dist":54.2,
 "lid_closed":true,"locked":true,"state":"OK"}
```

| Field | Sensor | Type |
|---|---|---|
| `temp`, `hum` | KY-015 (DHT11) | °C, % |
| `gas` | MQ135 | raw ADC 0–4095 |
| `light` | KY-018 | raw ADC 0–4095 |
| `dist` | HY-SRF05 | cm |
| `lid_closed` | TCRT5000 IR | bool, true only when the phone lid is fully closed |
| `locked` | SG90 servo | bool, current arm position |
| `state` | gateway rules | `OK` / `WARNING` / `NOT OK` |

**What the Pi does with `/api/poll`:** move the servo to match `locked`, but only lock once the IR sensor sees the lid closed. Report the real arm position back as `locked` in `/api/data`: a locked-mode session's countdown starts only when that turns true (if it doesn't within 30 s, the session is cancelled).

**Fail-safe (please implement on the Pi):** remember the last `until`. If it passes, or the server doesn't answer for a few minutes, unlock on your own, so the phone is never stuck.

## Focus sessions

| | Phone locked inside | Timer only |
|---|---|---|
| Start | lid must be closed and box online; set a safeword | starts at once |
| End early | safeword, or emergency button | Stop button |
| Lock | `locked=true` until the timer ends | never locks |

Every start and ending is logged (`start`, `finished`, `early`, `wrong_safeword`, `emergency`, `start_failed`) with planned and actual minutes.

## Web API (used by the web app)

| Endpoint | Purpose |
|---|---|
| `GET /api/latest` | newest reading + `pi_online` |
| `GET /api/history?field=temp&range=1h\|24h\|7d\|30d` | `[{"t":ISO,"v":number}]` |
| `GET /api/session` | session phase, countdown, lid and box status |
| `POST /api/session/start` | `{"mode":"locked","minutes":25,"safeword":"…"}` or `{"mode":"timer","minutes":25}`; `409` with `reason` `lid_open` / `box_offline` / `session_running` |
| `POST /api/session/unlock` | `{"safeword":"…"}`; `401` if wrong |
| `POST /api/session/emergency`, `POST /api/session/stop` | end a locked / timer session |
| `GET /api/session/log` | recent events |

## Develop without Docker

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows (source .venv/bin/activate on Mac/Linux)
pip install -r api/requirements.txt pytest httpx
pytest -q                                           # tests use in-memory storage
cd api && DEVICE_TOKEN=change-me SQLITE_PATH=dev.db uvicorn app.main:app --reload
```

Without `INFLUX_URL` the server keeps data in memory (lost on restart).

## Layout

```
docker-compose.yml   InfluxDB + api
api/app/main.py      routes
api/app/sessions.py  focus session logic (SQLite)
api/app/store.py     InfluxDB / in-memory storage
api/static/          web app; models/ = the box's 3D meshes from IoT_Design
tools/fake_pi.py     fake gateway for testing
tests/               pytest
```

The web app loads Chart.js, three.js and fonts from CDNs, so the browser needs internet access.
