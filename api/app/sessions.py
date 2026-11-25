"""Focus sessions: the lock state the Pi follows, kept in SQLite so it survives restarts.

Modes:
  locked - phone inside, servo locks until the timer ends; early unlock needs the safeword,
           emergency always works. Lid must be closed to start.
  timer  - countdown only; nothing is locked.
"""
from __future__ import annotations

import sqlite3
import threading
import time

import bcrypt

from .clock import now as clock_now

START_CONFIRM_TIMEOUT = 30  # s to wait for the Pi to report locked=true
PI_FRESH = 10               # s; older readings count as "box offline"


class SessionError(Exception):
    def __init__(self, status: int, reason: str):
        self.status, self.reason = status, reason


class Sessions:
    def __init__(self, path: str, clock=clock_now):
        self.clock = clock
        self.lock = threading.RLock()  # one SQLite connection shared by all request threads
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS session (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              mode TEXT, locked INTEGER NOT NULL DEFAULT 0,
              requested REAL, started REAL, until REAL,
              planned_min INTEGER, safeword_hash BLOB
            );
            INSERT OR IGNORE INTO session (id) VALUES (1);
            CREATE TABLE IF NOT EXISTS session_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts REAL NOT NULL, mode TEXT, event TEXT NOT NULL,
              planned_min INTEGER, actual_min REAL
            );
            CREATE TABLE IF NOT EXISTS gateway (
              id INTEGER PRIMARY KEY CHECK (id = 1), last_poll REAL, last_data REAL
            );
            INSERT OR IGNORE INTO gateway (id) VALUES (1);
            """
        )
        self.db.commit()
        # latest reading from the Pi (in memory; refilled within seconds after a restart)
        self.latest: dict = {}

    # ---------- helpers ----------
    def _row(self):
        return self.db.execute("SELECT * FROM session WHERE id = 1").fetchone()

    def _log(self, s, event: str):
        now = self.clock()
        actual = round(max(0.0, now - s["started"]) / 60, 1) if s["started"] else None
        self.db.execute(
            "INSERT INTO session_log (ts, mode, event, planned_min, actual_min) VALUES (?,?,?,?,?)",
            (now, s["mode"], event, s["planned_min"], actual),
        )

    def _end(self, s, event: str):
        """Every way a session ends comes through here."""
        self._log(s, event)
        self.db.execute(
            "UPDATE session SET mode=NULL, locked=0, requested=NULL, started=NULL, until=NULL,"
            " planned_min=NULL, safeword_hash=NULL WHERE id=1"
        )
        self.db.commit()

    def _tick(self):
        """Apply time-based transitions: timer finished, lock never confirmed."""
        s = self._row()
        now = self.clock()
        if not s["mode"]:
            return
        if s["until"] and now >= s["until"]:
            self._end(s, "finished")
        elif s["mode"] == "locked" and not s["started"] and now - s["requested"] > START_CONFIRM_TIMEOUT:
            self._end(s, "start_failed")

    def _gw(self):
        return self.db.execute("SELECT * FROM gateway WHERE id = 1").fetchone()

    def pi_online(self) -> bool:
        with self.lock:
            g = self._gw()
            last = max(g["last_poll"] or 0, g["last_data"] or 0)
            return self.clock() - last < PI_FRESH

    # ---------- Pi side ----------
    def on_data(self, reading: dict):
        with self.lock:
            now = self.clock()
            self.latest = {**self.latest, **reading, "ts": now}
            self.db.execute("UPDATE gateway SET last_data=? WHERE id=1", (now,))
            s = self._row()
            # countdown starts only when the Pi confirms the arm is in lock position
            if s["mode"] == "locked" and s["locked"] and not s["started"] and reading.get("locked") is True:
                self.db.execute(
                    "UPDATE session SET started=?, until=? WHERE id=1",
                    (now, now + s["planned_min"] * 60),
                )
                self._log({**dict(s), "started": None}, "start")
            self.db.commit()
            self._tick()

    def poll(self) -> dict:
        with self.lock:
            self.db.execute("UPDATE gateway SET last_poll=? WHERE id=1", (self.clock(),))
            self.db.commit()
            self._tick()
            s = self._row()
            return {"service": "ok", "locked": bool(s["locked"]), "until": s["until"]}

    # ---------- web side ----------
    def state(self) -> dict:
        with self.lock:
            self._tick()
            s, g = self._row(), self._gw()
            now = self.clock()
            if not s["mode"]:
                phase = "idle"
            elif s["mode"] == "locked" and not s["started"]:
                phase = "locking"
            else:
                phase = "running"
            return {
                "phase": phase,
                "mode": s["mode"],
                "locked": bool(s["locked"]),
                "planned_min": s["planned_min"],
                "started": s["started"],
                "until": s["until"],
                "remaining_s": max(0, round(s["until"] - now)) if s["until"] else None,
                "lid_closed": self.latest.get("lid_closed"),
                "servo_locked": self.latest.get("locked"),
                "pi_online": self.pi_online(),
                "last_poll": g["last_poll"],
                "now": now,
            }

    def start(self, mode: str, minutes: int, safeword: str | None):
        if mode not in ("locked", "timer"):
            raise SessionError(400, "bad_mode")
        if not 1 <= minutes <= 600:
            raise SessionError(400, "bad_minutes")
        with self.lock:
            self._tick()
            s = self._row()
            if s["mode"]:
                raise SessionError(409, "session_running")
            now = self.clock()
            if mode == "timer":
                self.db.execute(
                    "UPDATE session SET mode='timer', locked=0, requested=?, started=?, until=?,"
                    " planned_min=? WHERE id=1",
                    (now, now, now + minutes * 60, minutes),
                )
                self._log({"mode": "timer", "planned_min": minutes, "started": None}, "start")
                self.db.commit()
                return
            if not safeword or len(safeword) < 3:
                raise SessionError(400, "safeword_too_short")
            if not self.pi_online():
                raise SessionError(409, "box_offline")
            if self.latest.get("lid_closed") is not True:
                raise SessionError(409, "lid_open")
            h = bcrypt.hashpw(safeword.encode(), bcrypt.gensalt())
            self.db.execute(
                "UPDATE session SET mode='locked', locked=1, requested=?, started=NULL, until=NULL,"
                " planned_min=?, safeword_hash=? WHERE id=1",
                (now, minutes, h),
            )
            self.db.commit()

    def unlock(self, safeword: str):
        with self.lock:
            self._tick()
            s = self._row()
            if s["mode"] != "locked":
                raise SessionError(409, "not_locked")
            if not bcrypt.checkpw((safeword or "").encode(), s["safeword_hash"]):
                self._log(s, "wrong_safeword")
                self.db.commit()
                raise SessionError(401, "wrong_safeword")
            self._end(s, "early")

    def emergency(self):
        with self.lock:
            s = self._row()
            if s["mode"] != "locked":
                raise SessionError(409, "not_locked")
            self._end(s, "emergency")

    def stop(self):
        with self.lock:
            self._tick()
            s = self._row()
            if s["mode"] != "timer":
                raise SessionError(409, "not_timer")
            self._end(s, "early")

    def week(self, tz_offset_min: int = 0) -> dict:
        """Focus minutes per day for the last 7 days (local days via the browser's UTC offset)."""
        now = self.clock()
        shift = tz_offset_min * 60
        today = int((now + shift) // 86400)
        days = [today - i for i in range(6, -1, -1)]
        per_day = {d: 0.0 for d in days}
        counts = {"sessions": 0, "finished": 0, "early": 0, "emergency": 0}
        with self.lock:
            rows = self.db.execute(
                "SELECT ts, event, actual_min FROM session_log WHERE ts >= ? AND event IN"
                " ('finished','early','emergency')",
                (days[0] * 86400 - shift,),
            ).fetchall()
        for r in rows:
            d = int((r["ts"] + shift) // 86400)
            if d in per_day:
                per_day[d] += max(0.0, r["actual_min"] or 0)
                counts["sessions"] += 1
                counts[r["event"]] += 1
        streak = 0
        for d in reversed(days):  # consecutive days with any focus, ending today (or yesterday)
            if per_day[d] > 0:
                streak += 1
            elif d != today or streak:
                break
        total = sum(per_day.values())
        return {
            "days": [{"date": d * 86400 - shift, "minutes": round(per_day[d], 1)} for d in days],
            "total_min": round(total, 1),
            "streak_days": streak,
            "completion_rate": round(counts["finished"] / counts["sessions"], 2) if counts["sessions"] else None,
            **counts,
        }

    def log(self, limit: int = 30) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT ts, mode, event, planned_min, actual_min FROM session_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
