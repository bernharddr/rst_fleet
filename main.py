"""
Fleet Location Tracker — Main Orchestration Script

Usage:
  python main.py              # fetch GPS data, save HTML report
  python main.py --dry-run    # console preview only, still saves HTML report
  python main.py --slot 12:00 # force a specific time slot
  python main.py --sheet ID   # also push to Google Sheet (requires credentials.json)
"""

import argparse
import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from config.settings import (
    TZ, UPDATE_SLOTS, FLEET_GROUPS,
    GOOGLE_SHEET_ID, GOOGLE_CREDENTIALS_PATH,
)
from gfleet.auth import GFleetAuthenticator
from gfleet.client import GFleetClient
from geocoding.nominatim import NominatimGeocoder
from output.reporter import save_and_report, ASSIGNMENT_ORDER
from state.tracker import load_state, save_state, update_state, haversine_km, bearing_arrow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_current_slot(now: datetime) -> str:
    current_minutes = now.hour * 60 + now.minute
    slot_minutes = [int(s.split(":")[0]) * 60 for s in UPDATE_SLOTS]
    best = UPDATE_SLOTS[0]
    for i, sm in enumerate(slot_minutes):
        if current_minutes >= sm:
            best = UPDATE_SLOTS[i]
    return best


def _format_prev_time(time_str: str) -> str:
    if not time_str:
        return "?"
    try:
        utc = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        return utc.astimezone(ZoneInfo("Asia/Jakarta")).strftime("%H:%M")
    except Exception:
        return "?"


def load_fleet_assignments(path: str = "fleet_assignments.json") -> dict[str, str]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def run(slot: str = None, sheet_id: str = None, dry_run: bool = False) -> None:
    now = datetime.now(tz=TZ)
    current_slot = slot or get_current_slot(now)
    update_time_str = current_slot

    logger.info(
        f"Running fleet update — slot {current_slot} "
        f"(WIB: {now.strftime('%Y-%m-%d %H:%M')})"
        + (" [DRY RUN]" if dry_run else "")
    )

    # Step 1: Load last known positions
    vehicle_state = load_state()
    logger.info(f"Loaded state for {len(vehicle_state)} vehicles.")

    # Step 2: Fetch live GPS data
    auth = GFleetAuthenticator()
    client = GFleetClient(auth)
    vehicles = client.fetch_all_vehicles()
    nopol_index = client.build_nopol_index(vehicles)
    logger.info(f"Indexed {len(nopol_index)} unique vehicles.")

    # Step 3: Derive status for each vehicle
    status_index: dict[str, str] = {}
    engine_index: dict[str, bool] = {}
    coords_to_geocode = []

    for nopol, rec in nopol_index.items():
        prev = vehicle_state.get(nopol)
        status_index[nopol] = GFleetClient.derive_sheet_status(rec, prev)
        engine_index[nopol] = GFleetClient.engine_on(rec)
        if rec.lat != 0.0 or rec.lng != 0.0:
            coords_to_geocode.append((rec.lat, rec.lng))

    # Step 4: Geocode all unique coordinates → returns (area, detail) per coord
    geocoder = NominatimGeocoder()
    geo_by_coord = geocoder.batch_geocode(coords_to_geocode)

    # Step 5: Build location strings (with distance/direction if moved)
    location_index: dict[str, str] = {}   # broad area
    detail_index: dict[str, str | None] = {}  # specific facility
    for nopol, rec in nopol_index.items():
        if rec.lat != 0.0 or rec.lng != 0.0:
            area, detail = geo_by_coord.get((rec.lat, rec.lng), ("LOKASI TIDAK DIKETAHUI", None))
            prev = vehicle_state.get(nopol)
            if prev:
                dist = haversine_km(prev["lat"], prev["lng"], rec.lat, rec.lng)
                if dist >= 0.01:
                    prev_time_str = _format_prev_time(prev.get("time", ""))
                    arrow = bearing_arrow(prev["lat"], prev["lng"], rec.lat, rec.lng)
                    area = f"{area} ({arrow} {dist:.2f}km vs {prev_time_str})"
            location_index[nopol] = area
            detail_index[nopol] = detail

    # Step 6: Build vehicle data list for the HTML report
    fleet_assignments = load_fleet_assignments()
    vehicles_data = []
    for nopol, rec in nopol_index.items():
        vehicles_data.append({
            "nopol": nopol,
            "assignment": fleet_assignments.get(nopol, "Other"),
            "status": status_index.get(nopol, "GPS Missing"),
            "engine_on": engine_index.get(nopol, False),
            "voltage_v": round(rec.ext_voltage / 1000, 2),
            "lat": rec.lat,
            "lng": rec.lng,
            "lokasi": location_index.get(nopol, "GPS Missing"),
            "lokasi_detil": detail_index.get(nopol),
            "gps_time": rec.gps_time,
        })

    # Step 7: Save snapshot and generate HTML report (always)
    save_and_report(vehicles_data, now, fleet_assignments)

    # Step 8: Console dry-run table (grouped)
    if dry_run:
        groups: dict[str, list] = defaultdict(list)
        for nopol, rec in nopol_index.items():
            groups[fleet_assignments.get(nopol, "Other")].append((nopol, rec))

        header = f"  {'NOPOL':<20}  {'STATUS':<15}  {'ENG':<4}  {'VOLT':>7}  {'LOKASI':<40}  LOKASI DETIL"
        divider = f"  {'-'*20}  {'-'*15}  {'-'*4}  {'-'*7}  {'-'*40}  {'-'*25}"

        logger.info("--- DRY RUN — Vehicle statuses ---")
        for assignment in ASSIGNMENT_ORDER:
            if assignment not in groups:
                continue
            grp_vehicles = sorted(groups[assignment], key=lambda x: x[0])
            logger.info(f"\n  ===== {assignment} ({len(grp_vehicles)} units) =====")
            logger.info(header)
            logger.info(divider)
            for nopol, rec in grp_vehicles:
                status = status_index.get(nopol, "?")
                engine = "ON" if engine_index.get(nopol) else "OFF"
                voltage_v = rec.ext_voltage / 1000
                lokasi = location_index.get(nopol, "GPS Missing")
                detil = detail_index.get(nopol) or "-"
                logger.info(
                    f"  {nopol:<20}  {status:<15}  {engine:<4}  {voltage_v:>6.2f}V  {lokasi:<40}  {detil}"
                )
        logger.info("\n--- DRY RUN complete — HTML report saved ---")

    # Step 9: Optional Google Sheets update
    active_sheet_id = sheet_id or GOOGLE_SHEET_ID
    if active_sheet_id and not dry_run:
        _push_to_sheets(active_sheet_id, nopol_index, location_index, status_index, current_slot, now)

    # Step 10: Save updated positions to state file
    for nopol, rec in nopol_index.items():
        update_state(vehicle_state, nopol, rec.lat, rec.lng, rec.gps_time, status_index.get(nopol, ""))
    save_state(vehicle_state)
    logger.info(f"State saved for {len(vehicle_state)} vehicles.")


def _push_to_sheets(sheet_id, nopol_index, location_index, status_index, current_slot, now):
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        from sheets.locator import SheetLocator
        from sheets.writer import SheetWriter

        SCOPES = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_PATH, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(sheet_id)
        worksheet = spreadsheet.get_worksheet(0)

        locator = SheetLocator(worksheet)
        locator.scan()
        writer = SheetWriter(worksheet)
        all_updates = []

        for group in FLEET_GROUPS:
            section = locator.find_section(current_slot, group)
            if section is None:
                logger.warning(f"No section found for slot={current_slot}, group={group}")
                continue
            batch = writer.build_update_batch(
                section=section,
                locator=locator,
                nopol_api_index=nopol_index,
                location_index=location_index,
                status_index=status_index,
                update_time_str=current_slot,
                now=now,
            )
            all_updates.extend(batch)

        writer.flush(all_updates)
        logger.info(f"Google Sheets updated — {len(all_updates)} cells written.")
    except ImportError:
        logger.warning("gspread not installed — skipping Google Sheets update.")
    except Exception as e:
        logger.error(f"Google Sheets update failed: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RST Fleet Location Tracker")
    parser.add_argument("--slot", help="Force a time slot, e.g. '12:00'")
    parser.add_argument("--sheet", help="Push to Google Sheet (requires credentials.json)")
    parser.add_argument("--dry-run", action="store_true", help="Also print console table")
    args = parser.parse_args()
    run(slot=args.slot, sheet_id=args.sheet, dry_run=args.dry_run)
