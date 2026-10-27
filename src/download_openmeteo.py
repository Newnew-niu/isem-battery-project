"""Download archived 48-hour-ahead weather forecasts from the Open-Meteo Previous Runs API.

`*_previous_day2` = value predicted 48 h before valid time, which is published before
the DAM gate closure (11:00 Irish time on D-1) for every delivery hour of D.
See docs/DECISIONS.md, D2-D4, for the vintage, model and location choices.
Data: Open-Meteo.com, CC-BY-4.0.
"""
import json
import time
from pathlib import Path

import requests

URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw" / "openmeteo"

LOCATIONS = {
    "donegal": (54.95, -8.05),
    "north_mayo": (54.13, -9.62),
    "kerry": (52.25, -9.55),
    "west_cork": (51.85, -9.05),
    "offaly": (53.25, -7.25),
    "tyrone": (54.65, -7.00),
}
# Primary model, plus a second one for the robustness check in Phase 3.
MODELS = ["ecmwf_ifs025", "icon_seamless"]
VARIABLES = ["wind_speed_100m_previous_day2", "temperature_2m_previous_day2"]

# UTC dates; covers the warm-up month and the trading day that starts 23:00 local on 30 Sep 2026.
START, END = "2024-08-31", "2026-10-01"


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        for name, (lat, lon) in LOCATIONS.items():
            out = RAW_DIR / f"{model}_{name}.json"
            if out.exists():
                continue
            params = {
                "latitude": lat,
                "longitude": lon,
                "start_date": START,
                "end_date": END,
                "hourly": ",".join(VARIABLES),
                "models": model,
                "wind_speed_unit": "ms",
                "timezone": "GMT",
            }
            r = requests.get(URL, params=params, timeout=120)
            r.raise_for_status()
            payload = r.json()
            payload["location_name"] = name
            payload["model"] = model
            out.write_text(json.dumps(payload))
            n_null = sum(v is None for v in payload["hourly"][VARIABLES[0]])
            print(f"ok {out.name}: {len(payload['hourly']['time'])} hours, {n_null} null wind values")
            time.sleep(2)


if __name__ == "__main__":
    main()
