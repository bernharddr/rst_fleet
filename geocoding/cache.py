"""
Location cache — maps (lat, lng) coordinates to human-readable location names.

The cache is built passively: every time the app runs, it reads the LOKASI
values your team has already written in the sheet, cross-references them with
the vehicle's current GPS coordinates, and saves the mapping.

Over time the cache fills up with your team's own naming conventions
(e.g. "DEPO DELTA", "AIRIN", "PINDO 3") instead of generic OSM names.

Cache file format (location_cache.json):
{
  "-6.1115,106.8617": {
    "name": "DEPO DELTA",
    "count": 14,
    "last_seen": "2026-04-06T08:00:00"
  },
  ...
}

The "count" field tracks how many times a mapping has been confirmed —
higher count = more trustworthy. "last_seen" helps identify stale entries.
"""

import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "location_cache.json")

# Coordinate precision — 4 decimal places ≈ 11m resolution
COORD_PRECISION = 4


def _key(lat: float, lng: float) -> str:
    return f"{round(lat, COORD_PRECISION)},{round(lng, COORD_PRECISION)}"


def load_cache() -> dict:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load location cache: {e}")
        return {}


def save_cache(cache: dict) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        logger.info(f"Location cache saved: {len(cache)} entries in {CACHE_FILE}")
    except Exception as e:
        logger.warning(f"Could not save location cache: {e}")


def lookup(cache: dict, lat: float, lng: float) -> str | None:
    """
    Returns the cached location name for (lat, lng), or None if not found.
    """
    entry = cache.get(_key(lat, lng))
    if entry:
        return entry["name"]
    return None


def add_entry(cache: dict, lat: float, lng: float, name: str) -> bool:
    """
    Adds or reinforces a (lat, lng) → name mapping.
    If a different name already exists for these coordinates, the one
    with the higher count wins. Returns True if the cache was modified.
    """
    name = name.strip().upper()
    if not name or name in ("LOKASI TIDAK DIKETAHUI", "-", ""):
        return False

    k = _key(lat, lng)
    now = datetime.now().isoformat(timespec="seconds")

    if k not in cache:
        cache[k] = {"name": name, "count": 1, "last_seen": now}
        return True

    existing = cache[k]
    if existing["name"] == name:
        existing["count"] += 1
        existing["last_seen"] = now
        return True
    else:
        # Different name for same coordinates — keep the one seen more often
        if existing["count"] <= 1:
            # Override with the new name (existing was only seen once)
            cache[k] = {"name": name, "count": 1, "last_seen": now}
            return True
        else:
            logger.debug(
                f"Cache conflict at ({lat},{lng}): "
                f"'{existing['name']}' (count={existing['count']}) vs '{name}' (new) — keeping existing"
            )
            return False


def build_from_sheet(
    cache: dict,
    nopol_api_index: dict,      # {nopol: VehicleRecord}
    section_nopol_rows: dict,   # {nopol: row_1based}
    locator,                    # SheetLocator instance
    section,                    # SheetSection instance
) -> int:
    """
    Reads the current LOKASI column from the sheet for each vehicle in the
    section. If the vehicle also has valid GPS coordinates in the API, adds
    the (lat, lng) → LOKASI mapping to the cache.

    Returns the number of new/updated cache entries.
    """
    from config.settings import COL_LOKASI

    updated = 0
    for nopol, row_1based in section_nopol_rows.items():
        record = nopol_api_index.get(nopol)
        if not record:
            continue
        if record.lat == 0.0 and record.lng == 0.0:
            continue

        row_values = locator.get_row_values(row_1based)
        col_off = section.col_offset
        idx = col_off + COL_LOKASI
        lokasi = row_values[idx].strip() if idx < len(row_values) else ""

        if lokasi and lokasi != "-":
            if add_entry(cache, record.lat, record.lng, lokasi):
                updated += 1
                logger.debug(f"Cache: {nopol} ({record.lat},{record.lng}) → {lokasi}")

    return updated


def cache_stats(cache: dict) -> str:
    if not cache:
        return "empty"
    total = len(cache)
    high_conf = sum(1 for v in cache.values() if v["count"] >= 3)
    return f"{total} entries ({high_conf} high-confidence with count ≥ 3)"
