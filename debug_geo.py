"""
Quick tool to debug what Nominatim returns for a specific coordinate.
Run: python debug_geo.py -6.9626 107.8088
"""
import sys
import json
import requests

lat = float(sys.argv[1]) if len(sys.argv) > 1 else -6.1333
lng = float(sys.argv[2]) if len(sys.argv) > 2 else 106.9557

print(f"Testing: lat={lat}, lng={lng}\n")

resp = requests.get(
    "https://nominatim.openstreetmap.org/reverse",
    params={"lat": lat, "lon": lng, "format": "json", "zoom": 12, "addressdetails": 1},
    headers={"User-Agent": "fleet-tracker-debug/1.0"},
    timeout=15,
)
data = resp.json()
print(f"display_name: {data.get('display_name')}\n")
print("address fields:")
for k, v in data.get("address", {}).items():
    print(f"  {k:25s} = {v}")
