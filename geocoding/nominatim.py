import time
import logging
from functools import lru_cache
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


class NominatimGeocoder:
    """
    Reverse geocodes lat/lng to a human-readable Indonesian location name via OSM Nominatim.

    Usage policy:
    - Max 1 request/second (enforced via throttle)
    - Results are cached in-memory per process run to avoid duplicate calls
    """

    BASE_URL = "https://nominatim.openstreetmap.org/reverse"

    def __init__(self):
        self._last_request_time: float = 0.0

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < NOMINATIM_DELAY_SECONDS:
            time.sleep(NOMINATIM_DELAY_SECONDS - elapsed)
        self._last_request_time = time.monotonic()

    def reverse_geocode(self, lat: float, lng: float) -> str:
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
        try:
            resp = requests.get(
                self.BASE_URL,
                params=params,
                headers={"User-Agent": NOMINATIM_USER_AGENT},
                timeout=15,
            )
            resp.raise_for_status()
            address = resp.json().get("address", {})
            return self._extract_location_name(address)
        except Exception as e:
            logger.warning(f"OSM geocoding failed for ({lat},{lng}): {e}")
            return "LOKASI TIDAK DIKETAHUI"

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
        for lat, lng in unique:
            result[(lat, lng)] = self.reverse_geocode(lat, lng)
        logger.info(f"Geocoded {len(unique)} unique coordinates via OSM.")
        return result
