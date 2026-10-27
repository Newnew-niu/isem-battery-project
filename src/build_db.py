"""Clean and align all raw series to a UTC hourly index, run data-quality checks,
write reports/data_quality_log.md and load everything into DuckDB.

Nothing is imputed: gaps are counted and logged, and left as NULL in the database.
"""
import json
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd

from config import (DB_PATH, LOCAL_TZ, RAW, REPORTS, SQL_DIR, STUDY_END,
                    STUDY_START, TRADING_DAY_TZ, WARMUP_START)

log_lines = []


def log(text=""):
    print(text)
    log_lines.append(text)


def add_time_columns(df):
    utc = df["ts_utc"].dt.tz_localize("UTC")
    df["ts_local"] = utc.dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    df["trading_date"] = utc.dt.tz_convert(TRADING_DAY_TZ).dt.date
    return df


def gap_runs(missing, freq):
    """Group missing timestamps into contiguous runs -> DataFrame(start, end, n)."""
    if len(missing) == 0:
        return pd.DataFrame(columns=["start", "end", "n"])
    s = pd.Series(missing)
    run = (s.diff() != pd.Timedelta(freq)).cumsum()
    return s.groupby(run).agg(start="min", end="max", n="size").reset_index(drop=True)


def window_index(freq):
    """UTC index covering every trading day from warm-up start to study end."""
    start = pd.Timestamp(WARMUP_START, tz=TRADING_DAY_TZ).tz_convert("UTC")
    end = pd.Timestamp(STUDY_END, tz=TRADING_DAY_TZ).tz_convert("UTC") + pd.Timedelta(days=1)
    return pd.date_range(start, end, freq=freq, inclusive="left").tz_localize(None)


# ---------------------------------------------------------------- prices
def load_prices():
    raw = pd.read_csv(RAW / "ember" / "Ireland.csv")
    df = pd.DataFrame({
        "ts_utc": pd.to_datetime(raw["Datetime (UTC)"]),
        "price_eur_mwh": raw["Price (EUR/MWhe)"].astype(float),
    })
    df = add_time_columns(df)

    log("## 1. Day-ahead prices (Ember / SEMOpx)")
    log(f"- Rows in file: {len(df):,}; span {df.ts_utc.min()} to {df.ts_utc.max()} UTC")
    dup = df.ts_utc.duplicated().sum()
    log(f"- Duplicate timestamps: {dup}")
    # Ember's own local column must agree with our tz conversion
    ember_local = pd.to_datetime(raw["Datetime (Local)"])
    log(f"- Rows where Ember local time disagrees with UTC->{LOCAL_TZ}: "
        f"{(ember_local != df.ts_local).sum()}")

    idx = window_index("h")
    w = df[df.ts_utc.isin(idx)]
    missing = idx.difference(w.ts_utc)
    log(f"- Hours expected in warm-up + study window: {len(idx):,}; present {len(w):,}; "
        f"missing {len(missing)}")

    per_day = w.groupby("trading_date").size()
    odd = per_day[per_day != 24]
    log(f"- Trading days with != 24 hours: {len(odd)} "
        + (f"({', '.join(f'{d}: {n}h' for d, n in odd.items())})" if len(odd) else ""))
    expected_odd = {d for d in odd.index if pd.Timestamp(d).month in (3, 10)
                    and pd.Timestamp(d).dayofweek == 6 and pd.Timestamp(d).day > 24}
    log(f"  All of these are last-Sunday-of-March/October DST days: {set(odd.index) == expected_odd}. "
        "EU clocks change simultaneously at 01:00 UTC, so DST trading days have 23/25 periods.")

    p = w.price_eur_mwh
    log(f"- Price range {p.min():.2f} to {p.max():.2f} EUR/MWh; mean {p.mean():.2f}")
    log(f"- Negative-price hours: {(p < 0).sum()} ({(p < 0).mean():.2%}); "
        f"zero-price hours: {(p == 0).sum()}; hours <= 5 EUR: {(p <= 5).sum()}")
    q1, q3 = p.quantile([0.25, 0.75])
    hi = q3 + 3 * (q3 - q1)
    log(f"- Hours above Q3 + 3*IQR ({hi:.1f} EUR/MWh): {(p > hi).sum()} "
        "(kept: scarcity spikes are real price signals)")
    top = w.nlargest(5, "price_eur_mwh")[["ts_local", "price_eur_mwh"]]
    log("- Five highest prices (local time): "
        + "; ".join(f"{r.ts_local:%Y-%m-%d %H:%M} {r.price_eur_mwh:.0f}" for r in top.itertuples()))
    log()
    return df


# ---------------------------------------------------------------- system data
def load_sgd():
    frames = []
    for f in sorted((RAW / "sgd").glob("*.json")):
        rows = json.loads(f.read_text())["Rows"]
        frames.append(pd.DataFrame(rows))
    raw = pd.concat(frames, ignore_index=True)
    # Timestamps are UTC/GMT: on the spring DST day local 01:00-01:45 does not exist
    # yet SGD reports it, and on the autumn day local 01:xx appears only once.
    raw["ts_utc"] = pd.to_datetime(raw["EffectiveTime"], format="%d-%b-%Y %H:%M:%S")
    raw["Value"] = pd.to_numeric(raw["Value"], errors="coerce")

    log("## 2. Actual wind and system demand (EirGrid Smart Grid Dashboard, all-island)")
    log(f"- Raw files: {len(frames)}; raw rows: {len(raw):,}")
    dup = raw.duplicated(["ts_utc", "FieldName"]).sum()
    conflict = (raw.groupby(["ts_utc", "FieldName"]).Value.nunique() > 1).sum()
    log(f"- Duplicate (timestamp, field) rows: {dup} (conflicting values: {conflict}); "
        "duplicates from overlapping chunk edges are dropped, first value kept")
    raw = raw.drop_duplicates(["ts_utc", "FieldName"])

    qh = raw.pivot(index="ts_utc", columns="FieldName", values="Value")
    qh = qh.rename(columns={"WIND_ACTUAL": "wind_mw", "SYSTEM_DEMAND": "demand_mw"})
    qh = qh.reindex(columns=["wind_mw", "demand_mw"])
    idx = window_index("15min")
    qh = qh.reindex(idx)
    qh.index.name = "ts_utc"

    for col in ["wind_mw", "demand_mw"]:
        s = qh[col]
        missing = s.index[s.isna()]
        runs = gap_runs(missing, "15min")
        log(f"- {col}: expected {len(idx):,} quarter-hours, missing {len(missing):,} "
            f"({len(missing) / len(idx):.2%}) in {len(runs)} gap(s)")
        if len(runs):
            longest = runs.nlargest(5, "n")
            log("  Longest gaps (UTC): " + "; ".join(
                f"{r.start:%Y-%m-%d %H:%M} to {r.end:%Y-%m-%d %H:%M} ({r.n} qh)"
                for r in longest.itertuples()))
        log(f"  Range {s.min():.0f} to {s.max():.0f} MW; mean {s.mean():.0f}")

    neg_wind = (qh.wind_mw < 0).sum()
    log(f"- Negative wind readings: {neg_wind} (SCADA net export can dip below zero at "
        "near-zero output; kept as reported)")
    bad_demand = ((qh.demand_mw < 1500) | (qh.demand_mw > 9000)).sum()
    log(f"- Demand outside a plausible 1,500-9,000 MW band: {bad_demand}")
    # Frozen sensor check: identical values for >= 2 hours
    for col in ["wind_mw", "demand_mw"]:
        s = qh[col]
        same = s.diff().eq(0)
        streak = same.groupby((~same).cumsum()).cumsum().max()
        log(f"- Longest run of identical consecutive {col} values: {int(streak) + 1} qh")

    hourly = qh.resample("h").agg(["mean", "count"])
    hourly.columns = ["wind_mw", "n_qh_wind", "demand_mw", "n_qh_demand"]
    # An hour with fewer than 4 quarter-hours is averaged from what exists; flagged by n_qh_*.
    hourly = hourly.reset_index()
    partial = ((hourly.n_qh_wind.between(1, 3)) | (hourly.n_qh_demand.between(1, 3))).sum()
    log(f"- Hours built from 1-3 quarter-hours (partial, flagged via n_qh_*): {partial}")
    for c in ["wind_mw", "demand_mw"]:
        hourly.loc[hourly[f"n_qh_{c.split('_')[0]}"] == 0, c] = np.nan
    log()
    return qh.reset_index(), hourly


def check_sgd_timezone(prices, hourly):
    """Independent check of the SGD time convention: people's demand follows local clocks,
    so if SGD is UTC, the hour (UTC) of the evening demand peak should be one hour earlier
    in summer (IST = UTC+1) than in winter."""
    log("## 3. Time-zone verification for Smart Grid Dashboard data")
    h = hourly.dropna(subset=["demand_mw"]).copy()
    h["month"] = h.ts_utc.dt.month
    h["hour_utc"] = h.ts_utc.dt.hour
    out = {}
    for label, months in [("winter (Dec-Feb)", [12, 1, 2]), ("summer (Jun-Aug)", [6, 7, 8])]:
        prof = h[h.month.isin(months)].groupby("hour_utc").demand_mw.mean()
        if prof.loc[14:22].empty:
            log(f"- {label}: no demand data yet - CHECK NOT RUN")
            log()
            return
        evening = prof.loc[14:22]
        out[label] = int(evening.idxmax())
        log(f"- {label}: evening demand peak at {out[label]:02d}:00 in SGD timestamps")
    shift = list(out.values())[0] - list(out.values())[1]
    verdict = ("consistent with UTC timestamps" if shift == 1
               else "NOT the expected 1-hour shift - investigate")
    log(f"- Winter minus summer peak hour = {shift} h -> {verdict}")
    log()


# ---------------------------------------------------------------- weather
def load_weather():
    frames = []
    for f in sorted((RAW / "openmeteo").glob("*.json")):
        j = json.loads(f.read_text())
        hr = j["hourly"]
        frames.append(pd.DataFrame({
            "ts_utc": pd.to_datetime(hr["time"]),
            "model": j["model"],
            "location": j["location_name"],
            "wind_speed_100m": hr["wind_speed_100m_previous_day2"],
            "temperature_2m": hr["temperature_2m_previous_day2"],
        }))
    df = pd.concat(frames, ignore_index=True)
    idx = window_index("h")
    df = df[df.ts_utc.isin(idx)]

    log("## 4. Weather forecasts (Open-Meteo Previous Runs, 48 h-ahead vintage)")
    log(f"- Models: {', '.join(sorted(df.model.unique()))}; "
        f"locations: {', '.join(sorted(df.location.unique()))}")
    g = df.groupby(["model", "location"])
    summary = g.agg(hours=("ts_utc", "nunique"), null_wind=("wind_speed_100m", lambda s: s.isna().sum()),
                    null_temp=("temperature_2m", lambda s: s.isna().sum()),
                    max_wind=("wind_speed_100m", "max"))
    log(f"- Expected hours per series: {len(idx):,}")
    log("- Hours present / nulls per series: all series have "
        f"{summary.hours.min():,}-{summary.hours.max():,} hours, "
        f"{summary.null_wind.sum()} null wind and {summary.null_temp.sum()} null temperature values")
    log(f"- Max forecast 100 m wind speed: {summary.max_wind.max():.1f} m/s "
        "(values above ~40 m/s would be suspicious)")
    log()
    return df


def check_forecast_vs_actual(weather, hourly):
    """Sanity check that the 48 h-ahead forecast carries real information about wind output."""
    log("## 5. Forecast sanity check (descriptive only, not used for model selection)")
    w = (weather[weather.model == "ecmwf_ifs025"].groupby("ts_utc").wind_speed_100m.mean()
         .rename("fc").reset_index())
    m = w.merge(hourly[["ts_utc", "wind_mw"]], on="ts_utc").dropna()
    r = m.fc.corr(m.wind_mw)
    r_cubic = (m.fc.clip(upper=13) ** 3).corr(m.wind_mw)
    log(f"- Correlation of mean 48 h-ahead wind speed with actual all-island wind output: "
        f"{r:.3f} (linear), {r_cubic:.3f} (capped cubic, closer to a power curve)")
    log()


# ---------------------------------------------------------------- load
def load_db(prices, qh, hourly, weather):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute((SQL_DIR / "schema.sql").read_text())
    con.execute("INSERT INTO price_dam SELECT ts_utc, ts_local, trading_date, price_eur_mwh "
                "FROM prices")
    con.execute("INSERT INTO system_qh SELECT ts_utc, wind_mw, demand_mw FROM qh")
    con.execute("INSERT INTO system_hourly SELECT ts_utc, wind_mw, demand_mw, n_qh_wind, "
                "n_qh_demand FROM hourly")
    con.execute("INSERT INTO weather_fc SELECT ts_utc, model, location, wind_speed_100m, "
                "temperature_2m FROM weather")
    con.execute("CREATE OR REPLACE TABLE study_period AS SELECT ?::DATE AS start_date, "
                "?::DATE AS end_date", [STUDY_START, STUDY_END])
    con.execute((SQL_DIR / "views.sql").read_text())

    log("## 6. Database load")
    for t in ["price_dam", "system_qh", "system_hourly", "weather_fc"]:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        log(f"- {t}: {n:,} rows")
    n, n_full = con.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE wind_mw IS NOT NULL AND demand_mw IS NOT NULL "
        "AND fc_wind_speed_100m IS NOT NULL) FROM hourly_panel WHERE in_study").fetchone()
    log(f"- hourly_panel, study period: {n:,} hours; {n_full:,} with price, actual wind, "
        f"demand and forecast all present ({n_full / n:.2%})")
    con.close()


def main():
    log("# Data quality log")
    log(f"Generated by `src/build_db.py` on {datetime.now():%Y-%m-%d %H:%M}. "
        f"Study period (trading dates): {STUDY_START} to {STUDY_END}; "
        f"warm-up from {WARMUP_START}. Nothing is imputed.")
    log()
    prices = load_prices()
    qh, hourly = load_sgd()
    check_sgd_timezone(prices, hourly)
    weather = load_weather()
    check_forecast_vs_actual(weather, hourly)
    load_db(prices, qh, hourly, weather)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "data_quality_log.md").write_text("\n".join(log_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
