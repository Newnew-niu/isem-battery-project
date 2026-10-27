"""Download actual wind generation and load for the two I-SEM control areas from the
Energy-Charts public API (Fraunhofer ISE; data originate from the ENTSO-E Transparency Platform).

- country=ie -> Republic of Ireland (EirGrid control area)
- country=uk -> Northern Ireland (SONI control area; the only UK area still on ENTSO-E)
All-island values = ie + uk. See docs/DECISIONS.md, D5/D8.

One JSON file per country and month is cached in data/raw/energycharts/.
"""
import json
import sys
import time

import pandas as pd
import requests

from config import RAW

URL = "https://api.energy-charts.info/public_power"
RAW_DIR = RAW / "energycharts"
COUNTRIES = ["ie", "uk"]
# Calendar months in UTC; covers the warm-up month through the end of the study period.
MONTHS = pd.period_range("2024-08", "2026-10", freq="M")
PAUSE_S = 2.0


def fetch(country, start, end):
    params = {"country": country, "start": f"{start:%Y-%m-%dT%H:%MZ}", "end": f"{end:%Y-%m-%dT%H:%MZ}"}
    wait = 10.0
    for attempt in range(1, 6):
        try:
            r = requests.get(URL, params=params, timeout=120)
            if r.status_code == 200:
                return r.json()
            err = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            err = type(e).__name__
        print(f"  {country} {start:%Y-%m}: attempt {attempt} failed ({err}); waiting {wait:.0f}s", flush=True)
        time.sleep(wait)
        wait *= 2
    return None


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    failed = []
    for country in COUNTRIES:
        for m in MONTHS:
            out = RAW_DIR / f"public_power_{country}_{m.strftime('%Y%m')}.json"
            if out.exists():
                continue
            # end is inclusive in the API; stop one step before the next month starts
            start = m.start_time
            end = (m + 1).start_time - pd.Timedelta(minutes=15)
            payload = fetch(country, start, end)
            if payload is None:
                failed.append(out.name)
                continue
            out.write_text(json.dumps(payload))
            print(f"ok {out.name}: {len(payload['unix_seconds'])} timestamps", flush=True)
            time.sleep(PAUSE_S)
    if failed:
        print("FAILED: " + ", ".join(failed))
        sys.exit(1)
    print("All months downloaded.")


if __name__ == "__main__":
    main()
