import logging
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import gspread

from config.settings import (
    TZ, COL_LOKASI, COL_PUKUL, COL_SEJAK, COL_DURASI,
    COL_DURASI_HARI, COL_STATUS,
)
from gfleet.client import VehicleRecord
from sheets.locator import SheetSection, SheetLocator

logger = logging.getLogger(__name__)


class SheetWriter:
    """
    Builds and flushes a batch of cell updates to Google Sheets.

    Safety rules:
    - Only writes to WRITABLE columns: LOKASI, PUKUL, STATUS
    - Also writes DURASI / DURASI (HARI) if SEJAK already has a value
    - Never overwrites STATUS if existing value is "Antri"
    - Never touches: DRIVER, KONTAK, JOB STATUS, SEJAK, CURRENT NOTE, DO cols
    """

    def __init__(self, worksheet: gspread.Worksheet):
        self.ws = worksheet

    def build_update_batch(
        self,
        section: SheetSection,
        locator: SheetLocator,
        nopol_api_index: dict[str, VehicleRecord],
        location_index: dict[str, str],
        update_time_str: str,
        now: datetime,
    ) -> list[dict]:
        """
        Returns a list of gspread batch_update dicts for the section.
        """
        batch = []
        nopol_row_map = locator.get_nopol_row_map(section)

        for nopol, row_1based in nopol_row_map.items():
            row_values = locator.get_row_values(row_1based)
            col_off = section.col_offset

            record = nopol_api_index.get(nopol)

            # --- LOKASI ---
            if record and not (record.lat == 0.0 and record.lng == 0.0):
                lokasi_val = location_index.get(nopol, "LOKASI TIDAK DIKETAHUI")
            else:
                lokasi_val = None  # Don't update if GPS missing

            # --- STATUS ---
            from gfleet.client import GFleetClient
            new_status = GFleetClient.derive_sheet_status(record) if record else "GPS Missing"
            existing_status = self._get_cell(row_values, col_off, COL_STATUS)
            write_status = self._should_overwrite_status(existing_status, new_status)

            # --- DURASI ---
            sejak_str = self._get_cell(row_values, col_off, COL_SEJAK).strip()
            durasi_val = None
            durasi_hari_val = None
            if sejak_str:
                try:
                    durasi_val, durasi_hari_val = self._compute_durasi(sejak_str, now)
                except Exception as e:
                    logger.debug(f"Could not compute DURASI for {nopol}: {e}")

            # Build cell updates
            if lokasi_val is not None:
                batch.append(self._cell(row_1based, col_off + COL_LOKASI + 1, lokasi_val))

            batch.append(self._cell(row_1based, col_off + COL_PUKUL + 1, update_time_str))

            if write_status:
                batch.append(self._cell(row_1based, col_off + COL_STATUS + 1, new_status))

            if durasi_val is not None:
                batch.append(self._cell(row_1based, col_off + COL_DURASI + 1, durasi_val))
            if durasi_hari_val is not None:
                batch.append(self._cell(row_1based, col_off + COL_DURASI_HARI + 1, durasi_hari_val))

        return batch

    def flush(self, batch: list[dict]) -> None:
        if not batch:
            logger.info("No cells to update.")
            return
        self.ws.batch_update(batch)
        logger.info(f"Flushed {len(batch)} cell updates to Google Sheets.")

    @staticmethod
    def _get_cell(row_values: list[str], col_offset: int, col_relative: int) -> str:
        idx = col_offset + col_relative
        if idx < len(row_values):
            return row_values[idx]
        return ""

    @staticmethod
    def _should_overwrite_status(existing: str, new_status: str) -> bool:
        return existing.strip() != "Antri"

    @staticmethod
    def _compute_durasi(sejak_str: str, now: datetime) -> tuple[str, str]:
        """
        Parse SEJAK (e.g. "08:30" or "8:30") as a time on today or yesterday.
        Returns (durasi_hh:mm, durasi_hari_int_str).
        """
        parts = sejak_str.strip().split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0

        sejak_time = dtime(hour=hour, minute=minute)
        sejak_dt = datetime.combine(now.date(), sejak_time, tzinfo=TZ)

        # If sejak is in the future (e.g. 23:00 and now is 01:00), it was yesterday
        if sejak_dt > now:
            from datetime import timedelta
            sejak_dt -= timedelta(days=1)

        delta = now - sejak_dt
        total_seconds = int(delta.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes = remainder // 60
        days = delta.days

        durasi_hhmm = f"{hours}:{minutes:02d}"
        return durasi_hhmm, str(days)

    @staticmethod
    def _cell(row_1based: int, col_1based: int, value: str) -> dict:
        """
        Returns a gspread batch_update-compatible dict.
        Uses A1 notation: col 1 = A, col 2 = B, ...
        """
        col_letter = SheetWriter._col_to_letter(col_1based)
        return {
            "range": f"{col_letter}{row_1based}",
            "values": [[value]],
        }

    @staticmethod
    def _col_to_letter(col_1based: int) -> str:
        result = ""
        while col_1based > 0:
            col_1based, remainder = divmod(col_1based - 1, 26)
            result = chr(65 + remainder) + result
        return result
