"""
Fleet Location Tracker — Main Orchestration Script

Usage:
  python main.py                         # auto-detect current WIB time slot
  python main.py --slot 12:00            # force a specific slot
  python main.py --sheet SHEET_ID        # override Google Sheet ID from .env
  python main.py --dry-run               # print what would be written, no sheet update
  python main.py --build-cache-only      # only learn from sheet, don't write GPS updates
  python main.py --show-cache            # print current location cache stats and entries
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
from geocoding.cache import load_cache, save_cache, build_from_sheet, cache_stats
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
        logger.warning(f"vehicles.json not found at {path}.")
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


def show_cache() -> None:
    cache = load_cache()
    print(f"\nLocation cache: {cache_stats(cache)}\n")
    if not cache:
        print("  (empty — run the app a few times to build it up)")
        return
    # Sort by count descending
    sorted_entries = sorted(cache.items(), key=lambda x: x[1]["count"], reverse=True)
    print(f"  {'COORDINATES':<22}  {'COUNT':>5}  {'LAST SEEN':<20}  LOCATION")
    print(f"  {'-'*22}  {'-'*5}  {'-'*20}  {'-'*30}")
    for coord_key, entry in sorted_entries:
        print(f"  {coord_key:<22}  {entry['count']:>5}  {entry['last_seen']:<20}  {entry['name']}")
    print()


def run(
    slot: str = None,
    sheet_id: str = None,
    dry_run: bool = False,
    build_cache_only: bool = False,
) -> None:
    now = datetime.now(tz=TZ)
    current_slot = slot or get_current_slot(now)
    active_sheet_id = sheet_id or GOOGLE_SHEET_ID
    update_time_str = current_slot

    logger.info(
        f"Running fleet location update for slot {current_slot} "
        f"(WIB: {now.strftime('%Y-%m-%d %H:%M')})"
        + (" [BUILD CACHE ONLY]" if build_cache_only else "")
        + (" [DRY RUN]" if dry_run else "")
    )

    # Step 1: Load location cache
    location_cache = load_cache()
    logger.info(f"Location cache loaded: {cache_stats(location_cache)}")

    # Step 2: Fetch from GFleet API
    auth = GFleetAuthenticator()
    client = GFleetClient(auth)
    vehicles = client.fetch_all_vehicles()
    nopol_index = client.build_nopol_index(vehicles)
    logger.info(f"Indexed {len(nopol_index)} unique vehicles.")

    # Step 3: Load vehicle → group mapping
    load_vehicles_config()

    if dry_run and not active_sheet_id:
        # Dry run without sheet: just show what GPS data looks like
        logger.info("--- DRY RUN (no sheet) — Vehicles with GPS data ---")
        for nopol, rec in nopol_index.items():
            status = GFleetClient.derive_sheet_status(rec)
            cached_loc = location_cache.get(
                f"{round(rec.lat, 4)},{round(rec.lng, 4)}", {}
            ).get("name", "?")
            logger.info(f"  {nopol:20s}  {status:15s}  lat={rec.lat} lng={rec.lng}  cache={cached_loc}")
        return

    if not active_sheet_id:
        logger.error("GOOGLE_SHEET_ID is not set. Set it in .env or use --sheet argument.")
        sys.exit(1)

    # Step 4: Connect to Google Sheets and scan layout
    logger.info("Connecting to Google Sheets...")
    worksheet = connect_worksheet(active_sheet_id)
    locator = SheetLocator(worksheet)
    locator.scan()

    # Step 5: Build location cache from current sheet data (passive learning)
    # Read existing LOKASI values BEFORE we write anything, cross-reference with GPS
    cache_additions = 0
    for group in FLEET_GROUPS:
        section = locator.find_section(current_slot, group)
        if section is None:
            continue
        nopol_row_map = locator.get_nopol_row_map(section)
        added = build_from_sheet(
            cache=location_cache,
            nopol_api_index=nopol_index,
            section_nopol_rows=nopol_row_map,
            locator=locator,
            section=section,
        )
        cache_additions += added

    if cache_additions > 0:
        save_cache(location_cache)
        logger.info(f"Cache updated: +{cache_additions} new/reinforced entries. Total: {cache_stats(location_cache)}")
    else:
        logger.info("Cache: no new entries this run (vehicles may not have LOKASI set yet).")

    if build_cache_only:
        logger.info("--build-cache-only mode: skipping sheet GPS updates.")
        return

    # Step 6: Geocode all vehicles with valid GPS (cache-first, OSM fallback)
    geocoder = NominatimGeocoder(location_cache=location_cache)
    coords = [
        (rec.lat, rec.lng)
        for rec in nopol_index.values()
        if not (rec.lat == 0.0 and rec.lng == 0.0)
    ]
    location_by_coord = geocoder.batch_geocode(coords)

    location_index: dict[str, str] = {}
    for nopol, rec in nopol_index.items():
        if rec.lat != 0.0 or rec.lng != 0.0:
            key = (round(rec.lat, 4), round(rec.lng, 4))
            location_index[nopol] = location_by_coord.get(key, "LOKASI TIDAK DIKETAHUI")

    if dry_run:
        logger.info("--- DRY RUN — What would be written to sheet ---")
        for nopol, loc in location_index.items():
            rec = nopol_index[nopol]
            status = GFleetClient.derive_sheet_status(rec)
            logger.info(f"  {nopol:20s}  {status:15s}  {loc}")
        logger.info("--- DRY RUN complete — no sheet updated ---")
        return

    # Step 7: Build and flush all sheet updates
    writer = SheetWriter(worksheet)
    all_updates = []

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

    writer.flush(all_updates)
    logger.info(f"Done. Slot {current_slot} updated with {len(all_updates)} cell writes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fleet Location Tracker — GFleet → Google Sheets"
    )
    parser.add_argument("--slot", help="Force a specific time slot, e.g. '12:00'")
    parser.add_argument("--sheet", help="Override GOOGLE_SHEET_ID from .env")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be written without touching the sheet"
    )
    parser.add_argument(
        "--build-cache-only", action="store_true",
        help="Only learn location names from the sheet, don't write GPS updates"
    )
    parser.add_argument(
        "--show-cache", action="store_true",
        help="Print current location cache contents and exit"
    )
    args = parser.parse_args()

    if args.show_cache:
        show_cache()
        sys.exit(0)

    run(
        slot=args.slot,
        sheet_id=args.sheet,
        dry_run=args.dry_run,
        build_cache_only=args.build_cache_only,
    )
