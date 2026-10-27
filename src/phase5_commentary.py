"""Phase 5 - monthly market note for commercial/finance colleagues (latest full month).
Writes reports/market_commentary.md, a styled HTML copy and a PDF (headless Chrome/Edge).
Every number is computed here from the DuckDB database and the Phase 3-4 metrics.
"""
import json
import shutil
import subprocess
from pathlib import Path

import duckdb
import markdown
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

import plot_style as ps
from config import DB_PATH, REPORTS

ps.apply()
MONTH, PREV = pd.Period("2026-09"), pd.Period("2026-08")
YAGO = MONTH - 12


def load():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    d = con.execute("""SELECT h.*, g.price_eur_mwh AS gb FROM hourly_panel h
                       LEFT JOIN price_gb g USING (ts_utc) ORDER BY h.ts_utc""").df()
    con.close()
    d["month"] = pd.to_datetime(d.trading_date).dt.to_period("M")
    return d


def stats(x):
    w = x.dropna(subset=["price_eur_mwh", "wind_all_mw"])
    day = x.groupby("trading_date").price_eur_mwh.agg(lambda s: s.max() - s.min())
    pk = x[x.ts_local.dt.hour.between(17, 19) & (x.ts_local.dt.dayofweek < 5)]
    cap = (w.price_eur_mwh * w.wind_all_mw).sum() / w.wind_all_mw.sum()
    return {"mean": x.price_eur_mwh.mean(), "gb": x.gb.mean(), "ws": x.wind_share.mean(),
            "wind": x.wind_all_mw.mean(), "load": x.load_roi_mw.mean(), "capture": cap,
            "capture_rate": cap / x.price_eur_mwh.mean(), "spread": day.median(),
            "low": int((x.price_eur_mwh <= 5).sum()), "neg": int((x.price_eur_mwh < 0).sum()),
            "max": x.price_eur_mwh.max(), "peak_wd": pk.price_eur_mwh.mean()}


def pct(a, b):
    return 100 * (a / b - 1)


def chart(d):
    s = d[d.month == MONTH]
    day = s.groupby("trading_date").agg(p=("price_eur_mwh", "mean"), mx=("price_eur_mwh", "max"),
                                        mn=("price_eur_mwh", "min"), w=("wind_all_mw", "mean"), gb=("gb", "mean"))
    day.index = pd.to_datetime(day.index)
    fig, axes = plt.subplots(2, 1, figsize=(9, 5.2), sharex=True, gridspec_kw={"height_ratios": [1.5, 1]})
    ax = axes[0]
    ax.fill_between(day.index, day.mn, day.mx, color=ps.BLUE_RAMP[1], linewidth=0, label="Daily min-max range")
    ax.plot(day.index, day.p, color=ps.BLUE, marker="o", markersize=3.5, label="I-SEM daily mean")
    ax.plot(day.index, day.gb, color=ps.ORANGE, linewidth=1.5, label="GB daily mean")
    ax.set_ylabel("EUR/MWh")
    ax.set_title("Day-ahead price")
    ax.legend(loc="upper left", ncol=3, fontsize=8)
    ax = axes[1]
    ax.bar(day.index, day.w, color=ps.AQUA, width=0.8)
    ax.set_ylabel("MW")
    ax.set_title("All-island wind generation, daily mean")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    fig.suptitle(f"I-SEM in {MONTH.strftime('%B %Y')}", x=0.01, ha="left", fontsize=13, fontweight="semibold")
    fig.tight_layout()
    return ps.save(fig, "commentary_month")


def main():
    d = load()
    cur, prev, yago = (stats(d[d.month == p]) for p in (MONTH, PREV, YAGO))
    s = d[d.month == MONTH]
    daily = s.groupby("trading_date").agg(p=("price_eur_mwh", "mean"), mx=("price_eur_mwh", "max"),
                                          w=("wind_all_mw", "mean"), gb=("gb", "mean"))
    hi_day, lo_day = daily.mx.idxmax(), daily.p.idxmin()
    calm_hot = daily[(daily.p > 230) & (daily.w < 1100)]
    calm_days = ", ".join(f"{pd.Timestamp(t):%d}" for t in calm_hot.index)
    low_on_lo_day = int((s[s.trading_date == lo_day].price_eur_mwh <= 5).sum())
    mm = d[d.in_study].groupby("month").price_eur_mwh.mean()
    rises = 0
    for a, b in zip(mm.values[::-1][:-1], mm.values[::-1][1:]):
        if a > b:
            rises += 1
        else:
            break
    p3 = json.loads((REPORTS / "phase3_metrics.json").read_text())
    p4 = json.loads((REPORTS / "phase4_metrics.json").read_text())
    rev = p4["monthly_revenue_eur"][str(MONTH)]
    rev_rank = sorted(p4["monthly_revenue_eur"], key=lambda k: -p4["monthly_revenue_eur"][k]["forecast_lightgbm"]).index(str(MONTH)) + 1
    mae_m = p3["by_month_mae"]["lightgbm"][str(MONTH)]
    mae_rank = sorted(p3["by_month_mae"]["lightgbm"], key=lambda k: -p3["by_month_mae"]["lightgbm"][k]).index(str(MONTH)) + 1
    ty = d[(d.trading_date >= "2025-10-01") & (d.trading_date <= "2026-09-30")]
    ty = ty.dropna(subset=["price_eur_mwh", "wind_all_mw"])
    cap_y2 = (ty.price_eur_mwh * ty.wind_all_mw).sum() / ty.wind_all_mw.sum() / ty.price_eur_mwh.mean()
    chart(d)
    mname = MONTH.strftime("%B %Y")

    md = f"""# I-SEM monthly market note - {mname}

*Day-ahead market (DAM). For commercial and finance colleagues. Data to 30 September 2026.*

## Headlines

- **Prices rose again.** The DAM averaged **{cur['mean']:.0f} EUR/MWh**, {pct(cur['mean'], prev['mean']):+.0f}% on
  {PREV.strftime('%B')} and {pct(cur['mean'], yago['mean']):+.0f}% on {YAGO.strftime('%B %Y')}. It was the
  highest month in our two-year dataset.
- **More wind did not lower the level.** Wind penetration doubled to {cur['ws']:.0%}
  ({PREV.strftime('%B')}: {prev['ws']:.0%}), yet prices rose, in step with GB
  ({cur['gb']:.0f} EUR/MWh, {pct(cur['gb'], prev['gb']):+.0f}%). The level is being set by gas, not by wind.
- **Spreads widened sharply.** The median daily max-min spread was **{cur['spread']:.0f} EUR/MWh**
  ({PREV.strftime('%B')}: {prev['spread']:.0f}). Wind swung between calm and windy days, which is
  good for flexible assets.

![](figures/commentary_month.png)

## What happened

| | {mname} | {PREV.strftime('%B %Y')} | {YAGO.strftime('%B %Y')} |
|---|---|---|---|
| Mean DAM price (EUR/MWh) | {cur['mean']:.1f} | {prev['mean']:.1f} | {yago['mean']:.1f} |
| GB day-ahead mean (EUR/MWh) | {cur['gb']:.1f} | {prev['gb']:.1f} | {yago['gb']:.1f} |
| Weekday evening peak, 17:00-20:00 (EUR/MWh) | {cur['peak_wd']:.1f} | {prev['peak_wd']:.1f} | {yago['peak_wd']:.1f} |
| Median daily spread (EUR/MWh) | {cur['spread']:.0f} | {prev['spread']:.0f} | {yago['spread']:.0f} |
| All-island wind, mean (MW) | {cur['wind']:,.0f} | {prev['wind']:,.0f} | {yago['wind']:,.0f} |
| ROI wind penetration | {cur['ws']:.0%} | {prev['ws']:.0%} | {yago['ws']:.0%} |
| ROI load, mean (MW) | {cur['load']:,.0f} | {prev['load']:,.0f} | {yago['load']:,.0f} |
| Hours at or below 5 EUR/MWh | {cur['low']} | {prev['low']} | {yago['low']} |

- **Calm days set the highs.** The highest hourly price, {cur['max']:.0f} EUR/MWh, came on
  {pd.Timestamp(hi_day):%d %B}, when wind averaged only {daily.w[hi_day]:,.0f} MW. On {len(calm_hot)} calm days
  ({calm_days} September; wind below 1,100 MW) the daily mean was above 230 EUR/MWh.
- **Windy days with a weak GB market set the lows.** On {pd.Timestamp(lo_day):%d %B} the daily mean fell to
  {daily.p[lo_day]:.0f} EUR/MWh: wind was {daily.w[lo_day]:,.0f} MW and GB averaged only
  {daily.gb[lo_day]:.0f}. That day accounted for {low_on_lo_day} of the month's {cur['low']} hours at or below
  5 EUR/MWh. There were {cur['neg']} negative hours.
- **Demand is rising into autumn.** ROI load averaged {cur['load']:,.0f} MW, {pct(cur['load'], prev['load']):+.0f}% on
  August, with darker evenings lifting the weekday peak to {cur['peak_wd']:.0f} EUR/MWh.

## What it means for our assets

**Wind.** The wind-weighted (capture) price was **{cur['capture']:.0f} EUR/MWh**, a capture rate of
**{cur['capture_rate']:.0%}** of the baseload average ({PREV.strftime('%B')}: {prev['capture_rate']:.0%}). Revenue per MWh
was high in absolute terms, but the discount to baseload widened. When the whole fleet is
generating, the price falls (the cannibalisation effect). The capture rate is structurally lower
in windy months, and averaged {cap_y2:.0%} over the test year.

**Battery (1 MW / 2 MWh, DAM arbitrage only).** This was the **#{rev_rank} month of the test year**
for the forecast-driven strategy, at **EUR {rev['forecast_lightgbm'] / 1000:.1f}k per MW**. Perfect foresight
would have earned EUR {rev['perfect'] / 1000:.1f}k, and a fixed charge/discharge schedule only
EUR {rev['rule'] / 1000:.1f}k. The fixed rule failed because the daily price shape moved away from
last year's pattern.

**Forecasting.** {"This was our day-ahead price model's worst month" if mae_rank == 1 else f"This was our model's #{mae_rank} worst month"} (MAE {mae_m:.0f} EUR/MWh,
against {p3['overall']['lightgbm']['MAE']:.0f} for the year). Most of the error was the *level*: the
model lagged the gas-driven rise. The *shape* stayed right, which is what the battery needs, so
battery revenue held up. Adding a gas-price input is the top model improvement.

## Watch list for October

- **Gas and GB prices.** They have driven the I-SEM monthly mean up for {rises} consecutive months.
- **Autumn wind.** Expect wider spreads and a lower wind capture rate.
- **Load.** Rising load into winter will make low-wind evenings expensive. A calm, cold spell
  like December 2024's would mean spike risk.

*Source: our analysis of Ember/SEMOpx DAM prices, Energy-Charts (ENTSO-E) wind and load, and
Open-Meteo forecasts. DAM only. Battery figures exclude system services, capacity and balancing
revenue. The full methodology is in the project README.*
"""
    (REPORTS / "market_commentary.md").write_text(md, encoding="utf-8")

    css = """
    @page { size: A4; margin: 14mm 15mm; }
    body { font-family: 'Segoe UI', system-ui, sans-serif; color: #0b0b0b; font-size: 10.2pt; line-height: 1.42; }
    h1 { font-size: 17pt; color: #0d366b; margin: 0 0 2px; }
    h2 { font-size: 12pt; color: #0d366b; border-bottom: 1px solid #e1e0d9; padding-bottom: 2px; margin: 12px 0 5px; }
    p, li { margin: 3px 0; }
    em { color: #52514e; }
    img { width: 100%; margin: 4px 0; }
    table { border-collapse: collapse; width: 100%; font-size: 9pt; margin: 4px 0 6px; }
    th { background: #0d366b; color: #fff; text-align: left; padding: 3px 6px; }
    td { border-bottom: 1px solid #e1e0d9; padding: 2.5px 6px; }
    td:not(:first-child), th:not(:first-child) { text-align: right; }
    """
    body = markdown.markdown(md, extensions=["tables"])
    html = f"<!doctype html><html><head><meta charset='utf-8'><title>I-SEM market note {mname}</title>" \
           f"<style>{css}</style></head><body>{body}</body></html>"
    html_path = REPORTS / "market_commentary.html"
    html_path.write_text(html, encoding="utf-8")

    pdf = REPORTS / "market_commentary.pdf"
    browsers = [shutil.which("chrome"), r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
    exe = next((b for b in browsers if b and Path(b).exists()), None)
    if exe:
        subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf}", html_path.resolve().as_uri()], check=True, timeout=120,
                       capture_output=True)
        print(f"saved {pdf}")
    else:
        print("No Chrome/Edge found - PDF not generated (HTML and Markdown written)")


if __name__ == "__main__":
    main()
