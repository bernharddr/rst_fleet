"""
Minimal entry point for cron invocation.

Suggested crontab (server timezone must be WIB / UTC+7):
  0 0,4,8,12,16,20 * * * /usr/bin/python3 /path/to/cron_entry.py >> /var/log/fleet-tracker.log 2>&1
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from main import run
run()
