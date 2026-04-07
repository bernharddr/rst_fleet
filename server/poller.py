"""
Background GPS poller.

Polls GFleet API every POLL_INTERVAL_SECONDS and stores new positions to SQLite.
Runs as a background thread; exposes `current_vehicles` dict for WebSocket broadcasts.
"""

import logging
import threading
import time

from gfleet.auth import GFleetAuthenticator
from gfleet.client import GFleetClient, GFleetRateLimitError
from server.database import insert_position, purge_old
from state.tracker import load_state, save_state, update_state

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 30  # GFleet API rate-limits at ~1 req/20s; 30s is safe

# Shared state — read by WebSocket broadcaster
current_vehicles: list[dict] = []
current_vehicles_lock = threading.Lock()

_last_gps_times: dict[str, str] = {}
_last_insert_times: dict[str, str] = {}
_state_dirty = False
_vehicle_state: dict = {}


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
        })

        # Keep vehicle_state updated (used by main.py snapshot generator)
        prev_status = _vehicle_state.get(nopol, {}).get("status", "")
        update_state(_vehicle_state, nopol, rec.lat, rec.lng, rec.gps_time, prev_status)

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
    global _vehicle_state
    _vehicle_state = load_state()
    logger.info(f"Poller starting — interval {poll_interval}s")

    auth = GFleetAuthenticator()
    client = GFleetClient(auth)

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
        except GFleetRateLimitError as e:
            # Respect Retry-After from API, add a small buffer
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
        sleep_for = max(0, poll_interval - elapsed)
        time.sleep(sleep_for)


def start_background(poll_interval: int = POLL_INTERVAL_SECONDS) -> threading.Thread:
    """Spawn the poll loop as a daemon thread and return it."""
    t = threading.Thread(target=run_forever, args=(poll_interval,), daemon=True, name="gps-poller")
    t.start()
    return t
