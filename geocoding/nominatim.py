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

# Address fields to try in priority order — comprehensive for Indonesian OSM data
ADDRESS_FIELD_PRIORITY = (
    "amenity", "building", "industrial", "retail", "commercial",
    "hamlet", "quarter", "suburb", "neighbourhood",
    "village", "town", "municipality",
    "city_district", "city", "county", "state_district",
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
            logger.debug(f"OSM ({lat},{lng}) → {result}  [display: {display_name[:60]}]")
        except Exception as e:
            logger.warning(f"OSM geocoding failed for ({lat},{lng}): {e}")
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

        # 2. Try address fields in priority order
        for key in ADDRESS_FIELD_PRIORITY:
            val = address.get(key, "").strip()
            if val and val.lower() not in IGNORE_VALUES:
                return val.upper()

        # 3. Parse display_name — skip road segments, return first useful segment
        # e.g. "Jl. Raya Bandung, Leles, Kabupaten Garut, Jawa Barat, Indonesia"
        #       → skip "Jl. Raya Bandung" → return "LELES"
        if display_name:
            parts = [p.strip() for p in display_name.split(",")]
            for part in parts:
                lower = part.lower()
                if lower in IGNORE_VALUES:
                    continue
                if any(lower.startswith(prefix) for prefix in ROAD_PREFIXES):
                    continue
                if part and len(part) > 2:
                    return part.upper()

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
