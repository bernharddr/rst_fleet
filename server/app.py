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
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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

def _generate_snapshot():
    """
    Generate HTML snapshot from poller's in-memory vehicle data.
    No extra GFleet API call — reuses data already fetched by the poller.
    """
    import os
    from zoneinfo import ZoneInfo
    from datetime import datetime
    from geocoding.nominatim import NominatimGeocoder
    from output.reporter import save_and_report
    from state.tracker import load_state, haversine_km, bearing_arrow
    from main import load_fleet_assignments, _format_prev_time
    from config.settings import ENGINE_ON_VOLTAGE_MV

    with poller.current_vehicles_lock:
        vehicles_raw = list(poller.current_vehicles)

    if not vehicles_raw:
        logger.warning("Snapshot skipped: poller has no data yet.")
        return

    now = datetime.now(tz=ZoneInfo("Asia/Jakarta"))
    vehicle_state = load_state()
    fleet_assignments = load_fleet_assignments()

    # Geocode coordinates not already resolved by the poller.
    # Oncall Trailer vehicles already have lokasi set by the poller after each fetch;
    # for all others, batch_geocode uses the disk cache (mostly instant cache hits).
    geocoder = NominatimGeocoder()
    coords = [
        (v["lat"], v["lng"]) for v in vehicles_raw
        if (v["lat"] != 0.0 or v["lng"] != 0.0) and v.get("lokasi") is None
    ]
    geo_by_coord = geocoder.batch_geocode(coords)

    vehicles_data = []
    for v in vehicles_raw:
        nopol = v["nopol"]
        lat, lng = v["lat"], v["lng"]
        ext_voltage = v.get("ext_voltage", 0)
        engine_on = ext_voltage >= ENGINE_ON_VOLTAGE_MV

        if lat == 0.0 and lng == 0.0:
            status = "GPS Missing"
            lokasi = "GPS Missing"
            lokasi_detil = None
        else:
            prev = vehicle_state.get(nopol)
            dist = haversine_km(prev["lat"], prev["lng"], lat, lng) if prev else 0.0
            moved = dist >= 1.0

            if moved:
                status = "Jalan"
            elif engine_on:
                status = "Idle"
            else:
                status = "Berhenti"

            # Use pre-geocoded lokasi from poller (Oncall Trailer) if available,
            # otherwise fall back to batch_geocode result (disk cache for others)
            if v.get("lokasi") is not None:
                area, lokasi_detil = v["lokasi"], v.get("lokasi_detil")
            else:
                area, lokasi_detil = geo_by_coord.get((lat, lng), ("LOKASI TIDAK DIKETAHUI", None))
            if prev and moved:
                arrow = bearing_arrow(prev["lat"], prev["lng"], lat, lng)
                prev_time_str = _format_prev_time(prev.get("time", ""))
                lokasi = f"{area} ({arrow} {dist:.2f}km vs {prev_time_str})"
            else:
                lokasi = area

        vehicles_data.append({
            "nopol": nopol,
            "assignment": fleet_assignments.get(nopol, "Other"),
            "status": status,
            "engine_on": engine_on,
            "voltage_v": round(ext_voltage / 1000, 2),
            "speed_kmh": round(v.get("speed", 0), 1),
            "odo_km": round(v.get("odo", 0), 1),
            "lat": lat,
            "lng": lng,
            "lokasi": lokasi,
            "lokasi_detil": lokasi_detil,
            "gps_time": v.get("gps_time", ""),
            "at_place": v.get("at_place"),
            "place_entered_at": v.get("place_entered_at"),
        })

    save_and_report(vehicles_data, now, fleet_assignments)
    logger.info(f"Snapshot generated from poller data ({len(vehicles_data)} vehicles).")


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Init DB
    database.init_db()
    logger.info("Database initialized.")

    # Backfill place visits from existing GPS history (runs once when table is empty)
    from geocoding.nominatim import check_geofence
    n = database.backfill_place_visits(check_geofence)
    if n:
        logger.info(f"Backfilled {n} place visit records from GPS history.")
    else:
        logger.info("Place visits table already populated — skipping backfill.")

    # Register snapshot generator to run after every successful GPS poll
    poller.set_post_poll_callback(_generate_snapshot)

    # Start GPS poller thread
    poller.start_background()  # uses POLL_INTERVAL_SECONDS default (60s)
    logger.info("GPS poller started (snapshot generated after each fetch).")

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
async def get_trail(
    nopol: str,
    hours: float = 24,
    from_date: str | None = None,
    to_date: str | None = None,
):
    """
    GPS trail for a single vehicle.
    ?hours=24                                  → last 24 hours (default)
    ?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD   → explicit date range (00:00–23:59 UTC)
    """
    if from_date and to_date:
        from_iso = from_date + "T00:00:00Z"
        to_iso   = to_date   + "T23:59:59Z"
        trail = database.get_trail(nopol, from_iso=from_iso, to_iso=to_iso)
        return JSONResponse({"nopol": nopol, "from": from_iso, "to": to_iso, "points": trail})
    hours = max(0.5, min(hours, 8760))  # clamp 30 min – 1 year
    trail = database.get_trail(nopol, hours)
    return JSONResponse({"nopol": nopol, "hours": hours, "points": trail})


@app.get("/api/visits/active")
async def get_active_visits():
    """All vehicles currently inside a known place, with live duration."""
    visits = database.get_active_visits()
    now_utc = datetime.now(timezone.utc)
    for v in visits:
        try:
            entry_dt = datetime.fromisoformat(v["entered_at"].replace("Z", "+00:00"))
            v["duration_minutes"] = round((now_utc - entry_dt).total_seconds() / 60, 1)
        except Exception:
            v["duration_minutes"] = None
    return JSONResponse(visits)


@app.get("/api/visits/{nopol}")
async def get_visit_history(nopol: str, days: int = 30):
    """Visit history for a single vehicle (default: last 30 days)."""
    visits = database.get_visit_history(nopol, days)
    now_utc = datetime.now(timezone.utc)
    for v in visits:
        if v.get("exited_at") is None:
            try:
                entry_dt = datetime.fromisoformat(v["entered_at"].replace("Z", "+00:00"))
                v["duration_minutes"] = round((now_utc - entry_dt).total_seconds() / 60, 1)
            except Exception:
                pass
    return JSONResponse(visits)


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
