"""Run the server without Docker (in-memory storage) for UI work: python tools/dev_server.py"""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.environ.setdefault("DEVICE_TOKEN", "change-me")
os.environ.setdefault("SQLITE_PATH", str(root / "data" / "dev.db"))
(root / "data").mkdir(exist_ok=True)
sys.path.insert(0, str(root / "api"))

import uvicorn  # noqa: E402

uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), reload=False)
