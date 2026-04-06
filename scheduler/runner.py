"""
In-process scheduler using the 'schedule' library.
Alternative to cron — runs as a persistent long-lived process.

Usage:
  python -m scheduler.runner
"""

import logging
import time

import schedule

from main import run

logger = logging.getLogger(__name__)


def _safe_run(slot: str) -> None:
    """Wraps run() so that exceptions don't crash the scheduler loop."""
    try:
        run(slot=slot)
    except Exception as e:
        logger.exception(f"Update failed for slot {slot}: {e}")


def start_scheduler() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    slots = ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"]
    slot_labels = ["0:00", "4:00", "8:00", "12:00", "16:00", "20:00"]

    for clock_time, label in zip(slots, slot_labels):
        schedule.every().day.at(clock_time).do(_safe_run, slot=label)
        logger.info(f"Scheduled update at {clock_time} WIB for slot {label}")

    logger.info("Fleet tracker scheduler started. Waiting for next slot...")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    start_scheduler()
