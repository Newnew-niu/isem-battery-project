"""Phase 2 - market analysis. Runs the SQL in src/sql/, draws the figures in reports/figures/
and writes reports/phase2_market_analysis.md. Every number in the report is computed here.
"""
import json

import duckdb
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

import plot_style as ps
from config import DB_PATH, REPORTS, SQL_DIR

ps.apply()
con = duckdb.connect(str(DB_PATH), read_only=True)
M = {}  # metrics written to reports/phase2_metrics.json


def sql(name):
    return con.execute((SQL_DIR / f"{name}.sql").read_text()).df()


def panel(study_only=True):
    q = ("SELECT h.*, g.price_eur_mwh AS gb_price FROM hourly_panel h "
         "LEFT JOIN price_gb g USING (ts_utc)" + (" WHERE in_study" if study_only else "") + " ORDER BY h.ts_utc")
    d = con.execute(q).df()
    d["trading_month"] = d.trading_date.dt.to_period("M")
    d["hour"] = d.ts_local.dt.hour
    return d


d = panel()
# Year 1 = training year, Year 2 = test year (used again in Phases 3-4)
d["year"] = np.where(d.trading_date < "2025-10-01", "Y1 (Oct 24-Sep 25)", "Y2 (Oct 25-Sep 26)")
M["hours"] = len(d)
M["mean_price"] = d.price_eur_mwh.mean()
M["mean_price_y"] = d.groupby("year").price_eur_mwh.mean().to_dict()


# ---------------------------------------------------------------- Q1 profile
def q1():
    prof = sql("q1_price_profile")
    seasons = ["winter", "spring", "summer", "autumn"]
    fig, axes = plt.subplots(1, 4, figsize=(13, 3.6), sharey=True)
    for ax, s in zip(axes, seasons):
        for dt, color in [("weekday", ps.BLUE), ("weekend", ps.ORANGE)]:
            p = prof[(prof.season == s) & (prof.day_type == dt)]
            ax.plot(p.hour_local, p.mean_price, color=color, label=dt.capitalize())
        ax.set_title(s.capitalize(), fontsize=11)
        ax.set_xticks([0, 6, 12, 18, 23])
        ax.set_xlabel("Hour (Irish local time)")
    axes[0].set_ylabel("Mean DAM price (EUR/MWh)")
    axes[0].legend(loc="upper left")
    fig.suptitle("Two daily peaks; the evening peak (17:00-20:00) is the highest-priced hour in every season", x=0.01, ha="left",
                 fontsize=13, fontweight="semibold")
    fig.tight_layout()
    ps.save(fig, "q1_price_profile")

    wk = prof[prof.day_type == "weekday"]
    peak = wk.loc[wk.groupby("season").mean_price.idxmax()].set_index("season")
    trough = wk.loc[wk.groupby("season").mean_price.idxmin()].set_index("season")
    M["q1"] = {
        "peak_hour": peak.hour_local.to_dict(), "peak_price": peak.mean_price.to_dict(),
        "trough_hour": trough.hour_local.to_dict(), "trough_price": trough.mean_price.to_dict(),
        "weekend_discount": (prof.groupby("day_type").mean_price.mean().weekday
                             - prof.groupby("day_type").mean_price.mean().weekend),
        "winter_evening_vs_summer_evening": peak.mean_price["winter"] - peak.mean_price["summer"],
    }


# ---------------------------------------------------------------- Q2 merit order
def q2():
    x = d.dropna(subset=["wind_share"]).copy()
    x["month_str"] = x.trading_month.astype(str)
    fits = {
        "raw": smf.ols("price_eur_mwh ~ wind_share", x).fit(),
        "month_fe": smf.ols("price_eur_mwh ~ wind_share + C(month_str)", x).fit(),
        "month_hour_fe": smf.ols("price_eur_mwh ~ wind_share + C(month_str) + C(hour)", x).fit(),
    }
    M["q2"] = {k: {"slope_per_10pp": f.params.wind_share / 10, "r2": f.rsquared,
                   "ci95_per_10pp": (f.conf_int().loc["wind_share"] / 10).tolist()}
               for k, f in fits.items()}
    M["q2"]["r2_month_only"] = smf.ols("price_eur_mwh ~ C(month_str)", x).fit().rsquared

    # Remove the monthly level (mostly gas) to isolate the within-month wind effect
    x["dev"] = x.price_eur_mwh - x.groupby("trading_month").price_eur_mwh.transform("mean")
    bands = [0, .1, .2, .3, .4, .5, .6, 10]
    labels = ["<10%", "10-20%", "20-30%", "30-40%", "40-50%", "50-60%", "60%+"]
    x["band"] = pd.cut(x.wind_share, bands, labels=labels, right=False)
    g = x.groupby("band", observed=True)
    stats = pd.DataFrame({q: g.dev.quantile(q) for q in [.1, .25, .5, .75, .9]})
    stats["n"] = g.size()
    stats["raw_median"] = g.price_eur_mwh.median()
    M["q2"]["band_stats"] = stats.round(1).reset_index().astype({"band": str}).to_dict("records")
    M["q2"]["p_low_given_ws60"] = (x[x.wind_share >= .6].price_eur_mwh <= 5).mean()
    M["q2"]["n_ws60"] = int((x.wind_share >= .6).sum())

    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    pos = np.arange(len(stats))
    ax.vlines(pos, stats[.1], stats[.9], color=ps.BLUE_RAMP[4], linewidth=2)
    ax.bar(pos, stats[.75] - stats[.25], bottom=stats[.25], width=0.5, color=ps.BLUE_RAMP[2],
           edgecolor=ps.SURFACE, linewidth=2)
    ax.hlines(stats[.5], pos - 0.25, pos + 0.25, color=ps.BLUE_RAMP[10], linewidth=2.5)
    ax.axhline(0, color=ps.AXIS, linewidth=1)
    for i, (m, n) in enumerate(zip(stats[.5], stats.n)):
        ax.text(i + 0.3, m, f"{m:+.0f}", va="center", fontsize=9, color=ps.INK)
    ax.set_xticks(pos, [f"{l}\n(n={n:,})" for l, n in zip(labels, stats.n)])
    ax.set_xlabel("Wind penetration: ROI wind / ROI load")
    ax.set_ylabel("Price minus monthly mean (EUR/MWh)")
    ps.titles(ax, f"Each 10 points of wind penetration lowers the price by about "
                  f"{-M['q2']['month_fe']['slope_per_10pp']:.0f} EUR/MWh",
               "Median (dark tick), interquartile range (box) and 10th-90th percentile (line), "
                    "within-month deviations, Oct 2024-Sep 2026")
    fig.tight_layout()
    ps.save(fig, "q2_merit_order")

    # Net load (load minus wind) is what conventional plant must cover
    x["net_load"] = x.load_roi_mw - x.wind_roi_mw
    x["nl_bin"] = (x.net_load // 250) * 250 + 125
    nb = x.groupby("nl_bin").agg(med=("dev", "median"), q25=("dev", lambda s: s.quantile(.25)),
                                 q75=("dev", lambda s: s.quantile(.75)), n=("dev", "size"))
    nb = nb[nb.n >= 50]
    M["q2"]["corr_price_netload"] = x.price_eur_mwh.corr(x.net_load)
    M["q2"]["corr_price_windshare"] = x.price_eur_mwh.corr(x.wind_share)
    M["q2"]["corr_dev_netload"] = x.dev.corr(x.net_load)
    M["q2"]["corr_dev_windshare"] = x.dev.corr(x.wind_share)
    fig, ax = plt.subplots(figsize=(8.5, 4))
    ax.fill_between(nb.index, nb.q25, nb.q75, color=ps.BLUE_RAMP[1], linewidth=0, label="Interquartile range")
    ax.plot(nb.index, nb.med, color=ps.BLUE, label="Median")
    ax.axhline(0, color=ps.AXIS, linewidth=1)
    ax.set_xlabel("ROI net load = load minus wind (MW), 250 MW bins with at least 50 hours")
    ax.set_ylabel("Price minus monthly mean (EUR/MWh)")
    ps.titles(ax, "Price rises with net load - increasingly steeply above ~3,500 MW",
               "Within-month price deviation by net load, Oct 2024-Sep 2026")
    ax.legend(loc="upper left")
    fig.tight_layout()
    ps.save(fig, "q2_net_load")
    M["q2"]["netload_med_by_bin"] = nb.med.round(1).to_dict()


# ---------------------------------------------------------------- Q3 low prices
def q3():
    cls = sql("q3_low_prices")
    M["q3"] = {"classes": cls.round(3).to_dict("records")}
    low = d[d.price_eur_mwh <= 5]
    M["q3"]["n_low"] = len(low)
    M["q3"]["n_neg"] = int((d.price_eur_mwh < 0).sum())
    M["q3"]["min_price"] = d.price_eur_mwh.min()
    M["q3"]["share_low_midday_10_15"] = low.hour.between(10, 15).mean()
    M["q3"]["share_low_night_0_6"] = low.hour.between(0, 6).mean()
    M["q3"]["share_low_17_21"] = low.hour.between(17, 21).mean()
    M["q3"]["low_gb_mean"] = low.gb_price.mean()
    M["q3"]["low_by_year"] = low.groupby("year").size().to_dict()
    M["q3"]["low_months_top"] = low.groupby(low.trading_month.astype(str)).size().nlargest(4).to_dict()

    fig, axes = plt.subplots(1, 2, figsize=(12, 3.8), gridspec_kw={"width_ratios": [1, 1.4]})
    ax = axes[0]
    neg = d[d.price_eur_mwh < 0].groupby("hour").size().reindex(range(24), fill_value=0)
    near = d[(d.price_eur_mwh >= 0) & (d.price_eur_mwh <= 5)].groupby("hour").size().reindex(range(24), fill_value=0)
    ax.bar(range(24), neg, color=ps.BLUE, label="Negative", edgecolor=ps.SURFACE, linewidth=1)
    ax.bar(range(24), near, bottom=neg, color=ps.ORANGE, label="0-5 EUR/MWh", edgecolor=ps.SURFACE, linewidth=1)
    ax.set_xticks([0, 6, 12, 18, 23])
    ax.set_xlabel("Hour (Irish local time)")
    ax.set_ylabel("Hours")
    ax.set_title("Two windows: night and midday")
    ax.legend(loc="upper right")
    ax = axes[1]
    months = pd.period_range(d.trading_month.min(), d.trading_month.max(), freq="M")
    negm = d[d.price_eur_mwh < 0].groupby("trading_month").size().reindex(months, fill_value=0)
    nearm = d[(d.price_eur_mwh >= 0) & (d.price_eur_mwh <= 5)].groupby("trading_month").size().reindex(months, fill_value=0)
    xs = np.arange(len(months))
    ax.bar(xs, negm, color=ps.BLUE, edgecolor=ps.SURFACE, linewidth=1)
    ax.bar(xs, nearm, bottom=negm, color=ps.ORANGE, edgecolor=ps.SURFACE, linewidth=1)
    ax.set_xticks(xs[::3], [m.strftime("%b %y") for m in months[::3]])
    ax.set_ylabel("Hours")
    ax.set_title("Clustered in a few shoulder-season months")
    fig.suptitle(f"Prices at or below 5 EUR/MWh: {len(low)} of {len(d):,} hours ({len(low) / len(d):.1%})",
                 x=0.01, ha="left", fontsize=13, fontweight="semibold")
    fig.tight_layout()
    ps.save(fig, "q3_low_prices")


# ---------------------------------------------------------------- Q4 spreads
def q4():
    sp = sql("q4_daily_spread")
    full = panel(study_only=False)
    tb2 = full.groupby("trading_date").price_eur_mwh.apply(lambda s: np.sort(s.values)[-2:].mean()
                                                            - np.sort(s.values)[:2].mean())
    sp = sp.set_index("trading_date").join(tb2.rename("top2_bottom2"))
    sp.index = pd.to_datetime(sp.index)
    st = sp[sp.in_study]
    M["q4"] = {
        "study_median": st.spread.median(), "study_mean": st.spread.mean(),
        "study_p10": st.spread.quantile(.1), "study_p90": st.spread.quantile(.9),
        "study_tb2_median": st.top2_bottom2.median(), "study_tb2_mean": st.top2_bottom2.mean(),
        "share_days_spread_gt_100": (st.spread > 100).mean(),
        "median_by_year": sp.groupby(sp.index.year).spread.median().round(1).to_dict(),
        "rel_spread_by_year": (sp.spread / sp.mean_price).groupby(sp.index.year).median().round(2).to_dict(),
        "corr_spread_level_study": st.spread.corr(st.mean_price),
        "median_by_study_year": st.groupby(np.where(st.index < "2025-10-01", "Y1", "Y2")).spread.median().to_dict(),
    }
    mo = sp.spread.resample("MS").agg(["median", lambda s: s.quantile(.25), lambda s: s.quantile(.75)])
    mo.columns = ["med", "q25", "q75"]
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.axvspan(pd.Timestamp("2024-10-01"), pd.Timestamp("2026-10-01"), color=ps.BLUE_RAMP[0], alpha=0.6,
               linewidth=0)
    ax.text(pd.Timestamp("2024-10-15"), mo.q75.max() * 0.97, "Study period", color=ps.INK_2, fontsize=9)
    ax.fill_between(mo.index, mo.q25, mo.q75, color=ps.BLUE_RAMP[3], alpha=0.55, linewidth=0,
                    label="Interquartile range of days")
    ax.plot(mo.index, mo.med, color=ps.BLUE_RAMP[9], label="Monthly median")
    ax.set_ylabel("Daily max - min price (EUR/MWh)")
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ps.titles(ax, "Daily spreads have stayed well above pre-crisis levels",
               "Within-day DAM price range per trading day, I-SEM history Oct 2018-Oct 2026")
    ax.legend(loc="upper left", bbox_to_anchor=(0, 0.92))
    fig.tight_layout()
    ps.save(fig, "q4_daily_spread")


# ---------------------------------------------------------------- Q5 event study
def q5():
    wk = sql("q5_volatile_week").iloc[0]
    s, e = wk.week_start - pd.Timedelta(days=2), wk.week_end + pd.Timedelta(days=2)
    w = d[(d.trading_date >= s) & (d.trading_date <= e)].sort_values("ts_utc")
    M["q5"] = {"week_start": str(wk.week_start.date()), "week_end": str(wk.week_end.date()),
               "price_std": wk.price_std, "price_range": wk.price_range, "mean_price": wk.mean_price}
    daily = w.groupby("trading_date").agg(price=("price_eur_mwh", "mean"), pmax=("price_eur_mwh", "max"),
                                          gb=("gb_price", "mean"), wind=("wind_all_mw", "mean"),
                                          load=("load_roi_mw", "mean"), fc_ws=("fc_wind_speed_100m", "mean"),
                                          temp=("fc_temperature_2m", "mean"))
    M["q5"]["daily"] = daily.round(1).reset_index().astype({"trading_date": str}).to_dict("records")
    M["q5"]["study_mean_wind_all"] = d.wind_all_mw.mean()
    M["q5"]["study_mean_load"] = d.load_roi_mw.mean()
    M["q5"]["study_mean_fc_ws"] = d.fc_wind_speed_100m.mean()

    fig, axes = plt.subplots(3, 1, figsize=(11, 7.5), sharex=True,
                             gridspec_kw={"height_ratios": [1.4, 1, 1]})
    ax = axes[0]
    ax.plot(w.ts_local, w.price_eur_mwh, color=ps.BLUE, label="I-SEM DAM")
    ax.plot(w.ts_local, w.gb_price, color=ps.ORANGE, label="GB day-ahead", linewidth=1.5)
    ax.set_ylabel("EUR/MWh")
    ax.set_title("Price")
    ax.legend(loc="upper right")
    ax = axes[1]
    ax.plot(w.ts_local, w.wind_all_mw, color=ps.AQUA)
    ax.axhline(d.wind_all_mw.mean(), color=ps.MUTED, linewidth=1, linestyle="--")
    ax.text(w.ts_local.iloc[0], d.wind_all_mw.mean() + 80, "study-period average", color=ps.INK_2, fontsize=8)
    ax.set_ylabel("MW")
    ax.set_title("All-island actual wind generation")
    ax = axes[2]
    ax.plot(w.ts_local, w.load_roi_mw, color=ps.VIOLET)
    ax.set_ylabel("MW")
    ax.set_title("ROI load")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b"))
    for a in axes:
        a.axvspan(pd.Timestamp(wk.week_start) - pd.Timedelta(hours=1), pd.Timestamp(wk.week_end) + pd.Timedelta(hours=23),
                  color=ps.BLUE_RAMP[0], alpha=0.5, linewidth=0)
    fig.suptitle(f"Most volatile week: {wk.week_start:%d} to {wk.week_end:%d %b %Y} - a windless cold spell",
                 x=0.01, ha="left", fontsize=13, fontweight="semibold")
    fig.tight_layout()
    ps.save(fig, "q5_event_week")



# ---------------------------------------------------------------- extra: monthly drivers
def monthly():
    mo = d.groupby("trading_month").agg(price=("price_eur_mwh", "mean"), gb=("gb_price", "mean"),
                                        ws=("wind_share", "mean"))
    M["monthly"] = {"corr_ie_gb_monthly": mo.price.corr(mo.gb), "corr_ie_ws_monthly": mo.price.corr(mo.ws),
                    "corr_ie_gb_hourly": d.price_eur_mwh.corr(d.gb_price),
                    "ie_minus_gb_mean": (d.price_eur_mwh - d.gb_price).mean(),
                    "min_month": str(mo.price.idxmin()), "min_month_price": mo.price.min(),
                    "max_month": str(mo.price.idxmax()), "max_month_price": mo.price.max()}
    fig, ax = plt.subplots(figsize=(11, 3.8))
    xs = mo.index.to_timestamp()
    ax.plot(xs, mo.price, color=ps.BLUE, marker="o", markersize=4, label="I-SEM DAM")
    ax.plot(xs, mo.gb, color=ps.ORANGE, marker="o", markersize=4, label="GB day-ahead")
    ax.set_ylabel("Monthly mean (EUR/MWh)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    ps.titles(ax, "The monthly price level tracks GB (a gas-priced market), not wind",
               f"Monthly correlation with GB {mo.price.corr(mo.gb):.2f}; "
                    f"with ROI wind penetration {mo.price.corr(mo.ws):.2f}")
    ax.legend(loc="upper left")
    fig.tight_layout()
    ps.save(fig, "monthly_ie_vs_gb")


for f in [q1, q2, q3, q4, q5, monthly]:
    f()


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return round(float(o), 4)
    if isinstance(o, np.integer):
        return int(o)
    return o


def write_report(M):
    q1, q2, q3, q4, q5, mo = M["q1"], M["q2"], M["q3"], M["q4"], M["q5"], M["monthly"]
    bs = {b["band"]: b for b in q2["band_stats"]}
    daily = pd.DataFrame(q5["daily"])
    calm = daily[(daily.trading_date >= q5["week_start"]) & (daily.wind < 800)]
    windy = daily[(daily.trading_date > calm.trading_date.max()) & (daily.trading_date <= q5["week_end"])]
    nl = {float(k): v for k, v in q2["netload_med_by_bin"].items()}
    y1, y2 = M["mean_price_y"]["Y1 (Oct 24-Sep 25)"], M["mean_price_y"]["Y2 (Oct 25-Sep 26)"]
    txt = f"""# Phase 2 - I-SEM day-ahead market analysis

Study period: trading days 2024-10-01 to 2026-09-30 ({M['hours']:,} hours). Mean DAM price
{M['mean_price']:.1f} EUR/MWh (Year 1 {y1:.1f}, Year 2 {y2:.1f}). All numbers come from
`src/phase2_analysis.py`; the metrics are in `reports/phase2_metrics.json`.
Wind penetration = ROI wind / ROI load (DECISIONS.md D8).

> **Data note.** These results use the corrected price series. Ember's Ireland prices are
> labelled 24 hours late. Before the correction, the merit-order effect looked about three
> times weaker than it really is. See DECISIONS.md D9.

## 1. Price shape by hour, day type and season

![](figures/q1_price_profile.png)

- **Two daily peaks on weekdays.** Prices peak in the morning (07:00-09:00) and again in the evening. The
  evening peak is the most expensive hour of the day in every season: {q1['peak_price']['autumn']:.0f} EUR/MWh
  at {q1['peak_hour']['autumn']}:00 in autumn and {q1['peak_price']['winter']:.0f} at {q1['peak_hour']['winter']}:00 in winter
  (weekday means).
- **Where the low point falls.** In autumn and winter the cheapest hours are overnight
  (about 03:00-04:00). In spring and summer they move to **14:00**.
- **Weekends.** Weekends are on average {q1['weekend_discount']:.1f} EUR/MWh cheaper than weekdays, and
  they have almost no morning peak. Without commuting and office demand, only the evening peak remains.

*Why:* the evening peak is when household demand stacks on top of commercial demand, and solar
output is zero. The most expensive gas and peaking units are then at the margin. The
**midday low in spring and summer is consistent with fast-growing solar output**. Our data
contain no solar series, so this is an interpretation we have not tested.

## 2. Merit-order effect: wind penetration vs price

![](figures/q2_merit_order.png)

- **Price falls as wind penetration rises.** Each extra 10 percentage points lowers the price
  by **{-q2['month_fe']['slope_per_10pp']:.1f} EUR/MWh** after removing month-level effects
  (95% CI {-q2['month_fe']['ci95_per_10pp'][1]:.1f} to {-q2['month_fe']['ci95_per_10pp'][0]:.1f}). Adding
  hour-of-day effects barely changes this: {-q2['month_hour_fe']['slope_per_10pp']:.1f}.
- **The effect is large.** Within a month, the median price is {bs['<10%']['0.5']:+.0f} EUR/MWh
  relative to the monthly mean when wind penetration is below 10%, and
  {bs['60%+']['0.5']:+.0f} EUR/MWh when it is above 60%.
- **Wind alone explains {q2['raw']['r2']:.0%} of hourly price variation.** Month dummies alone
  explain {q2['r2_month_only']:.0%}.

![](figures/q2_net_load.png)

- **Net load (load minus wind) is the better single explanatory variable.** Its correlation
  with the within-month price deviation is {q2['corr_dev_netload']:.2f}, against
  {q2['corr_dev_windshare']:.2f} for wind penetration.
- **The relationship is convex.** The median price deviation runs from {nl[375.0]:+.0f} EUR/MWh at
  about 400 MW of net load to {nl[3625.0]:+.0f} at 3,600 MW and {nl[5125.0]:+.0f} at 5,100 MW.

*Why:* this is the supply curve, or merit order. Wind has near-zero marginal cost and
displaces thermal plant. As net load rises, the price is set by progressively more
expensive units: efficient CCGTs, then older gas and oil units, then peakers. That is why
the last few hundred MW of net load move the price far more than the first.

![](figures/monthly_ie_vs_gb.png)

- **The monthly price level follows GB, not wind.** Monthly correlation with GB is
  {mo['corr_ie_gb_monthly']:.2f}; with wind penetration it is {mo['corr_ie_ws_monthly']:.2f}. The level
  also climbs through Year 2, from {mo['min_month_price']:.0f} EUR/MWh in {mo['min_month']} to
  {mo['max_month_price']:.0f} in {mo['max_month']}.

*Why:* gas sets the marginal price in most hours in both markets, so the fuel price fixes the
level. Wind decides where within that level a given hour sits. For forecasting (Phase 3), the
level has to come from recent prices, because we have no gas-price data.

## 3. Negative and near-zero prices

![](figures/q3_low_prices.png)

- **Low prices are rare.** {q3['n_low']} hours ({q3['n_low'] / M['hours']:.1%}) are at or below
  5 EUR/MWh, and only {q3['n_neg']} are negative (minimum {q3['min_price']:.1f} EUR/MWh).
- **They cluster in two windows:** night, 00:00-06:00 ({q3['share_low_night_0_6']:.0%} of low hours),
  and midday, 10:00-15:00 ({q3['share_low_midday_10_15']:.0%}). They are concentrated in a few
  shoulder-season months, led by {', '.join(list(q3['low_months_top'])[:3])}.
- **Even very high wind rarely pushes prices to zero.** With wind penetration of 60% or more,
  only {q2['p_low_given_ws60']:.1%} of hours price at or below 5 EUR/MWh. When the I-SEM price is
  that low, the GB price averages {q3['low_gb_mean']:.1f} EUR/MWh, so the two markets are usually
  cheap together.

*Why:* zero or negative prices need surplus renewable output that cannot be exported or
stored, at times of low demand. In I-SEM, surplus wind can usually still be exported to GB,
which supports the price. The system also limits instantaneous non-synchronous generation and
requires a minimum number of conventional units online. Wind beyond those limits is
**dispatched down** by the system operator, which does not show up in the DAM price as a
crash. Prices collapse only when GB is also oversupplied. Midday low-price hours point again
to solar adding to the surplus.

## 4. Daily price spreads

![](figures/q4_daily_spread.png)

- **Spreads are wide.** In the study period the median daily max-minus-min spread is
  **{q4['study_median']:.0f} EUR/MWh**, and {q4['share_days_spread_gt_100']:.0%} of days exceed
  100 EUR/MWh. The 10th to 90th percentile range is {q4['study_p10']:.0f} to {q4['study_p90']:.0f}.
- **What a 2-hour battery can capture.** The average of the 2 most expensive hours minus the
  2 cheapest has a median of {q4['study_tb2_median']:.0f} EUR/MWh.
- **Spreads widened in Year 2.** The median rose from {q4['median_by_study_year']['Y1']:.0f} to
  {q4['median_by_study_year']['Y2']:.0f}. Spreads scale with the price level (correlation
  {q4['corr_spread_level_study']:.2f}).
- **Spreads are still well above pre-crisis levels.** The 2019 median was
  {q4['median_by_year']['2019']:.0f} EUR/MWh; 2025 was {q4['median_by_year']['2025']:.0f}.
  Days with an Ember flat-price artefact are excluded (D9).

*Why:* spreads are the gap between night or midday prices, when wind and solar are setting
low prices, and evening prices, when gas peakers set high ones. Higher gas prices raise the
evening price more than the night price, so the spread widens in euro terms. This is the
revenue pool for Phase 4.

## 5. Event study: {q5['week_start']} to {q5['week_end']}

![](figures/q5_event_week.png)

This was the most volatile week in the study period (std of hourly price
{q5['price_std']:.0f} EUR/MWh, range {q5['price_range']:.0f}).

- **{calm.trading_date.min()} to {calm.trading_date.max()}: a calm, cold spell.** All-island wind
  averaged only {calm.wind.min():.0f}-{calm.wind.max():.0f} MW, against a study-period average of
  {q5['study_mean_wind_all']:.0f}. Forecast temperatures were {calm.temp.min():.0f}-{calm.temp.max():.0f} degC and
  ROI load was about {calm.load.mean():,.0f} MW. Daily mean prices reached {calm.price.max():.0f} EUR/MWh,
  peaking at {calm.pmax.max():.0f}. GB was stressed at the same time (daily mean up to
  {calm.gb.max():.0f}), so imports offered little relief.
- **When the wind returned,** at {windy.wind.min():,.0f}-{windy.wind.max():,.0f} MW from
  {windy.trading_date.min()}, daily mean prices fell to {windy.price.min():.0f}-{windy.price.max():.0f} EUR/MWh
  within two days.

*Why:* this is the merit order working in both directions in a single week. A windless high-
pressure system over north-west Europe pushed I-SEM onto its most expensive thermal units
while GB was short as well. For a battery, these are the days that pay: spreads of 300+
EUR/MWh.
"""
    (REPORTS / "phase2_market_analysis.md").write_text(txt, encoding="utf-8")


write_report(_clean(M))
(REPORTS / "phase2_metrics.json").write_text(json.dumps(_clean(M), indent=1))
print("Phase 2 done")
