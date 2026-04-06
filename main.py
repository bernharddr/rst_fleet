"""
Fleet Location Tracker — Main Orchestration Script

Usage:
  python main.py                    # auto-detect current WIB time slot
  python main.py --slot 12:00       # force a specific slot
  python main.py --sheet SHEET_ID   # override Google Sheet ID from .env
  python main.py --dry-run          # print what would be written, no sheet update
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config.settings import (
    TZ, UPDATE_SLOTS, FLEET_GROUPS,
    GOOGLE_SHEET_ID, GOOGLE_CREDENTIALS_PATH,
)
from gfleet.auth import GFleetAuthenticator
from gfleet.client import GFleetClient
from geocoding.nominatim import NominatimGeocoder
from sheets.locator import SheetLocator
from sheets.writer import SheetWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


def get_current_slot(now: datetime) -> str:
    """Maps current WIB time to the most recently elapsed 4-hour slot."""
    current_minutes = now.hour * 60 + now.minute
    slot_minutes = [int(s.split(":")[0]) * 60 for s in UPDATE_SLOTS]
    best = UPDATE_SLOTS[0]
    for i, sm in enumerate(slot_minutes):
        if current_minutes >= sm:
            best = UPDATE_SLOTS[i]
    return best


def load_vehicles_config(path: str = "vehicles.json") -> dict[str, dict]:
    if not os.path.exists(path):
        logger.warning(f"vehicles.json not found at {path}. No vehicle-to-group mapping.")
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def connect_worksheet(sheet_id: str, worksheet_name: str = None) -> gspread.Worksheet:
    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_PATH, scopes=GOOGLE_SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    if worksheet_name:
        return spreadsheet.worksheet(worksheet_name)
    return spreadsheet.get_worksheet(0)


def run(slot: str = None, sheet_id: str = None, dry_run: bool = False) -> None:
    now = datetime.now(tz=TZ)
    current_slot = slot or get_current_slot(now)
    active_sheet_id = sheet_id or GOOGLE_SHEET_ID
    update_time_str = current_slot

    logger.info(f"Running fleet location update for slot {current_slot} (WIB: {now.strftime('%Y-%m-%d %H:%M')})")

    # Step 1: Fetch from GFleet API
    auth = GFleetAuthenticator()
    client = GFleetClient(auth)
    vehicles = client.fetch_all_vehicles()
    nopol_index = client.build_nopol_index(vehicles)
    logger.info(f"Indexed {len(nopol_index)} unique vehicles.")

    # Step 2: Load vehicle → group mapping
    vehicles_config = load_vehicles_config()

    # Step 3: Reverse geocode all vehicles with valid GPS
    geocoder = NominatimGeocoder()
    coords = [
        (rec.lat, rec.lng)
        for rec in nopol_index.values()
        if not (rec.lat == 0.0 and rec.lng == 0.0)
    ]
    location_by_coord = geocoder.batch_geocode(coords)
    # Build nopol → location_name index
    location_index: dict[str, str] = {}
    for nopol, rec in nopol_index.items():
        if rec.lat != 0.0 or rec.lng != 0.0:
            key = (round(rec.lat, 4), round(rec.lng, 4))
            location_index[nopol] = location_by_coord.get(key, "LOKASI TIDAK DIKETAHUI")

    if dry_run:
        logger.info("--- DRY RUN — Vehicles with GPS data ---")
        for nopol, loc in location_index.items():
            rec = nopol_index[nopol]
            status = GFleetClient.derive_sheet_status(rec)
            logger.info(f"  {nopol:20s}  {status:15s}  {loc}")
        logger.info("--- DRY RUN complete — no sheet updated ---")
        return

    if not active_sheet_id:
        logger.error("GOOGLE_SHEET_ID is not set. Set it in .env or use --sheet argument.")
        sys.exit(1)

    # Step 4: Connect to Google Sheets and scan layout
    logger.info("Connecting to Google Sheets...")
    worksheet = connect_worksheet(active_sheet_id)
    locator = SheetLocator(worksheet)
    locator.scan()

    writer = SheetWriter(worksheet)
    all_updates = []

    # Step 5: For each fleet group in this slot, build batch
    for group in FLEET_GROUPS:
        section = locator.find_section(current_slot, group)
        if section is None:
            logger.warning(f"No section found for slot={current_slot}, group={group}")
            continue

        logger.info(f"Processing {group} ({len(section.data_rows)} vehicles) ...")
        batch = writer.build_update_batch(
            section=section,
            locator=locator,
            nopol_api_index=nopol_index,
            location_index=location_index,
            update_time_str=update_time_str,
            now=now,
        )
        all_updates.extend(batch)

    # Step 6: Flush all updates in one call
    writer.flush(all_updates)
    logger.info(f"Done. Slot {current_slot} updated with {len(all_updates)} cell writes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fleet Location Tracker — GFleet → Google Sheets")
    parser.add_argument("--slot", help="Force a specific time slot, e.g. '12:00'")
    parser.add_argument("--sheet", help="Override GOOGLE_SHEET_ID from .env")
    parser.add_argument("--dry-run", action="store_true", help="Print updates without writing to sheet")
    args = parser.parse_args()
    run(slot=args.slot, sheet_id=args.sheet, dry_run=args.dry_run)
