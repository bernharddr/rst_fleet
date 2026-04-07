"""
Vehicle position state tracker.

Persists the last known GPS position for each vehicle between runs.
Used to detect movement by comparing current position vs last position.

State file: vehicle_state.json
Format:
{
  "B 9006 TEK": {
    "lat": -6.1050,
    "lng": 106.8800,
    "time": "2025-12-09T14:25:25.000Z",
    "status": "Berhenti"
  },
  ...
}
"""

import json
import logging
import math
import os

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "vehicle_state.json")

# Movement threshold in kilometres
MOVEMENT_THRESHOLD_KM = 1.0


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load vehicle state: {e}")
        return {}


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Could not save vehicle state: {e}")


_ARROWS = ["↑", "↗", "→", "↘", "↓", "↙", "←", "↖"]  # N NE E SE S SW W NW


def bearing_arrow(lat1: float, lng1: float, lat2: float, lng2: float) -> str:
    """Returns an 8-point compass arrow for travel direction from point 1 → point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lng2 - lng1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    deg = (math.degrees(math.atan2(x, y)) + 360) % 360
    return _ARROWS[round(deg / 45) % 8]


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Returns the great-circle distance in kilometres between two GPS points.
    Uses the Haversine formula.
    """
    R = 6371.0  # Earth radius in km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def has_moved(prev: dict | None, lat: float, lng: float) -> bool:
    """
    Returns True if the vehicle has moved more than MOVEMENT_THRESHOLD_KM
    since the last recorded position.
    """
    if not prev:
        return False
    dist = haversine_km(prev["lat"], prev["lng"], lat, lng)
    return dist >= MOVEMENT_THRESHOLD_KM


def update_state(state: dict, nopol: str, lat: float, lng: float, gps_time: str, status: str) -> None:
    state[nopol] = {
        "lat": lat,
        "lng": lng,
        "time": gps_time,
        "status": status,
    }
