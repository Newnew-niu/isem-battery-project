# I-SEM Wind, Price & Battery Arbitrage Analysis

How wind drives Irish day-ahead electricity prices, how well those prices can be forecast
before gate closure, and what that is worth to a battery. Two full years of the Irish Single
Electricity Market (I-SEM), trading days 1 Oct 2024 to 30 Sep 2026, built end to end from
public data with a SQL analysis layer and a reproducible one-command pipeline.

## Key results

| | |
|---|---|
| **Merit-order effect** | Each +10 points of wind penetration lowers the DAM price by **~15 EUR/MWh** within a month. Wind alone explains 27% of hourly price variation; the monthly *level* tracks GB/gas (correlation 0.96). |
| **Price forecast** (test year, leakage-free) | LightGBM MAE **25.4 EUR/MWh**, **32% better** than the "same hour yesterday" baseline (37.6). Linear regression: 29.1. Perfect knowledge of actual wind would improve it by only 2%. |
| **Battery, 1 MW / 2 MWh, DAM only** | Forecast-driven strategy earns **EUR 45.1k/MW/yr**, **80%** of perfect foresight (EUR 56.6k). A fixed-hours rule earns EUR 39.7k, so the forecast is worth **EUR 5.5k/MW/yr**. |
| **Data integrity** | Found and fixed a **24-hour timestamp offset** in the public price series, verified against 363 official SEMOpx files (100% exact match after correction). Without the fix, the merit-order effect looked three times weaker. |

Every number above is produced by code in `src/` and stored in `reports/phase*_metrics.json`.

## Deliverables

| File | What it is |
|---|---|
| [`reports/showcase.html`](reports/showcase.html) | One-page visual case study of the whole project (open in a browser) |
| [`reports/ISEM_market_summary.xlsx`](reports/ISEM_market_summary.xlsx) | Trader-facing workbook: KPI summary (live formulas), monthly price stats, hour x month heatmap, battery revenue by strategy with native charts, forecast accuracy |
| [`reports/market_commentary.md`](reports/market_commentary.md) / [`.pdf`](reports/market_commentary.pdf) | Monthly market note for commercial and finance colleagues (September 2026) |
| [`reports/phase2_market_analysis.md`](reports/phase2_market_analysis.md) | Market analysis: price shape, merit order, low prices, spreads, event study |
| [`reports/phase3_forecasting.md`](reports/phase3_forecasting.md) | Forecasting method, no-leakage rule, results, error analysis |
| [`reports/phase4_battery_backtest.md`](reports/phase4_battery_backtest.md) | Battery backtest, sensitivities, limitations |
| [`reports/data_quality_log.md`](reports/data_quality_log.md) | Auto-generated data-quality and validation log |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Decision log: every choice that could change a result, with evidence, costs and checks |

## Reproduce

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows; .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python src/run_pipeline.py        # downloads (cached) -> DuckDB -> all phases -> all reports (~3 min)
```

All sources download without an account. The pipeline is deterministic: two consecutive runs
give identical metrics. One caveat: SEMOpx publishes only a rolling ~12 months of files, so a
re-download later will cover a different window. That changes validation counts, not the
study-period price series.

## Data

| Series | Source | Resolution | Notes |
|---|---|---|---|
| DAM price | [Ember](https://ember-energy.org/data/european-wholesale-electricity-price-data) hourly file (from SEMOpx) | Hourly | Corrected by −24 h; flat-price days replaced or excluded (D9) |
| DAM price, official | [SEMOpx report API](https://reports.semopx.com/api/v1/documents/static-reports) | 30-min | Validation, flat-day repair, half-hourly sensitivity |
| Wind and load, ROI and NI | [Energy-Charts API](https://api.energy-charts.info/) (Fraunhofer ISE; ENTSO-E data) | 30-min | `uk` = Northern Ireland. NI load missing 55% of the period (D8) |
| Wind and temperature forecasts | [Open-Meteo Previous Runs](https://open-meteo.com/en/docs/previous-runs-api), ECMWF IFS 0.25° (ICON for robustness) | Hourly | 48 h-ahead vintage at 6 sites, so leakage-free (D2-D4) |
| GB day-ahead price | Ember hourly file | Hourly | Context and a D-1 lag feature only |
| *Validation only:* all-island wind | [EirGrid Smart Grid Dashboard](https://www.smartgriddashboard.com/) exports | 15-min | Matches Energy-Charts at corr 0.998, MAE 45 MW |

**Attribution.** Ember (2026), *European Wholesale Electricity Price Data*, CC-BY-4.0, original
source SEMOpx. SEMOpx market results. Fraunhofer ISE Energy-Charts, original source ENTSO-E
Transparency Platform (EirGrid and SONI as data providers). Weather data by
[Open-Meteo.com](https://open-meteo.com/), CC-BY-4.0. EirGrid Smart Grid Dashboard. The
Energy-Charts API does not state a licence for this endpoint, so raw data are not redistributed;
`data/` is git-ignored and rebuilt by the pipeline.

## Methodology

**Pipeline (`src/build_db.py`).** All series are keyed on UTC, with Irish local time and the
DAM trading date (23:00 to 23:00 local) added. Nothing is imputed: gaps stay NULL and are
logged. Automated checks cover duplicates, DST days, outliers, negative prices, frozen
feeds, cross-source agreement and price timing against physical data. The data live in DuckDB
(`src/sql/schema.sql`), and the analysis queries are SQL files in `src/sql/`.

**Market analysis (`src/phase2_analysis.py`).** Hour x day-type x season profiles; merit-order
regressions with month and hour fixed effects; net-load curve; low-price conditions; spread
history from 2018; event study of the most volatile week.

**Forecasting (`src/phase3_forecast.py`).** Features are limited to information available at
the 11:00 D-1 gate closure: D-1 and earlier prices, the D-1 GB price, 48 h-ahead weather and
the calendar. Year 1 is for training; all choices (hyperparameters, target form) were made on
a Year-1 validation window (Jul to Sep 2025). Models are refitted monthly on an expanding
window over the untouched test year. Benchmarks: naive same hour yesterday and last week. An
explicitly labelled upper bound adds actual wind.

**Battery (`src/battery.py`, `src/phase4_backtest.py`).** For each trading day with hourly
periods $t = 1..T$ ($T$ = 23, 24 or 25), price $p_t$, charge $c_t$, discharge $d_t$ (MW), state of
charge $s_t$ (MWh) and a binary mode $u_t$:

$$
\max_{c,d,s,u} \; \sum_{t=1}^{T} p_t \,(d_t - c_t)\,\Delta t
$$

$$
\begin{aligned}
s_t &= s_{t-1} + \eta_c\, c_t\, \Delta t - \frac{d_t\, \Delta t}{\eta_d}, \qquad s_0 = s_T = \underline{S} && \text{(SoC continuity, energy-neutral day)}\\
\underline{S} &\le s_t \le \overline{S}, \qquad \underline{S} = 0.05E,\; \overline{S} = 0.95E && \text{(SoC limits)}\\
0 \le c_t &\le P\,u_t, \qquad 0 \le d_t \le P\,(1-u_t), \qquad u_t \in \{0,1\} && \text{(power; no simultaneous charge and discharge)}\\
\sum_{t} \frac{d_t\,\Delta t}{\eta_d} &\le \overline{S} - \underline{S} && \text{(at most one full cycle per day)}
\end{aligned}
$$

with $P$ = 1 MW, $E$ = 2 MWh, $\Delta t$ = 1 h and $\eta_c = \eta_d = \sqrt{0.88}$. The binary is needed
because negative prices would otherwise make "charging and discharging at once" (burning energy
in losses) look profitable. Each day is solved with HiGHS (`scipy.optimize.milp`) in about 3 ms.
Three strategies are compared: perfect foresight; optimising on the forecast and settling at
actual prices; and a fixed seasonal 2-hour rule derived from Year 1.

## Data challenges and how they were handled

1. **The price series was a day out.** A month with a zero daily spread in the spread chart
   led to the official SEMOpx files. Ember's Ireland prices turned out to be labelled
   **24 hours late**, which was confirmed three independent ways: an exact match with SEMOpx,
   alignment with same-hour net load, and the weekend pattern. Ember also has 54 days with
   one price for all 24 hours. Both issues were fixed; without the fix the merit-order effect
   was understated about threefold (D9).
2. **The obvious sources were unusable.** EirGrid's file pages returned 403 to scripts. The
   dashboard's data service was heavily rate-limited, and NI demand returned nothing. These
   blocks were not worked around. ENTSO-E data via Energy-Charts were used instead, after
   validating them against EirGrid's own exports (D5).
3. **An earlier inference was wrong, and the logs say so.** The dashboard's timestamps were
   first judged to be UTC from a single DST edge case. Direct comparison with an independent
   series showed they were local time (D5).
4. **A forecast weakness was kept rather than tuned away.** The model under-forecasts by about
   8 EUR/MWh in the rising-price test year, because trees cannot extrapolate. A de-levelled
   target scored worse on the Year-1 validation window, so it was not adopted. Choosing it
   after seeing the test results would have been leakage (Phase 3 report).
5. **Reproducibility.** Results differed between runs until both causes were found: LightGBM
   threading, and unordered SQL results feeding row-sampled training.

## Limitations

- **DAM arbitrage only.** No DS3 system services, capacity market, intraday or balancing
  revenue, which together make up much of an Irish battery's income. No degradation, network
  charges or imbalance costs. Price-taker assumption.
- **No gas or carbon price data.** The price level comes from lagged power prices, which is the
  cause of the forecast bias.
- **The wind forecast is a proxy.** It uses wind speed at six points, about a day staler than a
  desk's forecast, so forecast accuracy is a conservative lower bound.
- **Wind penetration uses ROI only** (NI load unavailable). The hourly price is Ember's
  first-half-hour value. A half-hourly re-optimisation changes battery revenue by only +0.5%.
- **Year-1 price correction.** It rests on physical and calendar checks, not on an exact match
  with official files (SEMOpx keeps only ~12 months).

## Next steps

- Official wind forecasts and actual availability from the ENTSO-E Transparency Platform, plus
  load forecasts.
- Gas (NBP/TTF) and carbon prices as level features; probabilistic (quantile) forecasts for
  risk-aware battery bidding.
- Balancing Market imbalance prices and intraday (IDA1-3) for sequential battery
  optimisation, and DS3 system-services stacking.
- Future market design: the move to 15-minute products and planned changes to SEM-GB trading
  arrangements.
- Degradation-aware cycling (a cost per MWh throughput) and multi-day state-of-charge carry-over.

## Repository layout

```
src/                 pipeline, analysis, models, backtest, report builders (run_pipeline.py = entry point)
src/sql/             DuckDB schema, analysis view and the Phase 2 queries
reports/             deliverables, generated reports, figures/, metrics JSON
docs/DECISIONS.md    decision log (evidence, costs, checks)
notebooks/           scratch space for exploration (all reported results come from src/)
LEARNING_NOTES.md    study notes and interview preparation (Chinese)
data/                raw downloads and the DuckDB database (git-ignored, rebuilt by the pipeline)
```
