#!/usr/bin/env python3
"""
Import past GPS fleet data from GFleet trip-segment CSV files into gps_history.db.

Each CSV row is a trip segment (point A → point B). Both endpoints are inserted
as GPS positions so the trail viewer can show historical routes.

Usage:
    cd C:\\Users\\rog\\git_test
    python tools/import_past_data.py                     # imports all CSVs in "GPS Past Fleet Data/"
    python tools/import_past_data.py path/to/file.csv    # single file

Times in the CSV are WIB (UTC+7) and are converted to UTC on import.
"""

import csv
import re
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GPS_DB_PATH", "gps_history.db")

from server.database import init_db, _get_conn, _lock

WIB = timezone(timedelta(hours=7))
DEFAULT_DATA_DIR = Path("GPS Past Fleet Data")


def clean_nopol(raw: str) -> str:
    """'B 9001 TEK (ADIT)' → 'B 9001 TEK'"""
    return re.sub(r'\s*\([^)]*\)', '', raw).strip()


def parse_wib(s: str) -> str:
    """Parse 'YYYY/MM/DD HH:MM:SS' WIB → ISO8601 UTC string."""
    dt = datetime.strptime(s.strip(), "%Y/%m/%d %H:%M:%S").replace(tzinfo=WIB)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_duration_hours(s: str) -> float:
    """'1:50:04' → 1.834"""
    try:
        parts = s.strip().split(":")
        return int(parts[0]) + int(parts[1]) / 60 + int(parts[2]) / 3600
    except Exception:
        return 0.0


def import_csv(filepath: Path) -> tuple[int, int]:
    """
    Import one CSV file. Returns (inserted, skipped).
    Columns (positional, duplicate header names handled):
      0  Nama Armada
      1  No. perangkat
      2  No. Reg.       ← NOPOL (may have suffix like "(ADIT)")
      3  Waktu Mulai    ← trip start time WIB
      4  Lin.           ← start latitude
      5  Buj.           ← start longitude
      6  Alamat         ← start address (may be empty)
      7  Waktu Berakhir ← trip end time WIB
      8  Lin.           ← end latitude
      9  Buj.           ← end longitude
      10 Alamat         ← end address (may be empty)
      11 Waktu Mengemudi (hh:mm:ss)
      12 Jarak Tempuh (km)
    """
    print(f"\nImporting: {filepath}")
    rows_to_insert: list[tuple] = []
    parse_errors = 0

    with open(filepath, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader)  # skip header row
        for lineno, row in enumerate(reader, start=2):
            if len(row) < 10:
                continue
            try:
                nopol = clean_nopol(row[2])
                if not nopol:
                    continue

                start_time = parse_wib(row[3])
                start_lat  = float(row[4]) if row[4].strip() else 0.0
                start_lng  = float(row[5]) if row[5].strip() else 0.0
                end_time   = parse_wib(row[7])
                end_lat    = float(row[8]) if row[8].strip() else 0.0
                end_lng    = float(row[9]) if row[9].strip() else 0.0

                dur_h  = parse_duration_hours(row[11]) if len(row) > 11 else 0.0
                try:
                    dist_km = float(str(row[12]).replace(",", ".")) if len(row) > 12 and row[12].strip() else 0.0
                except ValueError:
                    dist_km = 0.0
                avg_spd = round(dist_km / dur_h, 1) if dur_h > 0.05 else 0.0

                # Insert start point (speed=0, just departed)
                if start_lat != 0.0 or start_lng != 0.0:
                    rows_to_insert.append((nopol, start_lat, start_lng, 0.0, 0.0, 27000, start_time, start_time))

                # Insert end point (avg speed for the trip)
                if end_lat != 0.0 or end_lng != 0.0:
                    rows_to_insert.append((nopol, end_lat, end_lng, avg_spd, 0.0, 27000, end_time, end_time))

            except Exception as e:
                parse_errors += 1
                if parse_errors <= 5:
                    print(f"  Line {lineno} parse error: {e} → {row[:5]}")

    print(f"  Parsed {len(rows_to_insert)} points ({parse_errors} errors). Inserting…")

    inserted = 0
    skipped  = 0

    with _lock:
        conn = _get_conn()
        # Bulk-check existing gps_times per nopol to avoid N+1 queries
        nopols_in_batch = {r[0] for r in rows_to_insert}
        existing: set[tuple] = set()
        for nopol in nopols_in_batch:
            rows_db = conn.execute(
                "SELECT nopol, gps_time FROM gps_positions WHERE nopol=?", (nopol,)
            ).fetchall()
            for r in rows_db:
                existing.add((r[0], r[1]))

        to_insert = [r for r in rows_to_insert if (r[0], r[6]) not in existing]
        skipped = len(rows_to_insert) - len(to_insert)

        conn.executemany(
            "INSERT INTO gps_positions (nopol,lat,lng,speed,odo,ext_voltage,gps_time,inserted_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            to_insert,
        )
        conn.commit()
        inserted = len(to_insert)

    print(f"  ✓ Inserted {inserted}, skipped {skipped} duplicates")
    return inserted, skipped


def main():
    init_db()

    if len(sys.argv) > 1:
        files = [Path(a) for a in sys.argv[1:]]
    else:
        if not DEFAULT_DATA_DIR.exists():
            print(f"ERROR: Default data directory '{DEFAULT_DATA_DIR}' not found.")
            print("Usage: python tools/import_past_data.py [file.csv ...]")
            sys.exit(1)
        files = sorted(DEFAULT_DATA_DIR.glob("*.csv"))

    if not files:
        print("No CSV files found.")
        sys.exit(0)

    total_inserted = 0
    total_skipped  = 0
    for fp in files:
        ins, skp = import_csv(fp)
        total_inserted += ins
        total_skipped  += skp

    print(f"\n{'='*40}")
    print(f"Done. Total inserted: {total_inserted}, skipped: {total_skipped}")
    print("Restart the server to rebuild the backfill index.")


if __name__ == "__main__":
    main()
