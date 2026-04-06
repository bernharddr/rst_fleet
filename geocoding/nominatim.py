import time
import logging
from functools import lru_cache
import requests

from config.settings import NOMINATIM_USER_AGENT, NOMINATIM_DELAY_SECONDS
from geocoding.cache import lookup as cache_lookup

logger = logging.getLogger(__name__)

# Known Indonesian terminal/depot name overrides
# Maps partial Nominatim address matches to canonical names
KNOWN_LOCATIONS = {
    "merak": "PELABUHAN MERAK",
    "bakauheni": "PELABUHAN BAKAUHENI",
    "tanjung priok": "PELABUHAN TANJUNG PRIOK",
    "priok": "PELABUHAN TANJUNG PRIOK",
    "delta mas": "DEPO DELTA",
    "depo delta": "DEPO DELTA",
}


class NominatimGeocoder:
    """
    Reverse geocodes lat/lng to a human-readable Indonesian location name.

    Lookup priority:
      1. location_cache.json  — your team's own naming (most accurate)
      2. Nominatim (OSM)      — fallback for unknown coordinates

    OSM Nominatim usage policy:
    - Max 1 request/second (enforced via NOMINATIM_DELAY_SECONDS)
    - Must set a descriptive User-Agent
    - Results cached in-memory per run to avoid duplicate requests
    """

    BASE_URL = "https://nominatim.openstreetmap.org/reverse"

    def __init__(self, location_cache: dict | None = None):
        self._last_request_time: float = 0.0
        self._location_cache = location_cache or {}

    def reverse_geocode(self, lat: float, lng: float) -> str:
        # 1. Check team's location cache first
        cached = cache_lookup(self._location_cache, lat, lng)
        if cached:
            logger.debug(f"Cache hit ({lat},{lng}) → {cached}")
            return cached

        # 2. Fall back to OSM Nominatim
        return self._cached_geocode(round(lat, 4), round(lng, 4))

    @lru_cache(maxsize=512)
    def _cached_geocode(self, lat: float, lng: float) -> str:
        self._throttle()
        params = {
            "lat": lat,
            "lon": lng,
            "format": "json",
            "zoom": 14,
            "addressdetails": 1,
        }
        headers = {"User-Agent": NOMINATIM_USER_AGENT}
        try:
            resp = requests.get(
                self.BASE_URL, params=params, headers=headers, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            address = data.get("address", {})
            result = self._extract_location_name(address)
            logger.debug(f"OSM geocode ({lat},{lng}) → {result}")
            return result
        except Exception as e:
            logger.warning(f"Geocoding failed for ({lat}, {lng}): {e}")
            return "LOKASI TIDAK DIKETAHUI"

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < NOMINATIM_DELAY_SECONDS:
            time.sleep(NOMINATIM_DELAY_SECONDS - elapsed)
        self._last_request_time = time.monotonic()

    def _extract_location_name(self, address: dict) -> str:
        full_text = " ".join(str(v).lower() for v in address.values())
        for keyword, canonical in KNOWN_LOCATIONS.items():
            if keyword in full_text:
                return canonical

        for key in ("amenity", "building", "suburb", "neighbourhood",
                    "city_district", "city", "town", "village", "county"):
            val = address.get(key, "")
            if val:
                return val.upper()

        return "LOKASI TIDAK DIKETAHUI"

    def batch_geocode(
        self, coords: list[tuple[float, float]]
    ) -> dict[tuple[float, float], str]:
        unique = list(set(coords))
        result: dict[tuple[float, float], str] = {}
        cache_hits = 0
        osm_calls = 0
        for lat, lng in unique:
            cached = cache_lookup(self._location_cache, lat, lng)
            if cached:
                result[(lat, lng)] = cached
                cache_hits += 1
            else:
                result[(lat, lng)] = self._cached_geocode(round(lat, 4), round(lng, 4))
                osm_calls += 1
        logger.info(f"Geocoding: {cache_hits} cache hits, {osm_calls} OSM calls.")
        return result
