"""
GFleet API History Endpoint Probe Script
Run this on your local machine to discover the history endpoint.

Usage:
  pip install requests
  python probe_history.py
"""

import base64
import json
import requests
from datetime import datetime, timedelta

BASE_URL = "https://gfleet-api.mdi.id"
USERNAME = "rstgroup"
PASSWORD = "~bJxx6sNwoWRAhh2LUIU7a5JpJOMnHyX"
API_KEY = "fa7b721a-f040-4d21-9567-18c36d1204bb"

# Sample NOPOL to use in parameterized requests
SAMPLE_NOPOL = "B 9006 TEK"

# Date range to try in history requests
DATE_FROM = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
DATE_TO = datetime.now().strftime("%Y-%m-%d")


def get_token() -> str:
    creds = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
    resp = requests.post(
        f"{BASE_URL}/auth/get-token",
        headers={
            "Authorization": f"Basic {creds}",
            "x-api-key": API_KEY,
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("detail", "")
    print(f"[AUTH] Token obtained: {'YES' if token else 'NO - ' + str(data)}")
    return token


def probe(token: str) -> None:
    headers = {
        "Authorization": f"Bearer {token}",
        "x-api-key": API_KEY,
        "Content-Type": "application/json",
    }

    # Candidate paths — GET with no params
    get_candidates = [
        "/gfleet/history",
        "/gfleet/api/history",
        "/gfleet/playback",
        "/gfleet/track",
        "/gfleet/report",
        "/gfleet/log",
        "/gfleet/api/log",
        "/gfleet/trips",
        "/gfleet/api/trips",
        "/gfleet/route",
        "/gfleet/api/route",
        "/gfleet/positions",
        "/gfleet/api/positions",
        "/gfleet/tracking",
        "/gfleet/api/tracking",
        "/gfleet/summary",
        "/gfleet/api/summary",
        "/report/gps",
        "/api/history",
        "/api/track",
        "/api/positions",
    ]

    # Parameterized variants to try for each candidate
    param_variants = [
        {},
        {"from": DATE_FROM, "to": DATE_TO},
        {"startDate": DATE_FROM, "endDate": DATE_TO},
        {"date": DATE_FROM},
        {"vehicleLicense": SAMPLE_NOPOL},
        {"vehicleLicense": SAMPLE_NOPOL, "from": DATE_FROM, "to": DATE_TO},
        {"nopol": SAMPLE_NOPOL, "from": DATE_FROM, "to": DATE_TO},
        {"deviceId": "", "from": DATE_FROM, "to": DATE_TO},
    ]

    print("\n=== Probing GET endpoints ===\n")
    for path in get_candidates:
        for params in param_variants[:2]:  # try no-params and date-range first
            try:
                r = requests.get(
                    f"{BASE_URL}{path}",
                    headers=headers,
                    params=params,
                    timeout=10,
                )
                snippet = r.text[:200].replace("\n", " ")
                print(f"  {r.status_code}  GET {path} {params or ''}")
                if r.status_code not in (404, 405):
                    print(f"         → {snippet}")
                    # If it looks like valid JSON data, print full response
                    try:
                        j = r.json()
                        if j.get("responseCode") == "000" or (
                            isinstance(j.get("detail"), list) and len(j["detail"]) > 0
                        ):
                            print("\n  *** INTERESTING RESPONSE ***")
                            print(json.dumps(j, indent=2)[:2000])
                            print()
                    except Exception:
                        pass
            except Exception as e:
                print(f"  ERR  GET {path}  → {e}")

    # Also try POST variants for history
    print("\n=== Probing POST endpoints ===\n")
    post_candidates = [
        "/gfleet/history",
        "/gfleet/api/history",
        "/gfleet/playback",
        "/gfleet/report",
        "/report/gps",
    ]
    post_bodies = [
        {"from": DATE_FROM, "to": DATE_TO},
        {"vehicleLicense": SAMPLE_NOPOL, "from": DATE_FROM, "to": DATE_TO},
        {"startDate": DATE_FROM, "endDate": DATE_TO},
        {"date": DATE_FROM, "vehicleLicense": SAMPLE_NOPOL},
    ]
    for path in post_candidates:
        for body in post_bodies[:2]:
            try:
                r = requests.post(
                    f"{BASE_URL}{path}",
                    headers=headers,
                    json=body,
                    timeout=10,
                )
                snippet = r.text[:200].replace("\n", " ")
                print(f"  {r.status_code}  POST {path} {body}")
                if r.status_code not in (404, 405):
                    print(f"         → {snippet}")
                    try:
                        j = r.json()
                        if j.get("responseCode") == "000":
                            print("\n  *** INTERESTING RESPONSE ***")
                            print(json.dumps(j, indent=2)[:2000])
                            print()
                    except Exception:
                        pass
            except Exception as e:
                print(f"  ERR  POST {path}  → {e}")


if __name__ == "__main__":
    token = get_token()
    probe(token)
    print("\nDone. Share the output above so we can identify the history endpoint.")
