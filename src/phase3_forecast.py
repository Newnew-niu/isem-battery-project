"""Phase 3 - day-ahead price forecasting with a strict no-leakage rule.

Information set at DAM gate closure (11:00 Irish time on D-1) for every hour of trading day D:
- prices of D-1 and earlier (D-1 was cleared at the D-2 auction, so it is known)
- GB day-ahead prices of D-1 and earlier (context for the gas-driven price level)
- weather forecasts issued >= 48 h before valid time (Open-Meteo previous_day2, see D2)
- calendar (hour, weekday, Irish public holidays, season)
Actual wind is used ONLY in the clearly labelled "perfect wind forecast" upper bound.

Split: Year 1 (trading days Oct 2024-Sep 2025) for training and model selection
(inner validation Jul-Sep 2025); Year 2 (Oct 2025-Sep 2026) is the untouched test set.
Models (c) and (d) are re-fitted at the start of every test month on all data before it
(expanding window), as a desk would.

Outputs: table price_forecast in DuckDB, reports/phase3_metrics.json, figures p3_*.png.
"""
import json

import duckdb
import holidays
import lightgbm as lgb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm

import plot_style as ps
from config import DB_PATH, REPORTS

ps.apply()
SEED = 42
TEST_START = pd.Timestamp("2025-10-01")
VAL_START = pd.Timestamp("2025-07-01")
LOCS = ["donegal", "north_mayo", "kerry", "west_cork", "offaly", "tyrone"]
IE_HOLIDAYS = holidays.country_holidays("IE", years=range(2024, 2027))
M = {}


# ---------------------------------------------------------------- data & features
def load(con):
    p = con.execute("""
        SELECT h.ts_utc, h.ts_local, h.trading_date, h.price_eur_mwh AS price, h.price_source,
               h.wind_all_mw, h.wind_share, g.price_eur_mwh AS gb
        FROM hourly_panel h LEFT JOIN price_gb g USING (ts_utc)
        WHERE h.trading_date >= DATE '2024-09-01' AND h.trading_date <= DATE '2026-09-30'
        ORDER BY h.ts_utc
    """).df()
    w = con.execute("SELECT ts_utc, model, location, wind_speed_100m AS ws, temperature_2m AS t FROM weather_fc ORDER BY ts_utc, model, location").df()
    return p, w


def weather_features(w, model):
    x = w[w.model == model].pivot(index="ts_utc", columns="location", values=["ws", "t"])
    out = pd.DataFrame(index=x.index)
    for loc in LOCS:
        out[f"ws_{loc}"] = x["ws"][loc]
    out["ws_mean"] = x["ws"][LOCS].mean(axis=1)
    # Rough power curve: output ~ v^3 below rated speed (~12 m/s), flat above
    out["ws_cubed"] = (x["ws"][LOCS].clip(upper=12) ** 3).mean(axis=1)
    out["temp_mean"] = x["t"][LOCS].mean(axis=1)
    return out.reset_index()


def build(p, w, model="ecmwf_ifs025"):
    d = p.copy()
    d["hour"] = d.ts_local.dt.hour
    d["dow"] = d.ts_local.dt.dayofweek
    d["td"] = pd.to_datetime(d.trading_date)
    d["holiday"] = d.td.dt.date.map(lambda x: x in IE_HOLIDAYS).astype(int)
    d["weekend"] = (d.dow >= 5).astype(int)
    doy = d.td.dt.dayofyear
    d["doy_sin"], d["doy_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)

    # Same-local-hour lags by trading day (mean handles the repeated hour on 25-hour days)
    by = d.groupby(["td", "hour"]).price.mean()
    for k in (1, 2, 7):
        lag = by.copy()
        lag.index = lag.index.set_levels(lag.index.levels[0] + pd.Timedelta(days=k), level=0)
        d = d.join(lag.rename(f"p_lag{k}"), on=["td", "hour"])
    day = d.groupby("td").price.agg(["mean", "max", "min"])
    day.index = day.index + pd.Timedelta(days=1)
    d = d.join(day.add_prefix("p_d1_"), on="td")
    gb = d.groupby("td").gb.mean()
    gb.index = gb.index + pd.Timedelta(days=1)
    d = d.join(gb.rename("gb_d1_mean"), on="td")

    d = d.merge(weather_features(w, model), on="ts_utc", how="left")
    d["ws_day_mean"] = d.groupby("td").ws_mean.transform("mean")
    # Perfect-foresight wind features (upper bound only)
    d["act_wind"] = d.wind_all_mw
    d["act_wind_day_mean"] = d.groupby("td").wind_all_mw.transform("mean")
    return d


CAL = ["hour", "dow", "holiday", "weekend", "doy_sin", "doy_cos"]
PRICE_LAGS = ["p_lag1", "p_lag2", "p_lag7", "p_d1_mean", "p_d1_max", "p_d1_min", "gb_d1_mean"]
WIND_FC = ["ws_mean", "ws_cubed", "ws_day_mean", "temp_mean"] + [f"ws_{l}" for l in LOCS]
WIND_ACT = ["act_wind", "act_wind_day_mean", "temp_mean"]
FEATS_LGB = CAL + PRICE_LAGS + WIND_FC
FEATS_LGB_PERFECT = FEATS_LGB + ["act_wind", "act_wind_day_mean"]  # forecast info PLUS actual wind


def linear_design(d):
    """OLS design: hour and weekday dummies, holiday, seasonality, price lags and wind forecast
    (linear + quadratic + capped-cubic + daily mean) and the wind x evening-peak interaction."""
    X = pd.get_dummies(d.hour.astype("category"), prefix="h", drop_first=True, dtype=float)
    X = X.join(pd.get_dummies(d.dow.astype("category"), prefix="dow", drop_first=True, dtype=float))
    for c in ["holiday", "doy_sin", "doy_cos"] + PRICE_LAGS + ["ws_mean", "ws_cubed", "ws_day_mean", "temp_mean"]:
        X[c] = d[c]
    X["ws_mean_sq"] = d.ws_mean ** 2
    X["ws_x_peak"] = d.ws_mean * d.hour.between(17, 20)
    return sm.add_constant(X, has_constant="add")


LGB_GRID = [dict(num_leaves=nl, learning_rate=lr, min_child_samples=mcs)
            for nl in (15, 31, 63) for lr in (0.03, 0.06) for mcs in (20, 60)]


def target(df, mode):
    return df.price - df.p_d1_mean if mode == "delta" else df.price


def untarget(pred, df, mode):
    return pred + df.p_d1_mean.values if mode == "delta" else pred


def lgb_fit(X, y, params, n_estimators):
    m = lgb.LGBMRegressor(objective="l1", n_estimators=n_estimators, subsample=0.8, subsample_freq=1,
                          colsample_bytree=0.8, random_state=SEED, verbose=-1, deterministic=True, force_row_wise=True, n_jobs=4, **params)
    return m.fit(X, y)


def select_lgb(d, feats):
    """Pick hyperparameters AND the target form on the Year-1 inner validation window
    (Jul-Sep 2025) only - the test year is never used for any choice."""
    tr = d[(d.td < VAL_START)].dropna(subset=["price", "p_d1_mean"])
    va = d[(d.td >= VAL_START) & (d.td < TEST_START)].dropna(subset=["price", "p_d1_mean"])
    best, by_mode = None, {}
    for mode in ("raw", "delta"):
        for prm in LGB_GRID:
            m = lgb.LGBMRegressor(objective="l1", n_estimators=3000, subsample=0.8, subsample_freq=1,
                                  colsample_bytree=0.8, random_state=SEED, verbose=-1, deterministic=True, force_row_wise=True, n_jobs=4, **prm)
            m.fit(tr[feats], target(tr, mode), eval_set=[(va[feats], target(va, mode))], eval_metric="l1",
                  callbacks=[lgb.early_stopping(100, verbose=False)])
            pred = untarget(m.predict(va[feats], num_iteration=m.best_iteration_), va, mode)
            mae = np.abs(pred - va.price).mean()
            by_mode[mode] = min(by_mode.get(mode, np.inf), mae)
            if best is None or mae < best[0]:
                best = (mae, prm, m.best_iteration_, mode)
    return {"val_mae": best[0], "params": best[1], "n_estimators": int(best[2]), "target": best[3],
            "val_mae_by_target": by_mode}


def walk_forward(d, fit_predict):
    """Monthly expanding-window re-fit over the test year."""
    out = []
    for month in pd.period_range(TEST_START, "2026-09", freq="M"):
        start = month.start_time
        train = d[(d.td < start)].dropna(subset=["price"])
        test = d[(d.td >= start) & (d.td <= month.end_time)]
        out.append(pd.Series(fit_predict(train, test), index=test.index))
    return pd.concat(out)


# ---------------------------------------------------------------- evaluation
def errs(e):
    return {"MAE": float(np.abs(e).mean()), "RMSE": float(np.sqrt((e ** 2).mean())), "n": int(len(e))}


def main():
    con = duckdb.connect(str(DB_PATH))
    p, w = load(con)
    d = build(p, w)

    # D4 check on the training year: does a location-specific combination of forecasts explain
    # actual wind better than the simple mean?  (training data only)
    tr = d[(d.td >= "2024-10-01") & (d.td < TEST_START)].dropna(subset=["act_wind", "ws_cubed"])
    r2_mean = sm.OLS(tr.act_wind, sm.add_constant(tr[["ws_cubed"]])).fit().rsquared
    cub = pd.DataFrame({l: tr[f"ws_{l}"].clip(upper=12) ** 3 for l in LOCS})
    fit6 = sm.OLS(tr.act_wind, sm.add_constant(cub)).fit()
    M["d4_location_check"] = {"r2_equal_mean_cubed": r2_mean, "r2_six_locations": fit6.rsquared,
                              "coef_six_locations": fit6.params.drop("const").round(3).to_dict()}

    # Hyperparameter selection (Year 1 only)
    M["lgb_selection"] = select_lgb(d[d.td >= "2024-10-01"], FEATS_LGB)
    M["lgb_selection_perfect"] = select_lgb(d[d.td >= "2024-10-01"], FEATS_LGB_PERFECT)
    sel, selp = M["lgb_selection"], M["lgb_selection_perfect"]

    test_mask = (d.td >= TEST_START) & (d.td <= "2026-09-30")
    pred = pd.DataFrame(index=d.index[test_mask])
    pred["naive_d1"] = d.loc[test_mask, "p_lag1"]
    pred["naive_d7"] = d.loc[test_mask, "p_lag7"]

    def ols_fp_mode(mode):
        def fp(train, test):
            train = train.dropna(subset=["p_d1_mean"])
            Xtr = linear_design(train)
            ok = Xtr.notna().all(axis=1)
            fit = sm.OLS(target(train, mode)[ok], Xtr[ok]).fit()
            Xte = linear_design(test).reindex(columns=Xtr.columns, fill_value=0)
            return untarget(fit.predict(Xte).values, test, mode)
        return fp

    # Linear target form chosen on the same Year-1 validation window
    y1 = d[(d.td >= "2024-10-01") & (d.td < TEST_START)]
    tr_, va_ = y1[y1.td < VAL_START].dropna(subset=["price"]), y1[y1.td >= VAL_START]
    lin_val = {m: float(np.nanmean(np.abs(ols_fp_mode(m)(tr_, va_) - va_.price.values))) for m in ("raw", "delta")}
    # Identical in theory (p_d1_mean is a regressor); prefer the simpler raw form on ties
    lin_mode = "raw" if lin_val["raw"] <= lin_val["delta"] + 1e-6 else "delta"
    M["linear_selection"] = {"val_mae_by_target": lin_val, "target": lin_mode}
    ols_fp = ols_fp_mode(lin_mode)

    def lgb_fp(feats, s):
        def fp(train, test):
            train = train.dropna(subset=["p_d1_mean"])
            m = lgb_fit(train[feats], target(train, s["target"]), s["params"], s["n_estimators"])
            return untarget(m.predict(test[feats]), test, s["target"])
        return fp

    dd = d[d.td >= "2024-10-01"]
    pred["linear"] = walk_forward(dd, ols_fp)
    pred["lightgbm"] = walk_forward(dd, lgb_fp(FEATS_LGB, sel))
    pred["lightgbm_perfect_wind"] = walk_forward(dd, lgb_fp(FEATS_LGB_PERFECT, selp))

    # Robustness: second weather model (ICON) through the same pipeline
    di = build(p, w, "icon_seamless")
    di = di[di.td >= "2024-10-01"]
    seli = select_lgb(di, FEATS_LGB)
    pred["lightgbm_icon"] = walk_forward(di, lgb_fp(FEATS_LGB, seli)).reindex(pred.index)
    M["lgb_selection_icon"] = seli

    t = d.loc[test_mask, ["ts_utc", "ts_local", "td", "hour", "price", "wind_share"]].join(pred)
    models = ["naive_d1", "naive_d7", "linear", "lightgbm", "lightgbm_icon", "lightgbm_perfect_wind"]
    common = t.dropna(subset=["price"] + models)
    M["test_rows_total"] = int(test_mask.sum())
    M["test_rows_common"] = len(common)
    M["overall"] = {m: errs(common[m] - common.price) for m in models}
    base = M["overall"]["naive_d1"]["MAE"]
    M["mae_vs_naive_d1_pct"] = {m: 100 * (1 - M["overall"][m]["MAE"] / base) for m in models}
    M["by_hour_mae"] = {m: common.groupby("hour")[[m, "price"]].apply(lambda g: np.abs(g[m] - g.price).mean()).round(2).to_dict()
                        for m in models}
    bands = pd.cut(common.wind_share, [0, .2, .4, .6, 10], labels=["<20%", "20-40%", "40-60%", "60%+"], right=False)
    M["by_wind_band_mae"] = {m: common.groupby(bands, observed=True)[[m, "price"]].apply(lambda g: np.abs(g[m] - g.price).mean()).round(2).to_dict()
                             for m in models}
    M["by_wind_band_n"] = common.groupby(bands, observed=True).size().to_dict()
    M["by_month_mae"] = {m: common.groupby(common.td.dt.to_period("M").astype(str))[[m, "price"]].apply(
        lambda g: np.abs(g[m] - g.price).mean()).round(1).to_dict() for m in ["naive_d1", "linear", "lightgbm"]}
    # Bias: does the model systematically under/over-forecast in the rising-price test year?
    M["mean_error"] = {m: float((common[m] - common.price).mean()) for m in models}

    # Feature importance: final model trained on everything before the last test month
    last = pd.Timestamp("2026-09-01")
    trf = dd[dd.td < last].dropna(subset=["price"])
    trf = trf.dropna(subset=["p_d1_mean"])
    mf = lgb_fit(trf[FEATS_LGB], target(trf, sel["target"]), sel["params"], sel["n_estimators"])
    gain = pd.Series(mf.booster_.feature_importance("gain"), index=FEATS_LGB)
    M["lgb_gain_share"] = (gain / gain.sum()).sort_values(ascending=False).round(4).to_dict()
    Xl = linear_design(trf)
    ok = Xl.notna().all(axis=1)
    fl = sm.OLS(target(trf, lin_mode)[ok], Xl[ok]).fit()
    keep = PRICE_LAGS + ["ws_mean", "ws_mean_sq", "ws_cubed", "ws_day_mean", "ws_x_peak", "temp_mean", "holiday"]
    M["linear_coefs"] = {k: {"coef": float(fl.params[k]), "t": float(fl.tvalues[k])} for k in keep}

    # Store forecasts for Phase 4
    long = t.melt(id_vars=["ts_utc", "td"], value_vars=models, var_name="model", value_name="forecast")
    long = long.rename(columns={"td": "trading_date"})
    con.execute("CREATE OR REPLACE TABLE price_forecast AS SELECT ts_utc, CAST(trading_date AS DATE) AS trading_date, "
                "model, forecast FROM long")
    con.close()

    figures(common, models)
    (REPORTS / "phase3_metrics.json").write_text(json.dumps(M, indent=1, default=float))
    print(json.dumps({k: M[k] for k in ["overall", "mae_vs_naive_d1_pct", "mean_error", "d4_location_check",
                                        "lgb_selection", "lgb_selection_perfect", "linear_selection", "test_rows_common"]}, indent=1, default=float))


LABELS = {"naive_d1": "Naive: same hour yesterday", "naive_d7": "Naive: same hour last week",
          "linear": "Linear regression", "lightgbm": "LightGBM", "lightgbm_icon": "LightGBM (ICON weather)",
          "lightgbm_perfect_wind": "LightGBM + actual wind (upper bound)"}


def figures(c, models):
    # Overall MAE bars
    order = ["naive_d7", "naive_d1", "linear", "lightgbm", "lightgbm_perfect_wind"]
    fig, ax = plt.subplots(figsize=(8.5, 3.6))
    vals = [M["overall"][m]["MAE"] for m in order]
    colors = [ps.NEUTRAL, ps.NEUTRAL, ps.BLUE, ps.BLUE, ps.BLUE_RAMP[2]]
    ax.barh(range(len(order)), vals, color=colors, height=0.6, edgecolor=ps.SURFACE, linewidth=2)
    for i, v in enumerate(vals):
        ax.text(v + 0.4, i, f"{v:.1f}", va="center", fontsize=9)
    ax.set_yticks(range(len(order)), [LABELS[m] for m in order])
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)
    ax.set_xlabel("Mean absolute error, EUR/MWh (test year Oct 2025-Sep 2026)")
    ps.titles(ax, f"LightGBM cuts the naive error by {M['mae_vs_naive_d1_pct']['lightgbm']:.0f}%",
              "Pale bar = 'perfect wind forecast' upper bound, uses actual wind and is not a real forecast")
    fig.tight_layout()
    ps.save(fig, "p3_mae_by_model")

    # MAE by hour
    fig, ax = plt.subplots(figsize=(9, 3.8))
    for m, col in [("naive_d1", ps.NEUTRAL), ("linear", ps.ORANGE), ("lightgbm", ps.BLUE)]:
        s = pd.Series(M["by_hour_mae"][m])
        ax.plot(s.index, s.values, color=col, label=LABELS[m])
    ax.set_xticks([0, 6, 12, 18, 23])
    ax.set_xlabel("Hour (Irish local time)")
    ax.set_ylabel("MAE (EUR/MWh)")
    worst = max(M["by_hour_mae"]["lightgbm"], key=M["by_hour_mae"]["lightgbm"].get)
    ps.titles(ax, f"Errors peak in the morning ramp (worst hour {worst:02d}:00)", "Test year, mean absolute error by delivery hour")
    ax.legend(loc="upper left")
    fig.tight_layout()
    ps.save(fig, "p3_mae_by_hour")

    # MAE by wind band
    fig, ax = plt.subplots(figsize=(8, 3.6))
    bands = list(M["by_wind_band_mae"]["lightgbm"].keys())
    xs = np.arange(len(bands))
    for k, (m, col) in enumerate([("naive_d1", ps.NEUTRAL), ("lightgbm", ps.BLUE),
                                  ("lightgbm_perfect_wind", ps.BLUE_RAMP[2])]):
        ax.bar(xs + (k - 1) * 0.27, [M["by_wind_band_mae"][m][b] for b in bands], width=0.27,
               color=col, label=LABELS[m], edgecolor=ps.SURFACE, linewidth=2)
    ax.set_xticks(xs, [f"{b}\n(n={M['by_wind_band_n'][b]:,})" for b in bands])
    ax.set_xlabel("Actual ROI wind penetration")
    ax.set_ylabel("MAE (EUR/MWh)")
    ps.titles(ax, "The model's edge over naive grows with wind penetration",
              "Test year, mean absolute error by wind-penetration band")
    ax.set_ylim(0, max(M["by_wind_band_mae"]["naive_d1"].values()) * 1.3)
    ax.legend(loc="upper left", ncol=3)
    fig.tight_layout()
    ps.save(fig, "p3_mae_by_wind_band")

    # Example fortnight
    sl = c[(c.td >= "2026-01-12") & (c.td <= "2026-01-25")].sort_values("ts_utc")
    fig, ax = plt.subplots(figsize=(11, 3.8))
    ax.plot(sl.ts_local, sl.price, color=ps.INK, linewidth=1.6, label="Actual DAM price")
    ax.plot(sl.ts_local, sl.lightgbm, color=ps.BLUE, linewidth=1.6, label="LightGBM forecast")
    ax.plot(sl.ts_local, sl.naive_d1, color=ps.NEUTRAL, linewidth=1.2, label="Naive (yesterday)")
    ax.set_ylabel("EUR/MWh")
    ps.titles(ax, "Forecast vs actual, two test weeks in January 2026",
              "Hourly; forecasts made with information available before the D-1 11:00 gate closure")
    ax.legend(loc="upper right", ncol=3)
    fig.tight_layout()
    ps.save(fig, "p3_example_weeks")

    # Feature importance
    g = pd.Series(M["lgb_gain_share"]).head(12)[::-1]
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    ax.barh(range(len(g)), g.values * 100, color=ps.BLUE, height=0.6)
    ax.set_yticks(range(len(g)), g.index)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)
    ax.set_xlabel("Share of total split gain (%)")
    ps.titles(ax, "Price history sets the level; wind forecasts shape the hours",
              "LightGBM feature importance (gain), model trained to Aug 2026")
    fig.tight_layout()
    ps.save(fig, "p3_feature_importance")


if __name__ == "__main__":
    main()
