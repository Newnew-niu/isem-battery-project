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


def study_mask(ts_utc):
    """True for timestamps whose trading date lies in the study period (excludes warm-up)."""
    td = ts_utc.dt.tz_localize("UTC").dt.tz_convert(TRADING_DAY_TZ).dt.date
    return (td >= STUDY_START) & (td <= STUDY_END)


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

    s = w[study_mask(w.ts_utc)]
    p = s.price_eur_mwh
    log(f"- Study period: {len(s):,} hours")
    log(f"- Price range {p.min():.2f} to {p.max():.2f} EUR/MWh; mean {p.mean():.2f}")
    log(f"- Negative-price hours: {(p < 0).sum()} ({(p < 0).mean():.2%}); "
        f"zero-price hours: {(p == 0).sum()}; hours <= 5 EUR: {(p <= 5).sum()}")
    q1, q3 = p.quantile([0.25, 0.75])
    hi = q3 + 3 * (q3 - q1)
    log(f"- Hours above Q3 + 3*IQR ({hi:.1f} EUR/MWh): {(p > hi).sum()} "
        "(kept: scarcity spikes are real price signals)")
    top = s.nlargest(5, "price_eur_mwh")[["ts_local", "price_eur_mwh"]]
    log("- Five highest prices (local time): "
        + "; ".join(f"{r.ts_local:%Y-%m-%d %H:%M} {r.price_eur_mwh:.0f}" for r in top.itertuples()))
    log()
    return df


# ---------------------------------------------------------------- system data
EC_FIELDS = {"Wind onshore": "wind", "Load": "load"}
EC_AREAS = {"ie": "roi", "uk": "ni"}  # Energy-Charts 'uk' = Northern Ireland (SONI area)


def load_system():
    """Energy-Charts half-hourly wind and load per control area -> half-hourly and hourly tables."""
    frames = []
    for f in sorted((RAW / "energycharts").glob("public_power_*.json")):
        area = EC_AREAS[f.stem.split("_")[2]]
        j = json.loads(f.read_text())
        ts = pd.to_datetime(j["unix_seconds"], unit="s")  # UTC, start of the half-hour
        for p in j["production_types"]:
            if p["name"] in EC_FIELDS:
                frames.append(pd.DataFrame({"ts_utc": ts, "col": f"{EC_FIELDS[p['name']]}_{area}_mw",
                                            "value": pd.to_numeric(p["data"], errors="coerce")}))
    raw = pd.concat(frames, ignore_index=True)

    log("## 2. Actual wind and load (Energy-Charts / ENTSO-E, ROI and NI control areas)")
    dup = raw.duplicated(["ts_utc", "col"]).sum()
    log(f"- Duplicate (timestamp, series) rows: {dup}")
    raw = raw.drop_duplicates(["ts_utc", "col"])
    hh = raw.pivot(index="ts_utc", columns="col", values="value")
    cols = ["wind_roi_mw", "wind_ni_mw", "load_roi_mw", "load_ni_mw"]
    hh = hh.reindex(columns=cols)
    idx = window_index("30min")
    hh = hh.reindex(idx)
    hh.index.name = "ts_utc"
    in_study = study_mask(hh.index.to_series()).values

    # Frozen-feed rule: a value repeated for >= 4 consecutive half-hours (2 h) is a stuck
    # reading, not physics - load never stays flat that long, nor does wind above calm.
    # Values < 5 MW are exempt: flat near-zero wind on calm nights is real.
    # Confirmed case: 2025-05-01 01:30-09:00 UTC, ROI wind and load both frozen, and EirGrid's
    # own dashboard export is frozen over the same hours (upstream SCADA outage).
    for col in cols:
        s = hh[col]
        run_id = s.ne(s.shift()).cumsum()
        run_len = s.groupby(run_id).transform("size")
        frozen = (run_len >= 4) & s.notna() & (s >= 5)
        if frozen.any():
            log(f"- {col}: {int(frozen.sum())} half-hours set to NULL as a frozen feed "
                f"({', '.join(sorted({f'{t:%Y-%m-%d}' for t in s.index[frozen]}))})")
        hh.loc[frozen, col] = np.nan

    log(f"- Half-hours expected in study period: {in_study.sum():,}")
    for col in cols:
        s = hh.loc[in_study, col]
        missing = s.index[s.isna()]
        runs = gap_runs(missing, "30min")
        log(f"- {col}: missing {len(missing):,} ({len(missing) / len(s):.2%}) in {len(runs)} gap(s); "
            f"range {s.min():.0f} to {s.max():.0f} MW, mean {s.mean():.0f}")
        if len(runs):
            longest = runs.nlargest(3, "n")
            log("  Longest gaps (UTC): " + "; ".join(
                f"{r.start:%Y-%m-%d %H:%M} to {r.end:%Y-%m-%d %H:%M} ({r.n} hh)"
                for r in longest.itertuples()))
    log("- NI load is not published for long stretches (from Nov 2025 entirely), so wind "
        "penetration is measured on ROI only (wind_roi / load_roi); see DECISIONS.md D8.")

    neg = (hh[["wind_roi_mw", "wind_ni_mw"]] < 0).sum().sum()
    log(f"- Negative wind values: {neg}")
    bad_load = ((hh.load_roi_mw < 1500) | (hh.load_roi_mw > 8000)).sum()
    log(f"- ROI load outside a plausible 1,500-8,000 MW band: {bad_load}")
    for col in ["wind_roi_mw", "load_roi_mw"]:
        s = hh[col].dropna()
        same = s.diff().eq(0)
        streak = same.groupby((~same).cumsum()).cumsum().max()
        log(f"- Longest run of identical consecutive {col} values: {int(streak) + 1} half-hours "
            "(long runs would suggest a frozen feed)")

    # All-island wind only where both areas are reported.
    hh["wind_all_mw"] = hh.wind_roi_mw + hh.wind_ni_mw

    agg = hh.resample("h")
    hourly = agg.mean()
    counts = agg.count()
    # Hours with only one of two half-hours are kept (mean of what exists) and flagged.
    hourly["n_hh_wind_roi"] = counts.wind_roi_mw
    hourly["n_hh_load_roi"] = counts.load_roi_mw
    for c in hourly.columns:
        if c.endswith("_mw"):
            hourly.loc[counts[c] == 0, c] = np.nan
    hourly = hourly.reset_index()
    partial = ((hourly.n_hh_wind_roi == 1) | (hourly.n_hh_load_roi == 1)).sum()
    log(f"- Hours built from a single half-hour (flagged via n_hh_*): {partial}")
    log()
    return hh.reset_index(), hourly


def validate_against_eirgrid(hh):
    """Cross-check Energy-Charts all-island wind with EirGrid Smart Grid Dashboard data
    downloaded during Phase 1 (only runs if those raw files are present)."""
    log("## 3. Cross-check against EirGrid Smart Grid Dashboard (all-island wind)")
    ec = hh.set_index("ts_utc").wind_all_mw
    checks = []
    csvs = sorted((RAW / "sgd_manual").glob("*.csv"))
    if csvs:
        c = pd.concat(pd.read_csv(f) for f in csvs)
        t = pd.to_datetime(c["DATE & TIME"], format="%d %B %Y %H:%M")
        c = c.assign(t=t).drop_duplicates("t")
        # CSV export is Irish local time; the repeated autumn hour is ambiguous -> dropped.
        utc = (c.t.dt.tz_localize(LOCAL_TZ, ambiguous="NaT", nonexistent="NaT")
               .dt.tz_convert("UTC").dt.tz_localize(None))
        checks.append(("manual CSV export (local time)",
                       pd.Series(c["ACTUAL WIND(MW)"].values, index=utc).dropna()))
    api_files = sorted((RAW / "sgd").glob("windactual_*.json"))
    if api_files:
        a = pd.concat(pd.DataFrame(json.loads(f.read_text())["Rows"]) for f in api_files)
        t = pd.to_datetime(a.EffectiveTime, format="%d-%b-%Y %H:%M:%S")
        checks.append(("data-service JSON (timestamps read as UTC)",
                       pd.Series(a.Value.astype(float).values, index=t)))
    if not checks:
        log("- EirGrid reference files not present - CHECK NOT RUN")
        log()
        return
    for name, s in checks:
        res = []
        for off in [-60, -30, 0, 30, 60]:
            s2 = s.copy()
            s2.index = s2.index + pd.Timedelta(minutes=off)
            m = pd.concat([s2.resample("30min").mean().rename("sgd"), ec], axis=1).dropna()
            res.append((off, len(m), m.sgd.corr(m.wind_all_mw), (m.wind_all_mw - m.sgd).abs().mean(),
                        (m.wind_all_mw - m.sgd).mean()))
        best = max(res, key=lambda r: r[2])
        log(f"- vs {name}: best alignment at shift {best[0]:+d} min "
            f"(n={best[1]:,}, corr={best[2]:.4f}, MAE={best[3]:.0f} MW, mean diff={best[4]:+.1f} MW); "
            "corr by shift: " + ", ".join(f"{r[0]:+d}: {r[2]:.3f}" for r in res))
    log("- Interpretation: Energy-Charts (UTC) matches the local-time CSV with no shift, and "
        "matches the data-service JSON only after a -60 min shift, i.e. the data-service "
        "timestamps are also Irish local time, not UTC as first inferred (DECISIONS.md D5).")
    log()


def check_load_timezone(hourly):
    """Demand follows local clocks, so with correct UTC timestamps the evening peak should
    appear one hour earlier (in UTC) in summer than in winter."""
    log("## 4. Time-zone sanity check on ROI load")
    h = hourly.dropna(subset=["load_roi_mw"]).copy()
    h = h[study_mask(h.ts_utc)]
    out = {}
    for label, months in [("winter (Dec-Feb)", [12, 1, 2]), ("summer (Jun-Aug)", [6, 7, 8])]:
        prof = h[h.ts_utc.dt.month.isin(months)].groupby(h.ts_utc.dt.hour).load_roi_mw.mean()
        out[label] = int(prof.loc[14:22].idxmax())
        log(f"- {label}: evening load peak at {out[label]:02d}:00 UTC")
    shift = out["winter (Dec-Feb)"] - out["summer (Jun-Aug)"]
    verdict = "consistent with UTC timestamps" if shift == 1 else "NOT the expected 1-hour shift - investigate"
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

    log("## 5. Weather forecasts (Open-Meteo Previous Runs, 48 h-ahead vintage)")
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
    log("## 6. Forecast sanity check (descriptive only, not used for model selection)")
    w = (weather[weather.model == "ecmwf_ifs025"].groupby("ts_utc").wind_speed_100m.mean()
         .rename("fc").reset_index())
    m = w.merge(hourly[["ts_utc", "wind_all_mw"]], on="ts_utc").dropna()
    m = m[study_mask(m.ts_utc)]
    r = m.fc.corr(m.wind_all_mw)
    r_cubic = (m.fc.clip(upper=13) ** 3).corr(m.wind_all_mw)
    log(f"- Correlation of mean 48 h-ahead wind speed with actual all-island wind output "
        f"(study period, n={len(m):,}): {r:.3f} (linear), {r_cubic:.3f} (capped cubic)")
    log()


# ---------------------------------------------------------------- load
def load_db(prices, hh, hourly, weather):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute((SQL_DIR / "schema.sql").read_text())
    con.execute("INSERT INTO price_dam SELECT ts_utc, ts_local, trading_date, price_eur_mwh "
                "FROM prices")
    con.execute("INSERT INTO system_hh SELECT ts_utc, wind_roi_mw, wind_ni_mw, wind_all_mw, "
                "load_roi_mw, load_ni_mw FROM hh")
    con.execute("INSERT INTO system_hourly SELECT ts_utc, wind_roi_mw, wind_ni_mw, wind_all_mw, "
                "load_roi_mw, load_ni_mw, n_hh_wind_roi, n_hh_load_roi FROM hourly")
    con.execute("INSERT INTO weather_fc SELECT ts_utc, model, location, wind_speed_100m, "
                "temperature_2m FROM weather")
    con.execute("CREATE OR REPLACE TABLE study_period AS SELECT ?::DATE AS start_date, "
                "?::DATE AS end_date", [STUDY_START, STUDY_END])
    con.execute((SQL_DIR / "views.sql").read_text())

    log("## 7. Database load")
    for t in ["price_dam", "system_hh", "system_hourly", "weather_fc"]:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        log(f"- {t}: {n:,} rows")
    n, n_full = con.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE wind_share IS NOT NULL AND wind_all_mw IS NOT NULL "
        "AND fc_wind_speed_100m IS NOT NULL) FROM hourly_panel WHERE in_study").fetchone()
    log(f"- hourly_panel, study period: {n:,} hours; {n_full:,} with price, ROI wind share, "
        f"all-island wind and forecast all present ({n_full / n:.2%})")
    con.close()


def main():
    log("# Data quality log")
    log(f"Generated by `src/build_db.py` on {datetime.now():%Y-%m-%d %H:%M}. "
        f"Study period (trading dates): {STUDY_START} to {STUDY_END}; "
        f"warm-up from {WARMUP_START}. Nothing is imputed.")
    log()
    prices = load_prices()
    hh, hourly = load_system()
    validate_against_eirgrid(hh)
    check_load_timezone(hourly)
    weather = load_weather()
    check_forecast_vs_actual(weather, hourly)
    load_db(prices, hh, hourly, weather)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "data_quality_log.md").write_text("\n".join(log_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
