"""Time-series storage. InfluxDB in production, an in-memory store for tests / no-Docker runs."""
from __future__ import annotations

import time
from datetime import datetime, timezone

# Sensor fields we accept from the Pi, grouped into InfluxDB measurements.
MEASUREMENTS = {
    "env": {"temp": float, "hum": float, "gas": int, "light": int},
    "presence": {"dist": float, "lid_closed": bool, "locked": bool},
    "state": {"state": str, "level": int},
}
NUMERIC_FIELDS = {f for m in MEASUREMENTS.values() for f, t in m.items() if t in (float, int)}
STATE_LEVEL = {"OK": 0, "WARNING": 1, "NOT OK": 2}
RANGES = {  # range -> (bucket, aggregation window)
    "1h": ("raw", "10s"),
    "24h": ("raw", "1m"),
    "7d": ("1m", "10m"),
    "30d": ("1m", "1h"),
}
RANGE_SECONDS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


def clean(reading: dict) -> dict[str, dict]:
    """Split a Pi reading into measurements. Drops unknown fields and failed reads (-1)."""
    out: dict[str, dict] = {}
    if "state" in reading and "level" not in reading:
        reading = {**reading, "level": STATE_LEVEL.get(str(reading["state"]).upper(), None)}
    for meas, fields in MEASUREMENTS.items():
        vals = {}
        for name, typ in fields.items():
            v = reading.get(name)
            if v is None:
                continue
            try:
                v = typ(v)
            except (TypeError, ValueError):
                continue
            if typ in (float, int) and v == -1:
                continue  # sensor read failed
            vals[name] = v
        if vals:
            out[meas] = vals
    return out


class MemoryStore:
    def __init__(self):
        self.points: list[tuple[float, str, dict]] = []

    def write(self, ts: float, data: dict[str, dict]):
        for meas, vals in data.items():
            self.points.append((ts, meas, vals))
        cutoff = time.time() - RANGE_SECONDS["30d"]
        if self.points and self.points[0][0] < cutoff:
            self.points = [p for p in self.points if p[0] >= cutoff]

    def history(self, field: str, rng: str):
        since = time.time() - RANGE_SECONDS[rng]
        return [
            {"t": datetime.fromtimestamp(ts, timezone.utc).isoformat(), "v": vals[field]}
            for ts, _, vals in self.points
            if ts >= since and field in vals
        ]

    def close(self):
        pass


class InfluxStore:
    def __init__(self, url: str, token: str, org: str, bucket_raw: str, bucket_1m: str):
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS

        self.client = InfluxDBClient(url=url, token=token, org=org)
        self.org = org
        self.buckets = {"raw": bucket_raw, "1m": bucket_1m}
        self.writer = self.client.write_api(write_options=SYNCHRONOUS)
        self.query = self.client.query_api()

    def setup(self):
        """Create the 1-year bucket and the 1-minute downsampling task if they don't exist."""
        from influxdb_client import BucketRetentionRules

        buckets = self.client.buckets_api()
        if buckets.find_bucket_by_name(self.buckets["1m"]) is None:
            buckets.create_bucket(
                bucket_name=self.buckets["1m"],
                retention_rules=BucketRetentionRules(type="expire", every_seconds=365 * 86400),
                org=self.org,
            )
        tasks = self.client.tasks_api()
        name = "downsample_1m"
        if not tasks.find_tasks(name=name):
            flux = f'''option task = {{name: "{name}", every: 1m}}
from(bucket: "{self.buckets['raw']}")
  |> range(start: -task.every)
  |> filter(fn: (r) => r._measurement == "env" or r._measurement == "presence" or r._measurement == "state")
  |> filter(fn: (r) => r._field != "state")
  |> map(fn: (r) => ({{r with _value: float(v: r._value)}}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> to(bucket: "{self.buckets['1m']}", org: "{self.org}")'''
            org_id = self.client.organizations_api().find_organizations(org=self.org)[0].id
            tasks.create_task(task_create_request=_task_request(flux, org_id))

    def write(self, ts: float, data: dict[str, dict]):
        from influxdb_client import Point, WritePrecision

        points = []
        for meas, vals in data.items():
            p = Point(meas).time(int(ts * 1000), WritePrecision.MS)
            for k, v in vals.items():
                p = p.field(k, float(v) if isinstance(v, int) and not isinstance(v, bool) else v)
            points.append(p)
        self.writer.write(self.buckets["raw"], record=points)

    def history(self, field: str, rng: str):
        bucket, every = RANGES[rng]
        flux = f'''from(bucket: "{self.buckets[bucket]}")
  |> range(start: -{rng})
  |> filter(fn: (r) => r._field == "{field}")
  |> map(fn: (r) => ({{r with _value: float(v: r._value)}}))
  |> aggregateWindow(every: {every}, fn: mean, createEmpty: false)'''
        return [
            {"t": r.get_time().isoformat(), "v": r.get_value()}
            for table in self.query.query(flux)
            for r in table.records
        ]

    def close(self):
        self.client.close()


def _task_request(flux: str, org_id: str):
    from influxdb_client import TaskCreateRequest

    return TaskCreateRequest(flux=flux, org_id=org_id, status="active", description="1-minute means")
