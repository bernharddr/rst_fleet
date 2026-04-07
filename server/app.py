"""
RST Fleet Monitor — Web Server

FastAPI application that:
  - Serves the live fleet_report.html dashboard
  - Polls GFleet API every 10 seconds (background thread)
  - Pushes live vehicle positions via WebSocket
  - Exposes REST API for GPS trail queries
  - Runs OSM geocoding and snapshot generation every 15 minutes

Usage:
  python -m server.app
  uvicorn server.app:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from server import database, poller

logger = logging.getLogger(__name__)

REPORT_FILE = Path("fleet_report.html")
HISTORY_DIR = Path("history")

# ── WebSocket connection manager ──────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self._clients: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._clients.append(ws)
        logger.info(f"WS client connected. Total: {len(self._clients)}")

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            try:
                self._clients.remove(ws)
            except ValueError:
                pass
        logger.info(f"WS client disconnected. Total: {len(self._clients)}")

    async def broadcast(self, message: str):
        async with self._lock:
            dead = []
            for ws in self._clients:
                try:
                    await ws.send_text(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                try:
                    self._clients.remove(ws)
                except ValueError:
                    pass


manager = ConnectionManager()


# ── Background broadcast task ─────────────────────────────────────────────────

async def _broadcast_loop(interval: float = 10.0):
    """Push current vehicle positions to all WebSocket clients every N seconds."""
    while True:
        await asyncio.sleep(interval)
        with poller.current_vehicles_lock:
            vehicles = list(poller.current_vehicles)
        if vehicles:
            msg = json.dumps({"type": "positions", "vehicles": vehicles})
            await manager.broadcast(msg)


# ── Snapshot + geocoding background job ──────────────────────────────────────

def _run_snapshot():
    """Run the full main.py pipeline in a thread (generates fleet_report.html)."""
    try:
        from main import run as main_run
        main_run()
        logger.info("Snapshot generated.")
    except Exception as e:
        logger.error(f"Snapshot generation failed: {e}")


def _snapshot_loop(interval_minutes: int = 15):
    time.sleep(30)  # Wait for first poll to complete before first snapshot
    while True:
        _run_snapshot()
        time.sleep(interval_minutes * 60)


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Init DB
    database.init_db()
    logger.info("Database initialized.")

    # Start GPS poller thread
    poller.start_background(poll_interval=10)
    logger.info("GPS poller started.")

    # Start snapshot generator thread
    snap_thread = threading.Thread(target=_snapshot_loop, daemon=True, name="snapshot")
    snap_thread.start()
    logger.info("Snapshot generator started (every 15 min).")

    # Start WebSocket broadcast loop
    asyncio.create_task(_broadcast_loop(interval=10.0))

    # Daily DB purge at startup
    deleted = database.purge_old()
    if deleted:
        logger.info(f"Purged {deleted} old GPS rows (>{database.RETENTION_DAYS} days).")

    yield

    # Shutdown: save state
    from state.tracker import save_state
    save_state(poller._vehicle_state)
    logger.info("State saved on shutdown.")


app = FastAPI(title="RST Fleet Monitor", lifespan=lifespan)

# Serve history/ directory for snapshot downloads
HISTORY_DIR.mkdir(exist_ok=True)
app.mount("/history", StaticFiles(directory=str(HISTORY_DIR)), name="history")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    if REPORT_FILE.exists():
        return FileResponse(REPORT_FILE, media_type="text/html")
    return HTMLResponse(
        "<html><body><h2>RST Fleet Monitor</h2>"
        "<p>Waiting for first snapshot... refresh in 30 seconds.</p></body></html>"
    )


@app.get("/api/vehicles")
async def get_vehicles():
    """Latest GPS position per vehicle from DB."""
    return JSONResponse(database.get_latest_positions())


@app.get("/api/trail/{nopol}")
async def get_trail(nopol: str, hours: float = 24):
    """
    GPS trail for a single vehicle.
    ?hours=24   → last 24 hours (default)
    ?hours=168  → last 7 days
    ?hours=720  → last 30 days
    """
    hours = max(0.5, min(hours, 720))  # clamp 30 min – 30 days
    trail = database.get_trail(nopol, hours)
    return JSONResponse({"nopol": nopol, "hours": hours, "points": trail})


@app.get("/api/stats")
async def get_stats():
    stats = database.get_db_stats()
    with poller.current_vehicles_lock:
        live_count = len(poller.current_vehicles)
    return JSONResponse({**stats, "live_vehicles": live_count})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    # Send current data immediately on connect
    with poller.current_vehicles_lock:
        vehicles = list(poller.current_vehicles)
    if vehicles:
        await ws.send_text(json.dumps({"type": "positions", "vehicles": vehicles}))
    try:
        while True:
            # Keep connection alive; server pushes via broadcast_loop
            await ws.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(ws)
    except Exception:
        await manager.disconnect(ws)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    import os
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "server.app:app",
        host="0.0.0.0",
        port=port,
        reload=False,   # NEVER True — kills background threads
        log_level="info",
    )
