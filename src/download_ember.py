"""Download Ember's hourly European wholesale price file and extract the Ireland series.

Source: https://ember-energy.org/data/european-wholesale-electricity-price-data
Licence: CC-BY-4.0 (original source for Ireland: SEMOpx day-ahead market).
"""
import io
import zipfile
from pathlib import Path

import requests

URL = ("https://files.ember-energy.org/public-downloads/price/outputs/"
       "european_wholesale_electricity_price_data_hourly.zip")
OUT = Path(__file__).resolve().parents[1] / "data" / "raw" / "ember" / "Ireland.csv"


def main(force=False):
    if OUT.exists() and not force:
        print(f"{OUT.name} already present; skipping (use force=True to refresh)")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(URL, timeout=300)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        OUT.write_bytes(z.read("Ireland.csv"))
    print(f"Saved {OUT}")


if __name__ == "__main__":
    main()
