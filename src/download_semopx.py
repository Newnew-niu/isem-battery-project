"""Download official SEMOpx day-ahead (SEM-DA) market result files from the SEMOpx report API
(no account). SEMOpx keeps roughly the last 12 months, so this covers only part of the study
period. Used to validate Ember and to replace Ember's flat-price days (docs/DECISIONS.md, D9).

Raw CSVs are cached in data/raw/semopx/.
"""
import time

import requests

from config import RAW

API = "https://reports.semopx.com/api/v1/documents/static-reports"
DOC = "https://reports.semopx.com/documents/"
RAW_DIR = RAW / "semopx"
PREFIX = "MarketResult_SEM-DA_PWR-MRC-D+1_"


def list_da_files():
    names, page = [], 1
    while True:
        for attempt in range(5):
            r = requests.get(API, params={"DPuG_ID": "EA-001", "sort_by": "Date", "order_by": "ASC",
                                          "page_size": 100, "page": page}, timeout=60)
            if r.status_code == 200:
                break
            time.sleep(10 * (attempt + 1))
        r.raise_for_status()
        j = r.json()
        names += [i["ResourceName"] for i in j["items"] if i["ResourceName"].startswith(PREFIX)]
        if page >= j["pagination"]["totalPages"]:
            return sorted(set(names))
        page += 1
        time.sleep(1)


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    names = list_da_files()
    print(f"{len(names)} SEM-DA result files listed")
    for n in names:
        out = RAW_DIR / n
        if out.exists():
            continue
        for attempt in range(4):
            r = requests.get(DOC + n, timeout=60)
            if r.status_code == 200:
                out.write_bytes(r.content)
                break
            time.sleep(10 * (attempt + 1))
        else:
            print(f"FAILED {n}")
        time.sleep(1)
    print(f"{len(list(RAW_DIR.glob(PREFIX + '*.csv')))} files cached")


if __name__ == "__main__":
    main()
