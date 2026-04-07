"""
SQLite database for GPS position history.

Schema:
  gps_positions(id, nopol, lat, lng, speed, odo, ext_voltage, gps_time, inserted_at)

Deduplication: only insert when gps_time changes, or >10min heartbeat.
Retention: auto-purge rows older than 90 days.
"""

import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta

DB_PATH = Path("gps_history.db")
RETENTION_DAYS = 90
HEARTBEAT_MINUTES = 10

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA cache_size=-32000")  # 32 MB cache
    return _conn


def init_db() -> None:
    with _lock:
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS gps_positions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nopol       TEXT    NOT NULL,
                lat         REAL    NOT NULL,
                lng         REAL    NOT NULL,
                speed       REAL    NOT NULL DEFAULT 0,
                odo         REAL    NOT NULL DEFAULT 0,
                ext_voltage INTEGER NOT NULL DEFAULT 0,
                gps_time    TEXT    NOT NULL,
                inserted_at TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_nopol_gps
                ON gps_positions (nopol, gps_time DESC);
            CREATE INDEX IF NOT EXISTS idx_inserted
                ON gps_positions (inserted_at DESC);
        """)
        conn.commit()


def insert_position(
    nopol: str,
    lat: float,
    lng: float,
    speed: float,
    odo: float,
    ext_voltage: int,
    gps_time: str,
    last_gps_times: dict[str, str],
    last_insert_times: dict[str, str],
) -> bool:
    """
    Insert a GPS position if it's new (gps_time changed) or heartbeat expired.
    Updates last_gps_times and last_insert_times in-place.
    Returns True if a row was inserted.
    """
    if lat == 0.0 and lng == 0.0:
        return False

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    last_gt = last_gps_times.get(nopol)
    last_ins = last_insert_times.get(nopol)

    # Check heartbeat: force-insert even if gps_time unchanged after N minutes
    heartbeat_expired = False
    if last_ins:
        try:
            last_dt = datetime.fromisoformat(last_ins.replace("Z", "+00:00"))
            heartbeat_expired = (
                datetime.now(timezone.utc) - last_dt
            ) > timedelta(minutes=HEARTBEAT_MINUTES)
        except Exception:
            heartbeat_expired = True

    if gps_time == last_gt and not heartbeat_expired:
        return False

    with _lock:
        _get_conn().execute(
            "INSERT INTO gps_positions (nopol,lat,lng,speed,odo,ext_voltage,gps_time,inserted_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (nopol, lat, lng, speed, odo, ext_voltage, gps_time, now_iso),
        )
        _get_conn().commit()

    last_gps_times[nopol] = gps_time
    last_insert_times[nopol] = now_iso
    return True


def get_trail(nopol: str, hours: float = 24) -> list[dict]:
    """Return GPS trail for a vehicle within the last N hours."""
    since = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _lock:
        rows = _get_conn().execute(
            "SELECT lat, lng, speed, odo, gps_time, inserted_at"
            " FROM gps_positions"
            " WHERE nopol=? AND inserted_at >= ?"
            " ORDER BY gps_time ASC",
            (nopol, since),
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_positions() -> list[dict]:
    """Return the most recent GPS row per vehicle."""
    with _lock:
        rows = _get_conn().execute("""
            SELECT p.nopol, p.lat, p.lng, p.speed, p.odo, p.ext_voltage, p.gps_time
            FROM gps_positions p
            INNER JOIN (
                SELECT nopol, MAX(gps_time) AS max_gt
                FROM gps_positions
                GROUP BY nopol
            ) m ON p.nopol = m.nopol AND p.gps_time = m.max_gt
        """).fetchall()
    return [dict(r) for r in rows]


def purge_old() -> int:
    """Delete rows older than RETENTION_DAYS. Returns number of rows deleted."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _lock:
        cur = _get_conn().execute(
            "DELETE FROM gps_positions WHERE inserted_at < ?", (cutoff,)
        )
        _get_conn().commit()
    return cur.rowcount


def get_db_stats() -> dict:
    """Return row count and oldest/newest timestamps."""
    with _lock:
        row = _get_conn().execute(
            "SELECT COUNT(*) as cnt, MIN(inserted_at) as oldest, MAX(inserted_at) as newest"
            " FROM gps_positions"
        ).fetchone()
    return dict(row) if row else {}
