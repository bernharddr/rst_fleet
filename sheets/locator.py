import logging
import re
from dataclasses import dataclass, field

import gspread

from config.settings import COL_NOPOL
from gfleet.client import GFleetClient

logger = logging.getLogger(__name__)


@dataclass
class SheetSection:
    time_slot: str          # e.g. "12:00"
    fleet_group: str        # e.g. "KSO"
    header_row: int         # 1-based row index of the NOPOL/DRIVER/... header row
    data_rows: list[int] = field(default_factory=list)   # 1-based row indices of vehicle data rows
    col_offset: int = 0     # 0-based column index where NOPOL starts in each data row


class SheetLocator:
    """
    Scans a worksheet once and dynamically locates all section boundaries.

    Sheet layout (each section):
      Row N:   "UPDATE JAM  HH:MM"          ← time marker
      Row N+1: "KSO  TRAILER"               ← fleet group header
      Row N+2: "NOPOL | DRIVER | LOKASI ..." ← column header
      Row N+3+: vehicle data rows            ← until blank or next section

    All discovery is done from a single get_all_values() call.
    """

    # Matches "UPDATE JAM" with a time like "0:00", "4:00", "12:00"
    UPDATE_JAM_RE = re.compile(r"UPDATE\s+JAM", re.IGNORECASE)
    TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")
    NOPOL_RE = re.compile(r"\bNOPOL\b", re.IGNORECASE)

    def __init__(self, worksheet: gspread.Worksheet):
        self.ws = worksheet
        self._all_values: list[list[str]] = []
        self._section_map: dict[tuple[str, str], SheetSection] = {}

    def scan(self) -> None:
        """
        Reads the entire sheet once and builds the section map.
        Call this once per run before any find_section() calls.
        """
        self._all_values = self.ws.get_all_values()
        self._section_map = {}
        self._parse_sections()
        logger.info(f"Sheet scanned: {len(self._section_map)} sections found.")

    def _parse_sections(self) -> None:
        rows = self._all_values
        n = len(rows)
        i = 0
        current_slot: str | None = None

        while i < n:
            row = rows[i]
            row_text = " ".join(str(c) for c in row)

            # Detect UPDATE JAM row
            if self.UPDATE_JAM_RE.search(row_text):
                time_match = self.TIME_RE.search(row_text)
                if time_match:
                    current_slot = time_match.group(1)
                i += 1
                continue

            # Detect fleet group header (e.g. "KSO  TRAILER", "RST  TRWB")
            if current_slot is not None:
                fleet_group = self._detect_fleet_group(row_text)
                if fleet_group:
                    # Look for the NOPOL header row in the next few rows
                    header_row_idx = None
                    for j in range(i + 1, min(i + 5, n)):
                        if self.NOPOL_RE.search(" ".join(rows[j])):
                            header_row_idx = j
                            break

                    if header_row_idx is not None:
                        col_offset = self._find_nopol_col(rows[header_row_idx])
                        data_rows = self._collect_data_rows(
                            header_row_idx + 1, col_offset
                        )
                        section = SheetSection(
                            time_slot=current_slot,
                            fleet_group=fleet_group,
                            header_row=header_row_idx + 1,  # 1-based
                            data_rows=[r + 1 for r in data_rows],  # 1-based
                            col_offset=col_offset,
                        )
                        key = (current_slot, fleet_group)
                        # Keep existing if already found (first occurrence wins)
                        if key not in self._section_map:
                            self._section_map[key] = section
                        i = (data_rows[-1] + 1) if data_rows else header_row_idx + 1
                        continue

            i += 1

    def _detect_fleet_group(self, row_text: str) -> str | None:
        text_upper = row_text.upper()
        for group in ("KSO", "RST", "IBL", "RGB"):
            if group in text_upper:
                return group
        return None

    def _find_nopol_col(self, row: list[str]) -> int:
        for idx, cell in enumerate(row):
            if self.NOPOL_RE.match(cell.strip()):
                return idx
        return 0

    def _collect_data_rows(self, start_idx: int, col_offset: int) -> list[int]:
        """
        Collects 0-based row indices of vehicle data rows starting at start_idx.
        Stops at blank rows or new UPDATE JAM markers.
        A data row must have a non-empty NOPOL-column cell.
        """
        rows = self._all_values
        data_rows = []
        for i in range(start_idx, len(rows)):
            row = rows[i]
            row_text = " ".join(row)
            if self.UPDATE_JAM_RE.search(row_text):
                break
            nopol_cell = row[col_offset].strip() if col_offset < len(row) else ""
            if nopol_cell and not self.NOPOL_RE.match(nopol_cell):
                data_rows.append(i)
        return data_rows

    def find_section(self, time_slot: str, fleet_group: str) -> SheetSection | None:
        return self._section_map.get((time_slot, fleet_group))

    def get_nopol_row_map(self, section: SheetSection) -> dict[str, int]:
        """
        Returns {normalized_nopol: 1-based_row_index} for all data rows in the section.
        """
        result = {}
        for row_1based in section.data_rows:
            row = self._all_values[row_1based - 1]
            col = section.col_offset
            nopol_raw = row[col].strip() if col < len(row) else ""
            if nopol_raw:
                normalized = GFleetClient._normalize_nopol(nopol_raw)
                result[normalized] = row_1based
        return result

    def get_row_values(self, row_1based: int) -> list[str]:
        """Returns the raw cell values for a given 1-based row index."""
        return self._all_values[row_1based - 1]

    @property
    def all_sections(self) -> dict[tuple[str, str], SheetSection]:
        return self._section_map
