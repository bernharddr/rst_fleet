import json
import math
import os
import time
import logging
import requests

from config.settings import NOMINATIM_USER_AGENT, NOMINATIM_DELAY_SECONDS

logger = logging.getLogger(__name__)

_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "geocoding_cache.json")


def _load_disk_cache() -> dict:
    if not os.path.exists(_CACHE_FILE):
        return {}
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        # Keys are stored as "lat,lng" strings; values are [area, detail_or_null]
        return {tuple(k.split(",")): (v[0], v[1]) for k, v in raw.items()}
    except Exception as e:
        logger.warning(f"Could not load geocoding cache: {e}")
        return {}


def _save_disk_cache(cache: dict) -> None:
    try:
        serializable = {f"{k[0]},{k[1]}": list(v) for k, v in cache.items()}
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Could not save geocoding cache: {e}")

KNOWN_LOCATIONS = {
    "merak": "PELABUHAN MERAK",
    "bakauheni": "PELABUHAN BAKAUHENI",
    "tanjung priok": "PELABUHAN TANJUNG PRIOK",
    "priok": "PELABUHAN TANJUNG PRIOK",
    "delta mas": "DEPO DELTA",
    "depo delta": "DEPO DELTA",
}

# Fields that indicate a specific named facility
FACILITY_FIELDS = ("amenity", "building", "industrial", "retail", "commercial")

# All fields ordered most → least granular for broad area extraction
AREA_FIELD_ORDER = (
    "hamlet", "quarter", "neighbourhood", "village",
    "suburb", "town", "municipality",
    "city_district", "city", "subdistrict", "regency", "county",
    "state",
)

# Prefixes that indicate a road name
ROAD_PREFIXES = ("jalan", "jl.", "jl ", "gang", "gg.", "tol ", "jalan tol")

# Values to ignore as too generic
IGNORE_VALUES = {"indonesia", "java", "jawa", "kalimantan", "sumatra", "sumatera",
                 "sulawesi", "bali", "papua", "nusa tenggara"}

_KNOWN_PLACES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "known_places.json")


def _load_known_places() -> list[dict]:
    if not os.path.exists(_KNOWN_PLACES_FILE):
        return []
    try:
        with open(_KNOWN_PLACES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [p for p in data
                if "lat" in p and "lng" in p and "name" in p
                and not str(p.get("name", "")).startswith("_")]
    except Exception as e:
        logger.warning(f"Could not load known_places.json: {e}")
        return []


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class NominatimGeocoder:
    """
    Returns two location values per coordinate:
      - area:   broad district/city name   (zoom=16, area fields only)
      - detail: specific facility name     (geofence → zoom=16 facility fields)

    Shown as separate columns: LOKASI and Lokasi Detil.
    """

    BASE_URL = "https://nominatim.openstreetmap.org/reverse"

    def __init__(self):
        self._last_request_time: float = 0.0
        self._cache: dict = _load_disk_cache()
        self._known_places = _load_known_places()
        logger.info(f"Geocoding cache: {len(self._cache)} entries loaded from disk.")
        if self._known_places:
            logger.info(f"Loaded {len(self._known_places)} known places from known_places.json")

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < NOMINATIM_DELAY_SECONDS:
            time.sleep(NOMINATIM_DELAY_SECONDS - elapsed)
        self._last_request_time = time.monotonic()

    def _check_geofence(self, lat: float, lng: float) -> str | None:
        for place in self._known_places:
            if _haversine_km(lat, lng, place["lat"], place["lng"]) <= place.get("radius_km", 1.0):
                return place["name"].upper()
        return None

    def _query_osm(self, lat: float, lng: float, zoom: int) -> tuple[dict, str]:
        self._throttle()
        params = {"lat": lat, "lon": lng, "format": "json", "zoom": zoom, "addressdetails": 1}
        resp = requests.get(
            self.BASE_URL, params=params,
            headers={"User-Agent": NOMINATIM_USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("address", {}), data.get("display_name", "")

    def _extract_facility(self, address: dict) -> str | None:
        """Extract specific facility name (amenity, industrial, building, etc.)"""
        for fk in FACILITY_FIELDS:
            val = address.get(fk, "").strip()
            if val and val.lower() not in IGNORE_VALUES:
                return val.upper()
        return None

    def _extract_area(self, address: dict, display_name: str = "") -> str:
        """Extract broad area name (village → suburb → town → city → regency)."""
        # Check known terminals/ports
        full_text = " ".join(str(v).lower() for v in address.values()) + " " + display_name.lower()
        for keyword, canonical in KNOWN_LOCATIONS.items():
            if keyword in full_text:
                return canonical

        # Walk area fields, collect up to 3 unique levels
        parts: list[str] = []
        for key in AREA_FIELD_ORDER:
            val = address.get(key, "").strip()
            if val and val.lower() not in IGNORE_VALUES:
                if val.upper() not in parts:
                    parts.append(val.upper())
            if len(parts) >= 3:
                break

        if parts:
            return ", ".join(parts)

        # Fallback: parse display_name, skip roads
        if display_name:
            result_parts = []
            for seg in [p.strip() for p in display_name.split(",")]:
                lower = seg.lower()
                if lower in IGNORE_VALUES:
                    continue
                if any(lower.startswith(prefix) for prefix in ROAD_PREFIXES):
                    continue
                if seg and len(seg) > 2:
                    result_parts.append(seg.upper())
                    if len(result_parts) >= 2:
                        break
            if result_parts:
                return ", ".join(result_parts)

        return "LOKASI TIDAK DIKETAHUI"

    def reverse_geocode(self, lat: float, lng: float) -> tuple[str, str | None]:
        """
        Returns (area, detail):
          area   — broad location: "ROROTAN, CILINCING, JAKARTA UTARA"
          detail — specific facility: "INDAH KIAT PULP & PAPER" or None
        """
        key = (round(lat, 4), round(lng, 4))
        if key in self._cache:
            return self._cache[key]

        # 1. Check user-defined geofences (highest priority)
        detail = self._check_geofence(lat, lng)

        try:
            # 2. Query zoom=16 — picks up facility-level detail + area fields
            address, display_name = self._query_osm(key[0], key[1], zoom=16)

            # Extract facility from zoom=16 if not found by geofence
            if not detail:
                detail = self._extract_facility(address)

            # Extract broad area from zoom=16
            area = self._extract_area(address, display_name)

            # Fall back to zoom=12 only if zoom=16 gave no area
            if area == "LOKASI TIDAK DIKETAHUI":
                address12, display12 = self._query_osm(key[0], key[1], zoom=12)
                area = self._extract_area(address12, display12)

            logger.info(
                f"OSM ({lat},{lng}) area='{area}'  detail='{detail}'"
            )

        except Exception as e:
            logger.warning(f"OSM geocoding FAILED for ({lat},{lng}): {e}")
            area = "LOKASI TIDAK DIKETAHUI"

        result = (area, detail)
        self._cache[key] = result
        return result

    def batch_geocode(
        self, coords: list[tuple[float, float]]
    ) -> dict[tuple[float, float], tuple[str, str | None]]:
        unique = list(set(coords))
        result: dict[tuple[float, float], tuple[str, str | None]] = {}
        cache_hits = 0
        osm_calls = 0
        for lat, lng in unique:
            key = (round(lat, 4), round(lng, 4))
            if key in self._cache:
                result[(lat, lng)] = self._cache[key]
                cache_hits += 1
            else:
                result[(lat, lng)] = self.reverse_geocode(lat, lng)
                osm_calls += 1
        logger.info(f"Geocoding done: {cache_hits} cache hits, {osm_calls} OSM calls.")
        if osm_calls > 0:
            _save_disk_cache(self._cache)
        return result
