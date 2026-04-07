import logging
from dataclasses import dataclass
import requests

from config.settings import GFLEET_BASE_URL, GFLEET_API_KEY, ENGINE_ON_VOLTAGE_MV
from gfleet.auth import GFleetAuthenticator, GFleetAuthError
from state.tracker import has_moved

logger = logging.getLogger(__name__)


class GFleetAPIError(Exception):
    pass


class GFleetRateLimitError(GFleetAPIError):
    def __init__(self, msg: str, retry_after: int = 30):
        super().__init__(msg)
        self.retry_after = retry_after


@dataclass
class VehicleRecord:
    device_id: str
    nopol: str          # vehicleLicense, normalized (stripped, uppercased)
    lat: float
    lng: float
    speed: float        # km/h
    odo: float
    gps_time: str       # raw time string from API e.g. "2025-12-09T14:25:25.000Z"
    status: int         # raw API status (0 = offline, 1 = online)
    fleet: str
    ext_voltage: int    # external voltage in millivolts (used for engine detection)


# Status values written to the sheet
STATUS_MOVING = "Jalan"           # moved > 1 km since last reading
STATUS_IDLE = "Idle"              # stopped, engine ON (voltage high)
STATUS_STOPPED = "Berhenti"       # stopped, engine OFF
STATUS_GPS_MISSING = "GPS Missing"


class GFleetClient:
    """
    Fetches vehicle data from the GFleet MODA API.
    Handles token refresh transparently on 401.
    """

    def __init__(self, authenticator: GFleetAuthenticator):
        self.auth = authenticator

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.auth.get_token()}",
            "x-api-key": GFLEET_API_KEY,
            "Content-Type": "application/json",
        }

    def fetch_all_vehicles(self) -> list[VehicleRecord]:
        url = f"{GFLEET_BASE_URL}/gfleet/api"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=60)
            if resp.status_code == 401:
                logger.info("Token expired, refreshing...")
                self.auth.get_token(force_refresh=True)
                resp = requests.get(url, headers=self._headers(), timeout=60)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 30))
                raise GFleetRateLimitError(f"Rate limited by GFleet API", retry_after=retry_after)
            resp.raise_for_status()
        except GFleetRateLimitError:
            raise
        except requests.RequestException as e:
            raise GFleetAPIError(f"Failed to fetch vehicles: {e}") from e

        data = resp.json()
        if data.get("responseCode") != "000":
            raise GFleetAPIError(f"GFleet API error: {data}")

        records = []
        for item in data.get("detail", []):
            try:
                rec = VehicleRecord(
                    device_id=str(item.get("deviceId", "")),
                    nopol=self._normalize_nopol(item.get("vehicleLicense", "")),
                    lat=float(item.get("lat", 0.0)),
                    lng=float(item.get("lng", 0.0)),
                    speed=float(item.get("speed", 0.0)),
                    odo=float(item.get("odo", 0.0)),
                    gps_time=str(item.get("time", "")),
                    status=int(item.get("status", 0)),
                    fleet=str(item.get("fleet", "")),
                    ext_voltage=int(item.get("extVoltage", 0)),
                )
                records.append(rec)
            except (TypeError, ValueError) as e:
                logger.warning(f"Skipping malformed vehicle record: {item} — {e}")

        logger.info(f"Fetched {len(records)} vehicles from GFleet API.")
        return records

    def build_nopol_index(self, records: list[VehicleRecord]) -> dict[str, VehicleRecord]:
        """Returns {normalized_nopol: VehicleRecord}, keeping most recent on duplicates."""
        index: dict[str, VehicleRecord] = {}
        for rec in records:
            if rec.nopol not in index or rec.gps_time > index[rec.nopol].gps_time:
                index[rec.nopol] = rec
        return index

    @staticmethod
    def derive_sheet_status(record: VehicleRecord, prev_state: dict | None) -> str:
        """
        Derives STATUS for the sheet using three signals:

        1. GPS validity  — lat/lng == 0 or device offline → GPS Missing
        2. Movement      — haversine distance from last position > 1 km → Jalan
        3. Engine state  — extVoltage > ENGINE_ON_VOLTAGE_MV → engine on → Idle
                        — extVoltage ≤ ENGINE_ON_VOLTAGE_MV → engine off → Berhenti

        Speed > 0 is also treated as moving (device reports speed directly).

        Returns: "Jalan" | "Idle" | "Berhenti" | "GPS Missing"
        """
        # No GPS signal — only check lat/lng, not status field
        # (Teltonika status=0 does NOT mean offline, it's a different field)
        if record.lat == 0.0 and record.lng == 0.0:
            return STATUS_GPS_MISSING

        # Moving: speed > 0 OR displaced > 1 km from last position
        if record.speed > 0 or has_moved(prev_state, record.lat, record.lng):
            return STATUS_MOVING

        # Stopped — check engine via voltage
        engine_on = record.ext_voltage > ENGINE_ON_VOLTAGE_MV
        return STATUS_IDLE if engine_on else STATUS_STOPPED

    @staticmethod
    def engine_on(record: VehicleRecord) -> bool:
        """True if the engine appears to be running based on external voltage."""
        return record.ext_voltage > ENGINE_ON_VOLTAGE_MV

    @staticmethod
    def _normalize_nopol(raw: str) -> str:
        """
        Normalize license plate, stripping driver name if appended.
        e.g. "B 9973 TEJ (MADNUR)" → "B 9973 TEJ"
        """
        import re
        clean = re.sub(r'\s*\(.*?\)', '', raw)  # remove (anything in parentheses)
        return clean.strip().upper()
