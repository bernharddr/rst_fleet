import json
import math
import os
import threading
import time
import logging
import requests

from config.settings import NOMINATIM_USER_AGENT, NOMINATIM_DELAY_SECONDS

logger = logging.getLogger(__name__)

_CACHE_FILE = os.environ.get(
    "GEOCODING_CACHE_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "geocoding_cache.json")
)

# ── Module-level shared state (thread-safe) ───────────────────────────────────

# Single cache shared across all NominatimGeocoder instances and threads
_cache: dict = {}
_cache_lock = threading.Lock()
_cache_loaded = False

# Single OSM throttle across all instances and threads
_osm_lock = threading.Lock()   # ensures only one OSM request at a time
_last_osm_call: float = 0.0


def _ensure_cache_loaded() -> None:
    global _cache, _cache_loaded
    if _cache_loaded:
        return
    with _cache_lock:
        if _cache_loaded:
            return
        if not os.path.exists(_CACHE_FILE):
            _cache_loaded = True
            return
        try:
            with open(_CACHE_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            _cache = {tuple(k.split(",")): (v[0], v[1]) for k, v in raw.items()}
            logger.info(f"Geocoding cache: {len(_cache)} entries loaded from disk.")
        except Exception as e:
            logger.warning(f"Could not load geocoding cache: {e}")
        _cache_loaded = True


def _save_disk_cache() -> None:
    with _cache_lock:
        try:
            serializable = {f"{k[0]},{k[1]}": list(v) for k, v in _cache.items()}
            with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(serializable, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Could not save geocoding cache: {e}")


def _osm_throttle() -> None:
    """Global rate limiter — only one OSM call at a time, min 1.1s apart."""
    global _last_osm_call
    with _osm_lock:
        elapsed = time.monotonic() - _last_osm_call
        if elapsed < NOMINATIM_DELAY_SECONDS:
            time.sleep(NOMINATIM_DELAY_SECONDS - elapsed)
        _last_osm_call = time.monotonic()


# ── Known places & constants ──────────────────────────────────────────────────

KNOWN_LOCATIONS = {
    "merak": "PELABUHAN MERAK",
    "bakauheni": "PELABUHAN BAKAUHENI",
    "tanjung priok": "PELABUHAN TANJUNG PRIOK",
    "priok": "PELABUHAN TANJUNG PRIOK",
    "delta mas": "DEPO DELTA",
    "depo delta": "DEPO DELTA",
}

FACILITY_FIELDS = ("amenity", "building", "industrial", "retail", "commercial")

AREA_FIELD_ORDER = (
    "hamlet", "quarter", "neighbourhood", "village",
    "suburb", "town", "municipality",
    "city_district", "city", "subdistrict", "regency", "county",
    "state",
)

ROAD_PREFIXES = ("jalan", "jl.", "jl ", "gang", "gg.", "tol ", "jalan tol")

IGNORE_VALUES = {"indonesia", "java", "jawa", "kalimantan", "sumatra", "sumatera",
                 "sulawesi", "bali", "papua", "nusa tenggara"}

_KNOWN_PLACES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "known_places.json")
_known_places: list[dict] = []
_known_places_loaded = False


def _load_known_places() -> list[dict]:
    global _known_places, _known_places_loaded
    if _known_places_loaded:
        return _known_places
    if not os.path.exists(_KNOWN_PLACES_FILE):
        _known_places_loaded = True
        return []
    try:
        with open(_KNOWN_PLACES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        _known_places = [
            p for p in data
            if "lat" in p and "lng" in p and "name" in p
            and not str(p.get("name", "")).startswith("_")
        ]
        logger.info(f"Loaded {len(_known_places)} known places from known_places.json")
    except Exception as e:
        logger.warning(f"Could not load known_places.json: {e}")
    _known_places_loaded = True
    return _known_places


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Geocoder class ────────────────────────────────────────────────────────────

class NominatimGeocoder:
    """
    Returns two location values per coordinate:
      area   — broad district/city: "ROROTAN, CILINCING, JAKARTA UTARA"
      detail — specific facility:   "INDAH KIAT PULP & PAPER" or None

    All instances share one cache and one OSM throttle (thread-safe).
    """

    BASE_URL = "https://nominatim.openstreetmap.org/reverse"

    def __init__(self):
        _ensure_cache_loaded()
        _load_known_places()

    def _check_geofence(self, lat: float, lng: float) -> str | None:
        for place in _known_places:
            if _haversine_km(lat, lng, place["lat"], place["lng"]) <= place.get("radius_km", 1.0):
                return place["name"].upper()
        return None

    def _query_osm(self, lat: float, lng: float, zoom: int) -> tuple[dict, str]:
        _osm_throttle()
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
        for fk in FACILITY_FIELDS:
            val = address.get(fk, "").strip()
            if val and val.lower() not in IGNORE_VALUES:
                return val.upper()
        return None

    def _extract_area(self, address: dict, display_name: str = "") -> str:
        full_text = " ".join(str(v).lower() for v in address.values()) + " " + display_name.lower()
        for keyword, canonical in KNOWN_LOCATIONS.items():
            if keyword in full_text:
                return canonical

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
        key = (round(lat, 4), round(lng, 4))

        with _cache_lock:
            if key in _cache:
                return _cache[key]

        detail = self._check_geofence(lat, lng)

        try:
            address, display_name = self._query_osm(key[0], key[1], zoom=16)

            if not detail:
                detail = self._extract_facility(address)

            area = self._extract_area(address, display_name)

            if area == "LOKASI TIDAK DIKETAHUI":
                address12, display12 = self._query_osm(key[0], key[1], zoom=12)
                area = self._extract_area(address12, display12)

            logger.info(f"OSM ({lat},{lng}) area='{area}'  detail='{detail}'")

        except Exception as e:
            logger.warning(f"OSM geocoding FAILED for ({lat},{lng}): {e}")
            area = "LOKASI TIDAK DIKETAHUI"

        result = (area, detail)
        with _cache_lock:
            _cache[key] = result
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
            with _cache_lock:
                cached = _cache.get(key)
            if cached is not None:
                result[(lat, lng)] = cached
                cache_hits += 1
            else:
                result[(lat, lng)] = self.reverse_geocode(lat, lng)
                osm_calls += 1

        logger.info(f"Geocoding done: {cache_hits} cache hits, {osm_calls} OSM calls.")
        if osm_calls > 0:
            _save_disk_cache()
        return result
