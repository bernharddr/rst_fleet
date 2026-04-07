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

# Prefixes that indicate a road name — skip these when parsing display_name
ROAD_PREFIXES = ("jalan", "jl.", "jl ", "gang", "gg.", "tol ", "jalan tol")

# Values to ignore as too generic
IGNORE_VALUES = {"indonesia", "java", "jawa", "kalimantan", "sumatra", "sumatera",
                 "sulawesi", "bali", "papua", "nusa tenggara"}


class NominatimGeocoder:
    """
    Reverse geocodes lat/lng using OSM Nominatim.

    Uses a simple dict cache to avoid re-requesting the same coordinates.
    Throttles to ≤1 request/second per OSM policy.
    """

    BASE_URL = "https://nominatim.openstreetmap.org/reverse"

    def __init__(self):
        self._last_request_time: float = 0.0
        self._cache: dict[tuple[float, float], str] = {}

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < NOMINATIM_DELAY_SECONDS:
            time.sleep(NOMINATIM_DELAY_SECONDS - elapsed)
        self._last_request_time = time.monotonic()

    def reverse_geocode(self, lat: float, lng: float) -> str:
        key = (round(lat, 4), round(lng, 4))
        if key in self._cache:
            return self._cache[key]

        self._throttle()
        params = {
            "lat": key[0],
            "lon": key[1],
            "format": "json",
            "zoom": 12,
            "addressdetails": 1,
        }
        try:
            resp = requests.get(
                self.BASE_URL,
                params=params,
                headers={"User-Agent": NOMINATIM_USER_AGENT},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            address = data.get("address", {})
            display_name = data.get("display_name", "")
            result = self._extract_location_name(address, display_name)
            # Temporarily log at INFO so we can see what's happening
            logger.info(f"OSM ({lat},{lng}) → '{result}'  addr_keys={list(address.keys())}  display='{display_name[:80]}'")
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
        #    village comes before suburb so "Rorotan" is picked over "Cilincing".
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
