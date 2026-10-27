"""Write reports/phase3_forecasting.md from reports/phase3_metrics.json (every number traceable)."""
import json

from config import REPORTS

M = json.loads((REPORTS / "phase3_metrics.json").read_text())
o, imp = M["overall"], M["mae_vs_naive_d1_pct"]
sel, selp, lin = M["lgb_selection"], M["lgb_selection_perfect"], M["linear_selection"]
hb, wb, wn = M["by_hour_mae"], M["by_wind_band_mae"], M["by_wind_band_n"]
worst_h = max(hb["lightgbm"], key=hb["lightgbm"].get)
gain = M["lgb_gain_share"]
grp = {
    "price history (own lags)": sum(v for k, v in gain.items() if k.startswith("p_")),
    "GB price (D-1)": gain.get("gb_d1_mean", 0),
    "wind-speed forecasts": sum(v for k, v in gain.items() if k.startswith("ws_")),
    "temperature forecast": gain.get("temp_mean", 0),
    "calendar": sum(gain.get(k, 0) for k in ["hour", "dow", "holiday", "weekend", "doy_sin", "doy_cos"]),
}
lc = M["linear_coefs"]
mo = M["by_month_mae"]
worst_m = max(mo["lightgbm"], key=mo["lightgbm"].get)
d4 = M["d4_location_check"]


def row(m, label):
    return f"| {label} | {o[m]['MAE']:.1f} | {o[m]['RMSE']:.1f} | {imp[m]:+.0f}% | {M['mean_error'][m]:+.1f} |"


txt = f"""# Phase 3 - Day-ahead price forecasting

**Target:** the hourly DAM price for every hour of trading day D.
**Test year:** trading days 2025-10-01 to 2026-09-30. {M['test_rows_common']:,} hours are scored
in which every model has a forecast and the actual price exists.
Code: `src/phase3_forecast.py`; metrics: `reports/phase3_metrics.json`.

## The no-leakage rule

The DAM for day D closes at **11:00 Irish time on D-1**, so a forecast may only use what was
known at that moment:

| Allowed | Why it is known at 11:00 on D-1 |
|---|---|
| DAM prices of D-1 and earlier | D-1 was cleared in the D-2 auction |
| GB day-ahead prices of D-1 | Also cleared on D-2 |
| Wind and temperature forecasts issued at least 48 h before valid time | Published before gate closure for every hour of D (DECISIONS.md D2) |
| Calendar and Irish public holidays | Known in advance |

**Not allowed:** actual wind, actual load, and any price of day D. Actual wind appears only in
the separately labelled *upper bound* below.

## Set-up

- **Split by time, never shuffled.** Year 1 (Oct 2024 to Sep 2025) is used for training. Within
  Year 1, July to September 2025 is the validation window, and every modelling choice was made
  on it: LightGBM hyperparameters (12-point grid) and whether to predict the price or its
  deviation from yesterday's mean. **The test year was not used for any choice.**
- **Monthly re-fit.** Each test month, models (c) and (d) are refitted on all data before that
  month (an expanding window), as a desk would retrain.
- **Models**
  - (a) naive: same hour yesterday
  - (b) naive: same hour last week
  - (c) OLS linear regression with hour and weekday dummies, holiday, seasonality, price lags,
    GB price, and wind-speed forecast terms (linear, quadratic, capped-cubic, daily mean,
    interaction with the evening peak) plus a temperature forecast
  - (d) LightGBM on the same information, with the six location forecasts kept separate.
    Chosen settings: {sel['params']}, {sel['n_estimators']} trees, target = `{sel['target']}`.

## Results

![](figures/p3_mae_by_model.png)

| Model | MAE | RMSE | MAE vs naive (a) | Mean error (bias) |
|---|---|---|---|---|
{row('naive_d7', '(b) Naive, same hour last week')}
{row('naive_d1', '(a) Naive, same hour yesterday')}
{row('linear', '(c) Linear regression')}
{row('lightgbm', '(d) LightGBM')}
{row('lightgbm_icon', 'Robustness: LightGBM with ICON weather')}
{row('lightgbm_perfect_wind', '*Upper bound: LightGBM + actual wind (not a forecast)*')}

All figures are EUR/MWh on the test year.

- **Both models beat both baselines.** LightGBM cuts the naive error by **{imp['lightgbm']:.0f}%**
  and linear regression by {imp['linear']:.0f}%. "Last week" is a worse baseline than "yesterday".
- **The choice of weather model hardly matters.** ICON instead of ECMWF changes MAE by
  {o['lightgbm_icon']['MAE'] - o['lightgbm']['MAE']:+.1f} EUR/MWh.
- **Perfect knowledge of actual wind would help surprisingly little.** It improves on the real
  forecast by only {o['lightgbm']['MAE'] - o['lightgbm_perfect_wind']['MAE']:.1f} EUR/MWh
  ({100 * (1 - o['lightgbm_perfect_wind']['MAE'] / o['lightgbm']['MAE']):.0f}%). A 48-hour wind-speed
  forecast already carries most of the information in wind. The remaining error comes from
  other things: the price level (gas), demand, interconnector flows and plant outages.
- **An honest weakness: bias.** Both models under-forecast by about
  {-M['mean_error']['lightgbm']:.0f} EUR/MWh on average, while the naive baseline is almost
  unbiased. Prices rose through the test year, from about 110 to 180 EUR/MWh monthly. Tree
  models cannot extrapolate beyond the range they were trained on, and the worst month
  ({worst_m}, MAE {mo['lightgbm'][worst_m]:.0f}) is the one with the steepest rise. Predicting the
  deviation from yesterday's mean would in principle fix this, but it scored worse on the
  Year-1 validation window ({sel['val_mae_by_target']['delta']:.1f} vs
  {sel['val_mae_by_target']['raw']:.1f}), so **it was not adopted**. Switching after seeing test
  results would be test-set leakage. A gas-price feature is the proper fix (see next steps).
- **Validation error ({sel['val_mae']:.1f}) is well below test error ({o['lightgbm']['MAE']:.1f}).**
  The test year was harder: prices were higher and spreads wider.

### Error by hour

![](figures/p3_mae_by_hour.png)

Errors peak in the **morning ramp** (worst hour {worst_h}:00, MAE {hb['lightgbm'][worst_h]:.0f};
naive {hb['naive_d1'][worst_h]:.0f}) and stay high through the evening peak. Night hours are the
easiest to forecast (about {min(hb['lightgbm'].values()):.0f}). Ramps are where small timing
errors in demand and wind turn into large price errors.

### Error by wind penetration

![](figures/p3_mae_by_wind_band.png)

| Actual ROI wind penetration | Hours | Naive MAE | LightGBM MAE | Improvement |
|---|---|---|---|---|
""" + "\n".join(
    f"| {b} | {wn[b]:,} | {wb['naive_d1'][b]:.1f} | {wb['lightgbm'][b]:.1f} | "
    f"{100 * (1 - wb['lightgbm'][b] / wb['naive_d1'][b]):.0f}% |" for b in wb["lightgbm"]) + f"""

The model's edge grows with wind penetration. On windy days the price departs most from
yesterday's, and the wind forecast is what anticipates it. Low-wind hours stay the hardest:
those are scarcity hours, where the price depends on which thermal units are available and
on conditions in GB.

![](figures/p3_example_weeks.png)

## What drives the forecast

![](figures/p3_feature_importance.png)

LightGBM split gain by feature group:
""" + "\n".join(f"- {k}: {v:.0%}" for k, v in sorted(grp.items(), key=lambda kv: -kv[1])) + f"""

In the linear model (fitted on data up to August 2026), the wind-forecast terms are highly
significant: `ws_mean` t = {lc['ws_mean']['t']:.0f}, `ws_cubed` t = {lc['ws_cubed']['t']:.0f}, and the
evening-peak interaction t = {lc['ws_x_peak']['t']:.0f}. Wind lowers prices non-linearly and by more
in the evening peak. Holidays lower prices by about {-lc['holiday']['coef']:.0f} EUR/MWh.
**In plain terms:** recent prices tell the model *where the level is*, and the wind forecast
tells it *which hours will be cheap or dear*.

## Checks on earlier decisions

- **D4, the forecast locations.** On Year-1 data, a six-location regression of actual wind
  on capped-cubic wind speed reaches R² {d4['r2_six_locations']:.3f}, against
  {d4['r2_equal_mean_cubed']:.3f} for the simple equal-weight mean. The gain is small, so the
  equal-weight mean is kept for the linear model. LightGBM still receives each location
  separately. Kerry carries the largest weight ({d4['coef_six_locations']['kerry']:.2f}).
- **D3, the weather model.** ICON gives a near-identical result, so the conclusions do not
  depend on ECMWF.

## Limitations

- **No gas or carbon price.** The level comes only from lagged power prices, which causes the
  bias above.
- **The wind forecast is a proxy.** We use wind speed at six points, not an official wind
  *power* forecast. It is also about a day staler than what a desk has at 10:30 on D-1
  (DECISIONS.md D2), so the accuracy here is a conservative lower bound.
- **No interconnector, outage or demand forecasts.**
- **The forecast is a point estimate.** For trading, a probabilistic forecast (quantiles)
  would be more useful.
"""
(REPORTS / "phase3_forecasting.md").write_text(txt, encoding="utf-8")
print("written")
