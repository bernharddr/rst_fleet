"""
Background GPS poller.

Polls GFleet API every POLL_INTERVAL_SECONDS and stores new positions to SQLite.
Runs as a background thread; exposes `current_vehicles` list for WebSocket broadcasts.

Oncall Trailer vehicles are geocoded (via OSM) after every successful fetch so
their `lokasi` field in current_vehicles stays fresh. Other groups are geocoded
only during snapshot generation (every 15 min) using the disk cache.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone

from gfleet.auth import GFleetAuthenticator
from gfleet.client import GFleetClient, GFleetRateLimitError
from geocoding.nominatim import NominatimGeocoder, check_geofence
from server import database
from server.database import insert_position, purge_old
from state.tracker import load_state, save_state, update_state

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 180  # 3 minutes; GFleet fetch ~20s, so min sleep = max(40,160) = 160s
ONCALL_GROUP = "Oncall Trailer"
JITTER_GRACE_SECONDS = 300   # 5 min grace before confirming a place exit (avoids GPS jitter)

# Shared state — read by WebSocket broadcaster and snapshot generator
current_vehicles: list[dict] = []
current_vehicles_lock = threading.Lock()

_last_gps_times: dict[str, str] = {}
_last_insert_times: dict[str, str] = {}
_state_dirty = False
_vehicle_state: dict = {}
_geocoder: NominatimGeocoder | None = None
_oncall_nopols: set[str] = set()
_post_poll_callback = None  # called after each successful poll; set by app.py
_vehicle_last_place: dict[str, str | None] = {}   # nopol → confirmed current place
_vehicle_exit_pending: dict[str, tuple[str, float]] = {}  # nopol → (place, mono time of apparent exit)


def set_post_poll_callback(fn) -> None:
    """Register a function to be called after each successful GPS poll."""
    global _post_poll_callback
    _post_poll_callback = fn


def _load_oncall_nopols() -> set[str]:
    """Load fleet_assignments.json and return the set of Oncall Trailer NOPOLs."""
    try:
        with open("fleet_assignments.json", encoding="utf-8") as f:
            data = json.load(f)
        return {k for k, v in data.items() if v == ONCALL_GROUP and not k.startswith("_")}
    except Exception as e:
        logger.warning(f"Could not load fleet assignments for geocoding: {e}")
        return set()


def _geocode_oncall(vehicle_list: list[dict]) -> None:
    """
    Geocode Oncall Trailer vehicles and update their `lokasi`/`lokasi_detil`
    fields in-place. Uses the shared NominatimGeocoder (disk cache aware).
    """
    if not _geocoder or not _oncall_nopols:
        return

    oncall = [
        v for v in vehicle_list
        if v["nopol"] in _oncall_nopols and (v["lat"] != 0.0 or v["lng"] != 0.0)
    ]
    if not oncall:
        return

    coords = [(v["lat"], v["lng"]) for v in oncall]
    try:
        geo = _geocoder.batch_geocode(coords)
        for v in oncall:
            result = geo.get((v["lat"], v["lng"]))
            if result:
                area, detail = result
                v["lokasi"] = area
                v["lokasi_detil"] = detail
        logger.debug(f"Oncall Trailer geocoding done for {len(oncall)} units.")
    except Exception as e:
        logger.warning(f"Oncall Trailer geocoding failed: {e}")


def _init_place_tracking() -> None:
    """On startup, restore in-memory last-place state from open DB visits."""
    for visit in database.get_active_visits():
        _vehicle_last_place[visit["nopol"]] = visit["place_name"]
    logger.info(f"Restored {len(_vehicle_last_place)} active place visits from DB.")


def _update_place_visits(vehicle_list: list[dict]) -> None:
    """
    Detect place entry/exit transitions with a 5-minute jitter grace period.
    If a vehicle briefly leaves a known place and returns within 5 minutes,
    no exit is recorded (treats it as GPS noise).
    Writes at_place / place_entered_at onto each vehicle dict.
    """
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_mono = time.monotonic()
    active: dict[str, str] = {
        v["nopol"]: v["entered_at"] for v in database.get_active_visits()
    }

    for v in vehicle_list:
        nopol = v.get("nopol")
        lat, lng = v.get("lat", 0.0), v.get("lng", 0.0)
        if not nopol:
            continue

        current_place: str | None = None
        if lat != 0.0 or lng != 0.0:
            current_place = check_geofence(lat, lng)

        last_place = _vehicle_last_place.get(nopol)
        pending = _vehicle_exit_pending.get(nopol)  # (place_name, mono_time) or None

        if pending:
            pending_place, pending_time = pending
            if current_place == pending_place:
                # Returned to the same place within grace period → GPS jitter, ignore exit
                del _vehicle_exit_pending[nopol]
                logger.info(f"[visit] {nopol} returned to {pending_place} — GPS jitter ignored")
            elif now_mono - pending_time >= JITTER_GRACE_SECONDS:
                # Grace period expired — confirm the exit
                database.record_place_exit(nopol, now_iso)
                active.pop(nopol, None)
                logger.info(f"[visit] {nopol} LEFT {pending_place} (confirmed after grace period)")
                del _vehicle_exit_pending[nopol]
                _vehicle_last_place[nopol] = None
                last_place = None
                # Fall through to handle possible new place entry below
                if current_place is not None:
                    database.record_place_entry(nopol, current_place, now_iso)
                    active[nopol] = now_iso
                    _vehicle_last_place[nopol] = current_place
                    logger.info(f"[visit] {nopol} ENTERED {current_place}")
            # else: still within grace period — keep showing as at pending_place

        else:
            if current_place != last_place:
                if last_place is not None and current_place is None:
                    # Left a known place → start grace period instead of recording exit immediately
                    _vehicle_exit_pending[nopol] = (last_place, now_mono)
                    logger.info(f"[visit] {nopol} may have left {last_place} — 5min grace started")
                elif last_place is not None and current_place is not None:
                    # Moved directly from one known place to another → confirm immediately
                    database.record_place_exit(nopol, now_iso)
                    active.pop(nopol, None)
                    database.record_place_entry(nopol, current_place, now_iso)
                    active[nopol] = now_iso
                    _vehicle_last_place[nopol] = current_place
                    logger.info(f"[visit] {nopol} LEFT {last_place} → ENTERED {current_place}")
                elif last_place is None and current_place is not None:
                    # Entered a known place
                    database.record_place_entry(nopol, current_place, now_iso)
                    active[nopol] = now_iso
                    _vehicle_last_place[nopol] = current_place
                    logger.info(f"[visit] {nopol} ENTERED {current_place}")

        # During grace period, keep showing as still-at-place so DURASI doesn't flash
        effective_place = current_place or (pending[0] if pending else None)
        v["at_place"] = effective_place
        v["place_entered_at"] = active.get(nopol) if effective_place else None


def _poll_once(client: GFleetClient) -> int:
    """Fetch all vehicles, insert new positions. Returns number of new rows inserted."""
    global _state_dirty, _vehicle_state

    vehicles = client.fetch_all_vehicles()
    nopol_index = client.build_nopol_index(vehicles)

    inserted = 0
    new_vehicle_list = []

    for nopol, rec in nopol_index.items():
        ok = insert_position(
            nopol=nopol,
            lat=rec.lat,
            lng=rec.lng,
            speed=rec.speed,
            odo=rec.odo,
            ext_voltage=rec.ext_voltage,
            gps_time=rec.gps_time,
            last_gps_times=_last_gps_times,
            last_insert_times=_last_insert_times,
        )
        if ok:
            inserted += 1

        new_vehicle_list.append({
            "nopol": nopol,
            "lat": rec.lat,
            "lng": rec.lng,
            "speed": round(rec.speed, 1),
            "odo": round(rec.odo, 1),
            "ext_voltage": rec.ext_voltage,
            "gps_time": rec.gps_time,
            "lokasi": None,        # filled by _geocode_oncall for Oncall Trailer
            "lokasi_detil": None,
        })

        # Keep vehicle_state updated (used by snapshot generator)
        prev_status = _vehicle_state.get(nopol, {}).get("status", "")
        update_state(_vehicle_state, nopol, rec.lat, rec.lng, rec.gps_time, prev_status)

    # Geocode Oncall Trailer vehicles immediately (OSM + disk cache)
    _geocode_oncall(new_vehicle_list)

    # Detect place entries/exits and annotate vehicles with at_place/place_entered_at
    _update_place_visits(new_vehicle_list)

    with current_vehicles_lock:
        current_vehicles.clear()
        current_vehicles.extend(new_vehicle_list)

    _state_dirty = True
    return inserted


def _save_state_periodically(interval: int = 60) -> None:
    """Save vehicle_state.json every N seconds to avoid hammering disk."""
    global _state_dirty
    while True:
        time.sleep(interval)
        if _state_dirty:
            try:
                save_state(_vehicle_state)
                _state_dirty = False
            except Exception as e:
                logger.warning(f"State save failed: {e}")


def run_forever(poll_interval: int = POLL_INTERVAL_SECONDS) -> None:
    """Main poll loop. Call in a daemon thread."""
    global _vehicle_state, _geocoder, _oncall_nopols
    _vehicle_state = load_state()
    _geocoder = NominatimGeocoder()
    _oncall_nopols = _load_oncall_nopols()
    logger.info(
        f"Poller starting — interval {poll_interval}s, "
        f"{len(_oncall_nopols)} Oncall Trailer units will be geocoded each fetch"
    )

    auth = GFleetAuthenticator()
    client = GFleetClient(auth)

    # Restore place-visit state from DB (handles restarts gracefully)
    _init_place_tracking()

    # State saver thread
    saver = threading.Thread(target=_save_state_periodically, daemon=True)
    saver.start()

    consecutive_errors = 0
    last_purge_day = -1

    while True:
        start = time.monotonic()
        try:
            n = _poll_once(client)
            if n:
                logger.debug(f"Poller: {n} new GPS rows inserted")
            consecutive_errors = 0
            if _post_poll_callback is not None:
                try:
                    _post_poll_callback()
                except Exception as cb_err:
                    logger.warning(f"Post-poll callback failed: {cb_err}")
        except GFleetRateLimitError as e:
            wait = e.retry_after + 5
            logger.warning(f"Rate limited by GFleet — waiting {wait}s before retry")
            time.sleep(wait)
            continue
        except Exception as e:
            consecutive_errors += 1
            backoff = min(120, poll_interval * consecutive_errors)
            logger.warning(f"Poller error ({consecutive_errors}x): {e} — retry in {backoff}s")
            time.sleep(backoff)
            continue

        # Daily DB purge (run once per calendar day)
        today = time.localtime().tm_yday
        if today != last_purge_day:
            try:
                deleted = purge_old()
                if deleted:
                    logger.info(f"Daily purge: removed {deleted} old GPS rows")
                last_purge_day = today
            except Exception as e:
                logger.warning(f"Purge failed: {e}")

        elapsed = time.monotonic() - start
        # Always sleep at least 40s after a successful fetch so the next request
        # starts ≥40s after the previous one completed (GFleet rate-limit window).
        sleep_for = max(40, poll_interval - elapsed)
        time.sleep(sleep_for)


def start_background(poll_interval: int = POLL_INTERVAL_SECONDS) -> threading.Thread:
    """Spawn the poll loop as a daemon thread and return it."""
    t = threading.Thread(target=run_forever, args=(poll_interval,), daemon=True, name="gps-poller")
    t.start()
    return t
