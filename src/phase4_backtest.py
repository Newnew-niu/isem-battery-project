"""Phase 4 - battery arbitrage backtest on the test year (trading days Oct 2025-Sep 2026).

Strategies (all trade the DAM only and settle at actual DAM prices):
  1. perfect     - optimise on actual prices (upper bound, not achievable)
  2. forecast    - optimise on the Phase 3 LightGBM forecast
     (also shown: the same optimiser on the naive and linear forecasts)
  3. rule        - fixed seasonal schedule: charge in the 2 cheapest and discharge in the 2 most
                   expensive hours of the Year-1 average daily profile (no forecast at all)
Days with a missing actual price or forecast are excluded; annual figures = mean day x 365.
"""
import json

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import plot_style as ps
from battery import Battery, optimise_day, revenue, simulate_rule
from config import DB_PATH, REPORTS

ps.apply()
TEST_START, TEST_END = "2025-10-01", "2026-09-30"
SEASON = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
          6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
FORECASTS = {"forecast_lightgbm": "lightgbm", "forecast_linear": "linear", "forecast_naive": "naive_d1"}
M = {}


def load(con):
    act = con.execute("""SELECT ts_utc, ts_local, trading_date, price_eur_mwh AS price
                         FROM hourly_panel WHERE trading_date BETWEEN DATE '2024-10-01' AND DATE '2026-09-30'
                         ORDER BY ts_utc""").df()
    fc = con.execute("SELECT ts_utc, model, forecast FROM price_forecast ORDER BY ts_utc, model").df()
    fc = fc.pivot(index="ts_utc", columns="model", values="forecast")
    d = act.set_index("ts_utc").join(fc).reset_index()
    d["trading_date"] = pd.to_datetime(d.trading_date)
    d["hour"] = d.ts_local.dt.hour
    return d


def rule_schedule(d):
    """From Year-1 average prices per season: 2 cheapest hours to charge, then the 2 most
    expensive hours that come later in the trading day (which starts at 23:00 local)."""
    y1 = d[d.trading_date < TEST_START].dropna(subset=["price"])
    order = [23] + list(range(23))  # position of local hours within a trading day
    sched = {}
    for s in ["winter", "spring", "summer", "autumn"]:
        prof = y1[y1.trading_date.dt.month.map(SEASON) == s].groupby("hour").price.mean()
        charge = list(prof.nsmallest(2).index)
        last = max(order.index(h) for h in charge)
        later = prof[[h for h in order[last + 1:]]]
        discharge = list(later.nlargest(2).index)
        sched[s] = {"charge": sorted(charge), "discharge": sorted(discharge)}
    return sched


def run(d, b: Battery, strategies, sched):
    """Daily results for each strategy -> DataFrame (trading_date, strategy, revenue, ...)."""
    out = []
    test = d[(d.trading_date >= TEST_START) & (d.trading_date <= TEST_END)]
    cols = ["price"] + list(FORECASTS.values())  # same day set for every run, so results are comparable
    for day, g in test.groupby("trading_date"):
        if g[cols].isna().any().any():
            continue
        p = g.price.values
        for s in strategies:
            if s == "perfect":
                c, dis, _ = optimise_day(p, b)
            elif s == "rule":
                sc = sched[SEASON[day.month]]
                c, dis, _ = simulate_rule(sc["charge"], sc["discharge"], g.hour.values, b)
            else:
                c, dis, _ = optimise_day(g[FORECASTS[s]].values, b)
            out.append({"trading_date": day, "strategy": s, "revenue": revenue(p, c, dis),
                        "discharged_mwh": dis.sum(), "charged_mwh": c.sum()})
    return pd.DataFrame(out)


def summarise(res, b: Battery):
    g = res.groupby("strategy")
    days = g.size()
    annual = g.revenue.mean() * 365 / b.power_mw
    s = pd.DataFrame({"days": days, "annual_eur_per_mw": annual,
                      "capture_pct": 100 * annual / annual["perfect"],
                      "cycles_per_year": g.discharged_mwh.mean() * 365 / (b.usable * b.eta),
                      "loss_days": g.revenue.apply(lambda r: int((r < 0).sum()))})
    return s


def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    d = load(con)
    sched = rule_schedule(d)
    M["rule_schedule"] = sched
    base = Battery()
    strategies = ["perfect", "forecast_lightgbm", "forecast_linear", "forecast_naive", "rule"]
    res = run(d, base, strategies, sched)
    summ = summarise(res, base)
    M["base_case"] = {"battery": base.__dict__, "summary": summ.round(2).to_dict("index")}
    M["value_of_forecasting_eur_per_mw"] = float(summ.annual_eur_per_mw["forecast_lightgbm"] - summ.annual_eur_per_mw["rule"])
    M["value_of_ml_over_naive_eur_per_mw"] = float(summ.annual_eur_per_mw["forecast_lightgbm"] - summ.annual_eur_per_mw["forecast_naive"])
    M["days_in_backtest"] = int(summ.days["perfect"])

    monthly = res.assign(month=res.trading_date.dt.to_period("M").astype(str)).pivot_table(
        index="month", columns="strategy", values="revenue", aggfunc="sum")
    M["monthly_revenue_eur"] = monthly.round(2).to_dict("index")
    t = d[(d.trading_date >= TEST_START) & (d.trading_date <= TEST_END)].dropna(subset=["price"])
    dd = t.groupby("trading_date").price.agg(lambda x: x.max() - x.min()).rename("spread").to_frame()
    dd["level"] = t.groupby("trading_date").price.mean()
    mdr = dd.groupby(dd.index.to_period("M").astype(str)).mean()
    mdr["perfect"] = monthly["perfect"]
    M["monthly_corr_revenue_spread"] = float(mdr.perfect.corr(mdr.spread))
    M["monthly_corr_revenue_level"] = float(mdr.perfect.corr(mdr.level))
    M["sep26_rule_vs_forecast"] = {"rule": float(monthly.loc["2026-09", "rule"]),
                                   "forecast": float(monthly.loc["2026-09", "forecast_lightgbm"])}
    # Revenue concentration under perfect foresight
    pf = res[res.strategy == "perfect"].revenue.sort_values(ascending=False)
    M["perfect_top10pct_days_share"] = float(pf.head(int(len(pf) * 0.1)).sum() / pf.sum())
    fc = res[res.strategy == "forecast_lightgbm"].set_index("trading_date").revenue
    pfd = res[res.strategy == "perfect"].set_index("trading_date").revenue
    M["forecast_daily_capture_median_pct"] = float((100 * fc / pfd).replace([np.inf, -np.inf], np.nan).median())
    M["forecast_loss_days"] = int((fc < 0).sum())

    # Year 1 perfect-foresight for context (no forecast exists there)
    y1 = d[(d.trading_date < TEST_START)]
    y1_rev = [revenue(g.price.values, *optimise_day(g.price.values, base)[:2])
              for _, g in y1.groupby("trading_date") if g.price.notna().all()]
    M["y1_perfect_annual_eur_per_mw"] = float(np.mean(y1_rev) * 365)

    # Sensitivity: duration x efficiency
    sens = []
    for hours in (1, 2, 4):
        for rte in (0.80, 0.88, 0.92):
            b = Battery(energy_mwh=hours * 1.0, rte=rte)
            s = summarise(run(d, b, ["perfect", "forecast_lightgbm", "rule"], sched), b)
            for strat, r in s.iterrows():
                sens.append({"duration_h": hours, "rte": rte, "strategy": strat,
                             "annual_eur_per_mw": r.annual_eur_per_mw, "capture_pct": r.capture_pct})
    sens = pd.DataFrame(sens)
    M["sensitivity"] = sens.round({"annual_eur_per_mw": 1, "capture_pct": 1}).to_dict("records")

    # Half-hourly settlement sensitivity (perfect foresight) on days with official SEMOpx data
    hh = con.execute("""SELECT h.ts_utc, h.price_eur_mwh, p.trading_date FROM price_dam_hh h
                        JOIN price_dam p ON p.ts_utc = date_trunc('hour', h.ts_utc)
                        WHERE p.trading_date BETWEEN DATE '2025-10-01' AND DATE '2026-09-30'
                        ORDER BY h.ts_utc""").df()
    hr_rev, hh_rev = [], []
    for day, g in hh.groupby("trading_date"):
        hrs = d[d.trading_date == pd.Timestamp(day)].price.values
        if len(g) != 2 * len(hrs) or np.isnan(hrs).any():
            continue
        hh_rev.append(revenue(g.price_eur_mwh.values, *optimise_day(g.price_eur_mwh.values, base, dt=0.5)[:2], dt=0.5))
        hr_rev.append(revenue(hrs, *optimise_day(hrs, base)[:2]))
    if hh_rev:
        M["half_hourly"] = {"days": len(hh_rev), "hourly_annual": float(np.mean(hr_rev) * 365),
                            "half_hourly_annual": float(np.mean(hh_rev) * 365),
                            "uplift_pct": float(100 * (np.mean(hh_rev) / np.mean(hr_rev) - 1))}
    con.close()

    figures(res, summ, monthly, sens, d, base)
    (REPORTS / "phase4_metrics.json").write_text(json.dumps(M, indent=1, default=float))
    print(summ.round(1).to_string())
    print({k: M[k] for k in ["value_of_forecasting_eur_per_mw", "value_of_ml_over_naive_eur_per_mw",
                             "perfect_top10pct_days_share", "forecast_daily_capture_median_pct",
                             "y1_perfect_annual_eur_per_mw", "days_in_backtest"]}, M.get("half_hourly"))


NAMES = {"perfect": "Perfect foresight", "forecast_lightgbm": "Forecast-based (LightGBM)",
         "forecast_linear": "Forecast-based (linear)", "forecast_naive": "Forecast-based (naive)",
         "rule": "Simple rule (fixed hours)"}
COLORS = {"perfect": ps.NEUTRAL, "forecast_lightgbm": ps.BLUE, "rule": ps.ORANGE,
          "forecast_naive": ps.AQUA, "forecast_linear": ps.VIOLET}


def figures(res, summ, monthly, sens, d, base):
    # Annual revenue and capture
    order = ["perfect", "forecast_lightgbm", "forecast_linear", "forecast_naive", "rule"]
    fig, ax = plt.subplots(figsize=(8.5, 3.6))
    v = summ.annual_eur_per_mw.reindex(order) / 1000
    ax.barh(range(len(order)), v, color=[COLORS[o] for o in order], height=0.6)
    for i, o in enumerate(order):
        ax.text(v[o] + 0.3, i, f"EUR {v[o]:.1f}k  ({summ.capture_pct[o]:.0f}%)", va="center", fontsize=9)
    ax.set_yticks(range(len(order)), [NAMES[o] for o in order])
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)
    ax.set_xlim(0, v.max() * 1.3)
    ax.set_xlabel("DAM arbitrage revenue, EUR thousand per MW per year (test year)")
    ps.titles(ax, f"The forecast captures {summ.capture_pct['forecast_lightgbm']:.0f}% of the perfect-foresight value",
              "1 MW / 2 MWh, 88% round-trip, 1 cycle/day; % = share of perfect foresight")
    fig.tight_layout()
    ps.save(fig, "p4_annual_revenue")

    # Monthly revenue
    fig, ax = plt.subplots(figsize=(11, 3.8))
    xs = np.arange(len(monthly))
    for k, s in enumerate(["perfect", "forecast_lightgbm", "rule"]):
        ax.bar(xs + (k - 1) * 0.27, monthly[s] / 1000, width=0.27, color=COLORS[s], label=NAMES[s],
               edgecolor=ps.SURFACE, linewidth=1.5)
    ax.set_xticks(xs, [pd.Period(m).strftime("%b %y") for m in monthly.index])
    ax.set_ylabel("EUR thousand per MW")
    ps.titles(ax, f"Monthly revenue tracks the daily spread (corr {M['monthly_corr_revenue_spread']:.2f}) "
                  f"more than the price level ({M['monthly_corr_revenue_level']:.2f})",
              "DAM arbitrage revenue by trading month, test year")
    ax.legend(loc="upper left", ncol=3)
    fig.tight_layout()
    ps.save(fig, "p4_monthly_revenue")

    # Sensitivity heatmap (forecast strategy)
    fs = sens[sens.strategy == "forecast_lightgbm"].pivot(index="duration_h", columns="rte", values="annual_eur_per_mw") / 1000
    fig, ax = plt.subplots(figsize=(6.5, 3.4))
    im = ax.imshow(fs.values, cmap=ps.SEQ_CMAP, aspect="auto")
    for i in range(fs.shape[0]):
        for j in range(fs.shape[1]):
            val = fs.values[i, j]
            ax.text(j, i, f"{val:.1f}k", ha="center", va="center", fontsize=10,
                    color="#ffffff" if val > fs.values.mean() else ps.INK)
    ax.set_xticks(range(fs.shape[1]), [f"{r:.0%}" for r in fs.columns])
    ax.set_yticks(range(fs.shape[0]), [f"{h} h ({h} MWh)" for h in fs.index])
    ax.set_xlabel("Round-trip efficiency")
    ax.grid(False)
    ps.titles(ax, "Revenue scales almost linearly with duration; efficiency is second-order",
              "Forecast-based strategy, EUR thousand per MW per year")
    fig.tight_layout()
    ps.save(fig, "p4_sensitivity")

    # Example day
    fc = res[res.strategy == "forecast_lightgbm"].set_index("trading_date").revenue
    day = fc.sort_values().index[len(fc) // 2]  # a typical (median) day
    g = d[d.trading_date == day]
    c, dis, soc = optimise_day(g.lightgbm.values, base)
    fig, axes = plt.subplots(2, 1, figsize=(9, 5), sharex=True, gridspec_kw={"height_ratios": [1.4, 1]})
    x = np.arange(len(g))
    axes[0].plot(x, g.price.values, color=ps.INK, label="Actual price")
    axes[0].plot(x, g.lightgbm.values, color=ps.BLUE, label="Forecast")
    axes[0].set_ylabel("EUR/MWh")
    axes[0].legend(loc="upper left")
    axes[0].set_title("Prices")
    axes[1].bar(x, dis, color=ps.AQUA, label="Discharge (MW)")
    axes[1].bar(x, -c, color=ps.ORANGE, label="Charge (MW)")
    axes[1].axhline(0, color=ps.AXIS, linewidth=1)
    axes[1].set_ylabel("MW")
    axes[1].set_title("Schedule chosen from the forecast")
    axes[1].legend(loc="lower left", ncol=2)
    axes[1].set_xticks(x[::3], [f"{h:02d}:00" for h in g.hour.values[::3]])
    ch = [f"{h:02d}:00" for h, v in zip(g.hour.values, c) if v > 0.05]
    dh = [f"{h:02d}:00" for h, v in zip(g.hour.values, dis) if v > 0.05]
    fig.suptitle(f"A typical test day ({day:%a %d %b %Y}): charge at {', '.join(ch)}, "
                 f"discharge at {', '.join(dh)}",
                 x=0.01, ha="left", fontsize=13, fontweight="semibold")
    fig.tight_layout()
    ps.save(fig, "p4_example_day")


if __name__ == "__main__":
    main()
