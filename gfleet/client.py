import logging
from dataclasses import dataclass
import requests

from config.settings import GFLEET_BASE_URL, GFLEET_API_KEY
from gfleet.auth import GFleetAuthenticator, GFleetAuthError

logger = logging.getLogger(__name__)


class GFleetAPIError(Exception):
    pass


@dataclass
class VehicleRecord:
    device_id: str
    nopol: str        # vehicleLicense, normalized (stripped, uppercased)
    lat: float
    lng: float
    speed: float      # km/h
    odo: float
    gps_time: str     # raw time string from API e.g. "2025-12-09T14:25:25.000Z"
    status: int       # raw API status field (0 = offline, 1 = online)
    fleet: str


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
            resp.raise_for_status()
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
                )
                records.append(rec)
            except (TypeError, ValueError) as e:
                logger.warning(f"Skipping malformed vehicle record: {item} — {e}")

        logger.info(f"Fetched {len(records)} vehicles from GFleet API.")
        return records

    def build_nopol_index(self, records: list[VehicleRecord]) -> dict[str, VehicleRecord]:
        """
        Returns {normalized_nopol: VehicleRecord}.
        On duplicates, keeps the record with the most recent gps_time.
        """
        index: dict[str, VehicleRecord] = {}
        for rec in records:
            if rec.nopol not in index:
                index[rec.nopol] = rec
            else:
                existing = index[rec.nopol]
                if rec.gps_time > existing.gps_time:
                    index[rec.nopol] = rec
        return index

    @staticmethod
    def derive_sheet_status(record: VehicleRecord) -> str:
        """
        Derives the STATUS value for the sheet from GPS data.
        Returns one of: "Jalan", "Berhenti", "GPS Missing"
        Note: "Antri" is never returned — it requires human judgment.
        """
        if record.lat == 0.0 and record.lng == 0.0:
            return "GPS Missing"
        if record.status == 0:
            return "GPS Missing"
        if record.speed > 0:
            return "Jalan"
        return "Berhenti"

    @staticmethod
    def _normalize_nopol(raw: str) -> str:
        return raw.strip().upper()
