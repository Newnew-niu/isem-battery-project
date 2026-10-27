"""Write reports/phase4_battery_backtest.md from reports/phase4_metrics.json."""
import json

import pandas as pd

from config import REPORTS

M = json.loads((REPORTS / "phase4_metrics.json").read_text())
S = M["base_case"]["summary"]
b = M["base_case"]["battery"]
sens = pd.DataFrame(M["sensitivity"])
hh = M["half_hourly"]
rs = M["rule_schedule"]
monthly = pd.DataFrame(M["monthly_revenue_eur"]).T
NAMES = {"perfect": "1. Perfect foresight (upper bound)", "forecast_lightgbm": "2. Forecast-based, LightGBM",
         "forecast_linear": "2b. Forecast-based, linear", "forecast_naive": "2c. Forecast-based, naive (yesterday)",
         "rule": "3. Simple rule (fixed hours)"}


def k(x):
    return f"EUR {x / 1000:,.1f}k"


rows = "\n".join(f"| {NAMES[s]} | {k(S[s]['annual_eur_per_mw'])} | {S[s]['capture_pct']:.0f}% | {S[s]['loss_days']} |"
                 for s in ["perfect", "forecast_lightgbm", "forecast_linear", "forecast_naive", "rule"])
pv = sens.pivot_table(index="duration_h", columns=["strategy", "rte"], values="annual_eur_per_mw")
cap = sens.pivot_table(index="duration_h", columns=["strategy", "rte"], values="capture_pct")
sens_rows = "\n".join(
    f"| {h} h | " + " | ".join(f"{k(pv[('forecast_lightgbm', r)][h])} ({cap[('forecast_lightgbm', r)][h]:.0f}%)"
                              for r in (0.80, 0.88, 0.92)) + " |" for h in (1, 2, 4))
per_mwh = {h: pv[("forecast_lightgbm", 0.88)][h] / h for h in (1, 2, 4)}
best_m = monthly.perfect.idxmax()
worst_m = monthly.perfect.idxmin()

txt = f"""# Phase 4 - Battery arbitrage backtest (DAM only)

**Asset:** {b['power_mw']:.0f} MW / {b['energy_mwh']:.0f} MWh, {b['rte']:.0%} round-trip efficiency,
state of charge limited to {b['soc_min_frac']:.0%}-{b['soc_max_frac']:.0%}, at most one full cycle per
trading day, trading the day-ahead market only. **Test year:** trading days Oct 2025 to Sep 2026,
of which {M['days_in_backtest']} are scored. Six days are excluded because an input lag is missing:
1, 2 and 7 Oct 2025 follow a NULL Ember flat day, and 30-31 Mar and 5 Apr 2026 follow the
23-hour DST day. The same day set is used in every run.
Annual figures = mean daily revenue x 365.
The optimisation is formulated in the README; code is in `src/battery.py` and
`src/phase4_backtest.py`.

## Strategies

1. **Perfect foresight.** Optimise on actual prices. This is an upper bound, not an
   achievable result.
2. **Forecast-based.** At gate closure, optimise the schedule on the Phase 3 forecast, then
   settle every MWh at the actual DAM price. The linear and naive forecasts are shown for
   comparison.
3. **Simple rule.** No forecast at all: charge in the 2 cheapest and discharge in the 2 most
   expensive hours of the Year-1 average daily profile, set per season (winter: charge
   {rs['winter']['charge']}, discharge {rs['winter']['discharge']}; summer: charge
   {rs['summer']['charge']}, discharge {rs['summer']['discharge']}). The hours were fixed from
   Year 1, so the rule uses no test-year information.

## Results

![](figures/p4_annual_revenue.png)

| Strategy | Revenue per MW per year | Share of perfect foresight | Days with negative revenue |
|---|---|---|---|
{rows}

- **The forecast-based strategy earns {k(S['forecast_lightgbm']['annual_eur_per_mw'])}/MW/year,
  {S['forecast_lightgbm']['capture_pct']:.0f}% of the perfect-foresight value.** On a typical day it captures
  {M['forecast_daily_capture_median_pct']:.0f}% (median).
- **Value of forecasting.** Strategy 2 minus strategy 3 is
  **{k(M['value_of_forecasting_eur_per_mw'])}/MW/year**, about
  {100 * M['value_of_forecasting_eur_per_mw'] / S['rule']['annual_eur_per_mw']:.0f}% more revenue than the rule.
  Against a naive "yesterday's prices" forecast, the ML forecast is worth
  {k(M['value_of_ml_over_naive_eur_per_mw'])}/MW/year.
- **Counter-intuitive result: a bad forecast is worse than no forecast.** The naive strategy,
  which optimises on yesterday's prices, earns *less* than the fixed rule
  ({S['forecast_naive']['capture_pct']:.0f}% vs {S['rule']['capture_pct']:.0f}%). It also loses money on
  {S['forecast_naive']['loss_days']} days. Yesterday's peak is often in the wrong hour today, while a
  fixed seasonal schedule at least averages over many days.
- **Revenue is concentrated.** Under perfect foresight, the best 10% of days earn
  {M['perfect_top10pct_days_share']:.0%} of the annual total. Volatile, low-wind days are where a
  battery earns its money.
- **Year 1 comparison.** Perfect-foresight revenue in Year 1 was
  {k(M['y1_perfect_annual_eur_per_mw'])}/MW, so the opportunity was similar in both years.

### Monthly

![](figures/p4_monthly_revenue.png)

Monthly revenue tracks the average daily spread (correlation
{M['monthly_corr_revenue_spread']:.2f}) much more than the price level
({M['monthly_corr_revenue_level']:.2f}). The strongest month under perfect foresight is {best_m}
({k(monthly.perfect[best_m])}), and the weakest is {worst_m} ({k(monthly.perfect[worst_m])}).
In September 2026 the fixed rule earned only {k(M['sep26_rule_vs_forecast']['rule'])}, against
{k(M['sep26_rule_vs_forecast']['forecast'])} for the forecast. When the daily price shape shifts
away from last year's pattern, a fixed schedule breaks down. That is exactly when a forecast
pays for itself.

![](figures/p4_example_day.png)

## Sensitivity

![](figures/p4_sensitivity.png)

Forecast-based strategy, revenue per MW per year (share of perfect foresight in brackets):

| Duration | 80% RTE | 88% RTE | 92% RTE |
|---|---|---|---|
{sens_rows}

- **Revenue rises almost linearly with duration** at 1 cycle per day. Per MWh of storage it
  slips only slightly: {k(per_mwh[1])}, {k(per_mwh[2])} and {k(per_mwh[4])} per MWh for 1, 2
  and 4 h at 88%. So whether a longer battery is worth building depends mainly on the capex
  per MWh, not on the market.
- **Efficiency is second-order but not negligible.** Moving from 80% to 92% adds about
  {100 * (pv[('forecast_lightgbm', 0.92)][2] / pv[('forecast_lightgbm', 0.80)][2] - 1):.0f}% for a 2 h battery.
- **The forecast's capture rate improves with duration** ({cap[('forecast_lightgbm', 0.88)][1]:.0f}% at 1 h,
  {cap[('forecast_lightgbm', 0.88)][4]:.0f}% at 4 h). A longer battery needs the broad shape of the day
  right, not the exact peak hour, and that is easier to forecast.
- **The fixed rule is a 2-hour rule by definition.** For a 4 h battery it uses only part of the
  capacity ({cap[('rule', 0.88)][4]:.0f}% capture), so do not read it as a 4 h benchmark.
- **Half-hourly settlement.** The DAM actually clears in 30-minute periods. Re-optimising on
  the official half-hourly SEMOpx prices ({hh['days']} test days with full data) raises
  perfect-foresight revenue by only **{hh['uplift_pct']:.1f}%**, so the hourly simplification costs
  very little for a 2 h battery.

## Limitations (read before quoting any number)

- **DAM only.** No intraday (IDA/IDC), balancing market, capacity market or DS3 system-services
  revenue. In practice, system services and capacity payments are a large part of an Irish
  battery's income, so **these numbers are not total asset revenue**.
- **No degradation cost.** Every cycle is treated as free, which overstates the value of
  marginal low-spread cycles. A cycling cost of a few EUR/MWh would remove some trades.
- **Price-taker.** The battery's own bids are assumed not to move prices. For 1 MW that is
  fine; for a large fleet it is not.
- **No network charges, imbalance exposure, availability losses or forecast-error hedging.**
  The schedule is assumed to be delivered exactly as cleared.
- **The hourly price is Ember's first-half-hour value** (DECISIONS.md D9). The half-hourly
  sensitivity shows this effect is small.
- **Daily optimisation with an empty battery at each trading-day boundary.** No energy is
  carried over 23:00, so a small amount of value at that boundary is lost.
"""
(REPORTS / "phase4_battery_backtest.md").write_text(txt, encoding="utf-8")
print("written")
