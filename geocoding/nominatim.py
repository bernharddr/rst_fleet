import json
import math
import os
import time
import logging
import requests

from config.settings import NOMINATIM_USER_AGENT, NOMINATIM_DELAY_SECONDS

logger = logging.getLogger(__name__)

KNOWN_LOCATIONS = {
    "merak": "PELABUHAN MERAK",
    "bakauheni": "PELABUHAN BAKAUHENI",
    "tanjung priok": "PELABUHAN TANJUNG PRIOK",
    "priok": "PELABUHAN TANJUNG PRIOK",
    "delta mas": "DEPO DELTA",
    "depo delta": "DEPO DELTA",
}

# Fields ordered from most granular → least granular.
# We walk this list and collect the first 3 unique non-ignored values.
LOCATION_FIELD_ORDER = (
    "amenity", "building", "industrial", "retail", "commercial",
    "hamlet", "quarter", "neighbourhood", "village",
    "suburb", "town", "municipality",
    "city_district", "city", "subdistrict", "regency", "county",
    "state",
)

# Fields that indicate a specific named facility (highest priority)
FACILITY_FIELDS = {"amenity", "building", "industrial", "retail", "commercial"}

# Prefixes that indicate a road name — skip these when parsing display_name
ROAD_PREFIXES = ("jalan", "jl.", "jl ", "gang", "gg.", "tol ", "jalan tol")

# Values to ignore as too generic
IGNORE_VALUES = {"indonesia", "java", "jawa", "kalimantan", "sumatra", "sumatera",
                 "sulawesi", "bali", "papua", "nusa tenggara"}

_KNOWN_PLACES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "known_places.json")


def _load_known_places() -> list[dict]:
    """Load user-defined geofence list from known_places.json."""
    if not os.path.exists(_KNOWN_PLACES_FILE):
        return []
    try:
        with open(_KNOWN_PLACES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [p for p in data if not str(p.get("name", "")).startswith("_")]
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
    Reverse geocodes lat/lng using OSM Nominatim.

    Two-pass strategy:
      1. Check known_places.json geofences (user-defined customer sites)
      2. OSM zoom=16 for facility-level names (factories, ports, depots)
      3. Fall back to OSM zoom=12 if zoom=16 only returns a road
    """

    BASE_URL = "https://nominatim.openstreetmap.org/reverse"

    def __init__(self):
        self._last_request_time: float = 0.0
        self._cache: dict[tuple[float, float], str] = {}
        self._known_places = _load_known_places()
        if self._known_places:
            logger.info(f"Loaded {len(self._known_places)} known places from known_places.json")

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < NOMINATIM_DELAY_SECONDS:
            time.sleep(NOMINATIM_DELAY_SECONDS - elapsed)
        self._last_request_time = time.monotonic()

    def _check_known_places(self, lat: float, lng: float) -> str | None:
        """Return place name if within any defined geofence, else None."""
        for place in self._known_places:
            dist = _haversine_km(lat, lng, place["lat"], place["lng"])
            if dist <= place.get("radius_km", 1.0):
                return place["name"].upper()
        return None

    def _query_osm(self, lat: float, lng: float, zoom: int) -> tuple[dict, str]:
        """Perform one OSM reverse geocode request. Returns (address, display_name)."""
        self._throttle()
        params = {
            "lat": lat, "lon": lng,
            "format": "json",
            "zoom": zoom,
            "addressdetails": 1,
        }
        resp = requests.get(
            self.BASE_URL, params=params,
            headers={"User-Agent": NOMINATIM_USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("address", {}), data.get("display_name", "")

    def reverse_geocode(self, lat: float, lng: float) -> str:
        key = (round(lat, 4), round(lng, 4))
        if key in self._cache:
            return self._cache[key]

        # 1. Check user-defined geofences first
        geofence_hit = self._check_known_places(lat, lng)
        if geofence_hit:
            logger.info(f"Geofence hit ({lat},{lng}) → '{geofence_hit}'")
            self._cache[key] = geofence_hit
            return geofence_hit

        try:
            # 2. Try zoom=16 — picks up named industrial/commercial facilities
            address16, display16 = self._query_osm(key[0], key[1], zoom=16)
            result = self._extract_location_name(address16, display16)
            has_facility = any(k in address16 for k in FACILITY_FIELDS)

            # 3. If zoom=16 found no facility and result is generic, fall back to zoom=12
            if not has_facility and result == "LOKASI TIDAK DIKETAHUI":
                address12, display12 = self._query_osm(key[0], key[1], zoom=12)
                result = self._extract_location_name(address12, display12)
                logger.info(f"OSM z16→z12 ({lat},{lng}) → '{result}'")
            else:
                logger.info(f"OSM z16 ({lat},{lng}) → '{result}'  facility={has_facility}")

        except Exception as e:
            logger.warning(f"OSM geocoding FAILED for ({lat},{lng}): {e}")
            result = "LOKASI TIDAK DIKETAHUI"

        self._cache[key] = result
        return result

    def _extract_location_name(self, address: dict, display_name: str = "") -> str:
        # 1. Check known terminals/ports/depots
        full_text = " ".join(str(v).lower() for v in address.values())
        full_text += " " + display_name.lower()
        for keyword, canonical in KNOWN_LOCATIONS.items():
            if keyword in full_text:
                return canonical

        # 2. Walk fields from most → least granular, collect up to 3 unique values.
        parts: list[str] = []
        for key in LOCATION_FIELD_ORDER:
            val = address.get(key, "").strip()
            if val and val.lower() not in IGNORE_VALUES:
                if val.upper() not in parts:
                    parts.append(val.upper())
            if len(parts) >= 3:
                break

        if parts:
            return ", ".join(parts)

        # 3. Fallback: parse display_name segments, skip roads, take first 2 useful parts
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

    def batch_geocode(
        self, coords: list[tuple[float, float]]
    ) -> dict[tuple[float, float], str]:
        unique = list(set(coords))
        result: dict[tuple[float, float], str] = {}
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
        return result
