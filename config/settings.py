import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("Asia/Jakarta")

UPDATE_SLOTS = ["0:00", "4:00", "8:00", "12:00", "16:00", "20:00"]

FLEET_GROUPS = ["KSO", "RST", "IBL", "RGB"]

GFLEET_BASE_URL = os.environ.get("GFLEET_BASE_URL", "https://gfleet-api.mdi.id")
GFLEET_USERNAME = os.environ["GFLEET_USERNAME"]
GFLEET_PASSWORD = os.environ["GFLEET_PASSWORD"]
GFLEET_API_KEY = os.environ["GFLEET_API_KEY"]

GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_CREDENTIALS_PATH = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")

NOMINATIM_USER_AGENT = "fleet-tracker-moda/1.0"
NOMINATIM_DELAY_SECONDS = 1.1

# Column indices (0-based, relative to the NOPOL column in each data row)
COL_NOPOL = 0
COL_DRIVER = 1        # MANUAL — never written
COL_KONTAK = 2        # MANUAL — never written
COL_LOKASI = 3        # AUTO
COL_JOB_STATUS = 4    # MANUAL — never written
COL_PUKUL = 5         # AUTO
COL_SEJAK = 6         # MANUAL — never written
COL_DURASI = 7        # AUTO (conditional — only if SEJAK is set)
COL_DURASI_HARI = 8   # AUTO (conditional — only if SEJAK is set)
COL_STATUS = 9        # AUTO (guarded — never overwrites "Antri")
COL_CURRENT_NOTE = 10  # MANUAL — never written

WRITABLE_COLS = {COL_LOKASI, COL_PUKUL, COL_STATUS}
