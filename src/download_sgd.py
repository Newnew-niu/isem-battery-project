"""VALIDATION ONLY - not part of the pipeline (see docs/DECISIONS.md, D5).
The service turned out to be too rate-limited for a full download; the few chunks fetched
are used only to cross-check Energy-Charts timestamps in build_db.py.

Download all-island actual wind generation and system demand (15-min) from the
EirGrid Smart Grid Dashboard data service.

The service is rate-limited (intermittent HTTP 503), so requests are spaced out,
retried with exponential backoff, and every chunk is cached as raw JSON in
data/raw/sgd/. Re-running only fetches chunks that are not cached yet.
Failed chunks are reported, never filled in.
"""
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

BASE_URL = "https://www.smartgriddashboard.com/DashboardService.svc/data"
RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw" / "sgd"
AREAS = {"windactual": "WIND_ACTUAL", "demandactual": "SYSTEM_DEMAND"}
REGION = "ALL"

# Study period plus one month of warm-up (see docs/DECISIONS.md, D1).
# The extra day at the end covers the last trading day, which ends 23:00 local on 30 Sep.
START = date(2024, 9, 1)
END = date(2026, 10, 2)  # exclusive
CHUNK_DAYS = 7
PAUSE_S = 3.0
MAX_RETRIES = 6


def chunks(start, end, days):
    d = start
    while d < end:
        yield d, min(d + timedelta(days=days), end)
        d += timedelta(days=days)


def fetch(area, d0, d1):
    """Fetch [d0, d1) and return the parsed JSON, retrying on 503/timeouts."""
    last = d1 - timedelta(days=1)
    params = {
        "area": area,
        "region": REGION,
        "datefrom": d0.strftime("%d-%b-%Y") + " 00:00",
        "dateto": last.strftime("%d-%b-%Y") + " 23:59",
    }
    wait = 10.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(BASE_URL, params=params, timeout=60)
            if r.status_code == 200:
                payload = r.json()
                if payload.get("Status") == "Success":
                    return payload
                err = f"status={payload.get('Status')} msg={payload.get('ErrorMessage')}"
            else:
                err = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            err = type(e).__name__
        print(f"  {area} {d0}: attempt {attempt} failed ({err}); waiting {wait:.0f}s", flush=True)
        time.sleep(wait)
        wait = min(wait * 2, 300)
    return None


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    failed = []
    for area in AREAS:
        for d0, d1 in chunks(START, END, CHUNK_DAYS):
            out = RAW_DIR / f"{area}_{REGION}_{d0:%Y%m%d}_{d1:%Y%m%d}.json"
            if out.exists():
                continue
            payload = fetch(area, d0, d1)
            if payload is None:
                failed.append(out.name)
                print(f"FAILED {out.name}", flush=True)
            else:
                out.write_text(json.dumps(payload))
                print(f"ok {out.name} rows={len(payload['Rows'])}", flush=True)
            time.sleep(PAUSE_S)
    if failed:
        print(f"\n{len(failed)} chunk(s) failed; re-run to retry:\n  " + "\n  ".join(failed))
        sys.exit(1)
    print("All chunks downloaded.")


if __name__ == "__main__":
    main()
