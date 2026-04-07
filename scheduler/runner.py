"""
In-process scheduler using the 'schedule' library.
Runs as a persistent long-lived process — keep this terminal window open.

Usage:
  python scheduler/runner.py                  # run every 30 minutes (default)
  python scheduler/runner.py --interval 15    # run every 15 minutes
  python scheduler/runner.py --interval 60    # run every 60 minutes
"""

import argparse
import logging
import os
import subprocess
import time

import schedule

from main import run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _safe_run() -> None:
    """Wraps run() so exceptions don't crash the scheduler loop."""
    try:
        run()
    except Exception as e:
        logger.exception(f"Update failed: {e}")


def start_scheduler(interval_minutes: int = 15) -> None:
    schedule.every(interval_minutes).minutes.do(_safe_run)

    logger.info(f"Scheduler started — setiap {interval_minutes} menit. Ctrl+C untuk berhenti.")

    # Run immediately on start, open browser, then repeat on interval
    _safe_run()
    report = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fleet_report.html")
    if os.path.exists(report):
        try:
            os.startfile(report)
        except Exception:
            pass

    schedule.every(interval_minutes).minutes.do(_safe_run)
    logger.info(f"Laporan berikutnya dalam {interval_minutes} menit...")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fleet tracker scheduler")
    parser.add_argument(
        "--interval", type=int, default=15,
        help="How often to run in minutes (default: 15)"
    )
    args = parser.parse_args()
    start_scheduler(interval_minutes=args.interval)
