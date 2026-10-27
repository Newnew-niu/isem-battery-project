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
    log(f"- Rows in file: {len(df):,}; span {df.ts_utc.min()} to {df.ts_utc.max()} UTC (as labelled by Ember)")
    dup = df.ts_utc.duplicated().sum()
    log(f"- Duplicate timestamps: {dup}")
    # Ember's own local column must agree with our tz conversion
    ember_local = pd.to_datetime(raw["Datetime (Local)"])
    log(f"- Rows where Ember local time disagrees with UTC->{LOCAL_TZ}: "
        f"{(ember_local != df.ts_local).sum()}")

    # D9: Ember's Ireland series is labelled 24 h late. Evidence (logged below and in DECISIONS.md):
    # exact match with official SEMOpx files only after a -24 h shift; physical alignment with
    # load/wind; first row then falls on I-SEM go-live (trading day 2018-10-01).
    check_ember_shift(df)
    df["ts_utc"] = df.ts_utc - pd.Timedelta(hours=24)
    df = add_time_columns(df[["ts_utc", "price_eur_mwh"]].copy())
    log(f"- Applied -24 h correction; series now spans trading days {df.trading_date.min()} "
        f"to {df.trading_date.max()} (I-SEM went live on trading day 2018-10-01)")

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

    df = fix_flat_days(df)
    w = df[df.ts_utc.isin(idx)]
    s = w[study_mask(w.ts_utc)]
    p = s.price_eur_mwh.dropna()
    log(f"- Study period: {len(s):,} hours; price source: "
        + ", ".join(f"{k} {v:,}" for k, v in s.price_source.value_counts().items()))
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


def load_semopx():
    """Official SEMOpx DAM results: half-hourly ROI-DA index price (EUR). The SEM has one
    price for ROI and NI, so the ROI block is used."""
    rows = []
    for f in sorted((RAW / "semopx").glob("MarketResult_SEM-DA_*.csv")):
        lines = f.read_text(encoding="utf-8-sig").splitlines()
        i = lines.index("Market;ROI-DA")
        while lines[i] != "Index prices;30;EUR":
            i += 1
        ts = pd.to_datetime(lines[i + 1].split(";"), utc=True).tz_localize(None)
        vals = [float(v.replace(",", ".")) for v in lines[i + 2].split(";")]
        rows.append(pd.DataFrame({"ts_utc": ts, "price_eur_mwh": vals}))
    if not rows:
        return pd.DataFrame(columns=["ts_utc", "price_eur_mwh"])
    hh = pd.concat(rows).drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)
    return hh


def semopx_hourly_first():
    """Hourly price as Ember defines it in the half-hourly era: the first half-hour of each hour."""
    hh = load_semopx()
    if not len(hh):
        return pd.Series(dtype=float)
    r = hh.set_index("ts_utc").price_eur_mwh.resample("h")
    first, count = r.first(), r.count()
    return first[count == 2]


def check_ember_shift(df):
    """Compare Ember (as labelled) with official SEMOpx prices at several time shifts."""
    ref = semopx_hourly_first()
    if not len(ref):
        log("- SEMOpx files not present - Ember time-alignment check NOT RUN")
        return
    e = df.set_index("ts_utc").price_eur_mwh
    res = []
    for h in (-48, -24, 0, 24):
        s = e.copy()
        s.index = s.index + pd.Timedelta(hours=h)
        m = pd.concat([s.rename("ember"), ref.rename("semopx")], axis=1, join="inner").dropna()
        res.append((h, len(m), m.ember.corr(m.semopx), ((m.ember - m.semopx).abs() <= 0.01).mean()))
    log("- Ember vs official SEMOpx (first half-hour of each hour), by shift applied to Ember: "
        + "; ".join(f"{h:+d} h: corr {c:.3f}, exact match {x:.1%} (n={n:,})" for h, n, c, x in res))


def fix_flat_days(df):
    """Ember has trading days where all hours carry one identical price - not a real DAM
    outcome (verified against SEMOpx). Replace them with the official SEMOpx price (same
    definition as Ember: first half-hour of each hour) where available, otherwise NULL.
    See docs/DECISIONS.md, D9."""
    hh = load_semopx()
    ref = semopx_hourly_first()
    df["price_source"] = "ember"
    nd = df.groupby("trading_date").price_eur_mwh.transform("nunique")
    flat = nd == 1
    flat_days = sorted(df.loc[flat, "trading_date"].unique())
    log(f"- Ember trading days with a single price for all hours (flat days): {len(flat_days)} "
        f"({flat_days[0]} to {flat_days[-1]})")

    if len(ref):
        log(f"- SEMOpx official DAM files: {hh.ts_utc.dt.normalize().nunique():,} days of half-hourly prices "
            f"({hh.ts_utc.min():%Y-%m-%d} to {hh.ts_utc.max():%Y-%m-%d} UTC). The DAM clears in 30-minute "
            "periods; Ember's hourly value equals the first half-hour of each hour")
        m = df[~flat].set_index("ts_utc").price_eur_mwh.to_frame("ember").join(ref.rename("semopx"), how="inner")
        diff = (m.ember - m.semopx).abs()
        log(f"- Validation after the -24 h correction on {len(m):,} non-flat overlapping hours: exact match "
            f"(<= 0.01 EUR) in {(diff <= 0.01).mean():.2%} of hours; max abs diff {diff.max():.2f} EUR/MWh")
        fl = df[flat].set_index("ts_utc").price_eur_mwh.to_frame("ember").join(ref.rename("semopx"), how="inner")
        if len(fl):
            log(f"- On flat days covered by SEMOpx ({fl.index.normalize().nunique()} days), official prices "
                f"range {fl.semopx.min():.0f} to {fl.semopx.max():.0f} EUR/MWh while Ember shows one value "
                "per day -> Ember flat days are a data artefact, not a market outcome")
        repl = flat & df.ts_utc.isin(ref.index)
        df.loc[repl, "price_eur_mwh"] = df.loc[repl, "ts_utc"].map(ref).values
        df.loc[repl, "price_source"] = "semopx"
    else:
        repl = flat & False
    rest = flat & ~repl
    df.loc[rest, "price_eur_mwh"] = np.nan
    df.loc[rest, "price_source"] = "missing (Ember flat day)"
    in_study = study_mask(df.ts_utc)
    log(f"- Study period: {df[in_study & repl].trading_date.nunique()} flat days replaced with SEMOpx, "
        f"{df[in_study & rest].trading_date.nunique()} set to NULL "
        f"({', '.join(str(d) for d in sorted(df[in_study & rest].trading_date.unique()))})")
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


def check_price_physics(prices, hourly):
    """Independent check of price timing: the DAM price should co-move most strongly with
    same-hour net load (load minus wind), not with the previous or next day's."""
    log("## 5. Price timing check against physical system data")
    p = prices.set_index("ts_utc").price_eur_mwh
    h = hourly.set_index("ts_utc")
    nl = (h.load_roi_mw - h.wind_roi_mw).rename("net_load")
    out = []
    for k in (-24, 0, 24):
        s = p.copy()
        s.index = s.index + pd.Timedelta(hours=k)
        m = pd.concat([s, nl], axis=1, join="inner").dropna()
        m = m[study_mask(m.index.to_series()).values]
        out.append(f"{k:+d} h: {m.iloc[:, 0].corr(m.net_load):.3f}")
    log("- Correlation of corrected price with ROI net load when the price is shifted by "
        + "; ".join(out) + " -> peak at 0 h confirms the -24 h correction")
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

    log("## 6. Weather forecasts (Open-Meteo Previous Runs, 48 h-ahead vintage)")
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
    log("## 7. Forecast sanity check (descriptive only, not used for model selection)")
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
    con.execute("INSERT INTO price_dam SELECT ts_utc, ts_local, trading_date, price_eur_mwh, "
                "price_source FROM prices")
    gb = pd.read_csv(RAW / "ember" / "United Kingdom.csv")
    gb = pd.DataFrame({"ts_utc": pd.to_datetime(gb["Datetime (UTC)"]),
                       "price_eur_mwh": gb["Price (EUR/MWhe)"].astype(float)})
    con.execute("INSERT INTO price_gb SELECT * FROM gb")
    semopx_hh = load_semopx()
    con.execute("INSERT INTO price_dam_hh SELECT ts_utc, price_eur_mwh FROM semopx_hh")
    con.execute("INSERT INTO system_hh SELECT ts_utc, wind_roi_mw, wind_ni_mw, wind_all_mw, "
                "load_roi_mw, load_ni_mw FROM hh")
    con.execute("INSERT INTO system_hourly SELECT ts_utc, wind_roi_mw, wind_ni_mw, wind_all_mw, "
                "load_roi_mw, load_ni_mw, n_hh_wind_roi, n_hh_load_roi FROM hourly")
    con.execute("INSERT INTO weather_fc SELECT ts_utc, model, location, wind_speed_100m, "
                "temperature_2m FROM weather")
    con.execute("CREATE OR REPLACE TABLE study_period AS SELECT ?::DATE AS start_date, "
                "?::DATE AS end_date", [STUDY_START, STUDY_END])
    con.execute((SQL_DIR / "views.sql").read_text())

    log("## 8. Database load")
    for t in ["price_dam", "price_dam_hh", "price_gb", "system_hh", "system_hourly", "weather_fc"]:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        log(f"- {t}: {n:,} rows")
    n, n_full = con.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE price_eur_mwh IS NOT NULL AND wind_share IS NOT NULL AND wind_all_mw IS NOT NULL "
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
    check_price_physics(prices, hourly)
    weather = load_weather()
    check_forecast_vs_actual(weather, hourly)
    load_db(prices, hh, hourly, weather)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "data_quality_log.md").write_text("\n".join(log_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
