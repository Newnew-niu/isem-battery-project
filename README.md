# I-SEM Wind, Price & Battery Arbitrage Analysis

Analysis of the Irish Single Electricity Market (I-SEM) day-ahead market: how wind drives
prices, a leakage-free day-ahead price forecast, and a battery arbitrage backtest.

> Status: Phase 1 (data pipeline) complete. Sections below are filled in as phases complete.

## Project structure

```
data/raw/         downloaded source files (not committed; re-downloadable)
data/processed/   cleaned, aligned data and the DuckDB database (not committed)
src/              pipeline, modelling and backtest code
src/sql/          analysis queries
notebooks/        exploratory notebooks
reports/          final deliverables (Excel, market note)
LEARNING_NOTES.md study notes (Chinese)
```

## Data sources

All sources are public and download without an account.

| Series | Source | Resolution | Time zone | Coverage checked | Licence |
|---|---|---|---|---|---|
| Day-ahead price (Ireland / SEM) | [Ember – European Wholesale Electricity Price Data](https://ember-energy.org/data/european-wholesale-electricity-price-data), hourly file, sourced from SEMOpx | Hourly | UTC + local (Europe/Dublin) columns | 2018-10-01 to 2026-10-07 (file dated 2026-10-08) | CC-BY-4.0 |
| Actual wind generation and load, ROI (`ie`) and Northern Ireland (`uk`) | [Energy-Charts API](https://api.energy-charts.info/) (Fraunhofer ISE), republishing ENTSO-E Transparency Platform data | 30-minute | UTC (Unix timestamps) | 2024-08 to 2026-10; NI load missing 55% of the study period | Not stated in the API docs; to be confirmed. Raw data not redistributed |
| Wind speed and temperature forecasts (48 h-ahead vintage), 6 locations | [Open-Meteo Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api), ECMWF IFS 0.25° (+ ICON for robustness) | Hourly | UTC | Complete for the study window | CC-BY-4.0 |
| *Validation only:* all-island actual wind | [EirGrid Smart Grid Dashboard](https://www.smartgriddashboard.com/) CSV exports | 15-minute | Irish local time | 26 × 30-day windows | [Open data licence](https://www.smartgriddashboard.com/all/open-data-license/) |

Why these sources, and what went wrong with the alternatives (EirGrid files 403, dashboard
rate limits and missing NI demand), is recorded in [`docs/DECISIONS.md`](docs/DECISIONS.md)
(D5, D8). Energy-Charts all-island wind agrees with EirGrid's own exports at correlation 0.998
(MAE 45 MW) over 35,948 half-hours.

**Attribution.** Price data: Ember (2026), *European Wholesale Electricity Price Data*, licensed
under CC-BY-4.0, original source SEMOpx. Wind and load: Fraunhofer ISE Energy-Charts, original
source ENTSO-E Transparency Platform (EirGrid and SONI as data providers). Weather forecast data:
[Open-Meteo.com](https://open-meteo.com/), CC-BY-4.0. Validation data: EirGrid Smart Grid Dashboard.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate      # Windows; use .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python src/run_pipeline.py  # downloads (cached) -> data/processed/isem.duckdb + reports/data_quality_log.md
```

## Data pipeline (Phase 1)

- `src/download_*.py`: fetch raw data into `data/raw/` with polite retries and caching.
- `src/build_db.py`: aligns everything on a **UTC** hourly key, adds Irish local time and the
  DAM trading date, runs the quality checks and loads DuckDB. Nothing is imputed; gaps stay NULL.
- `src/sql/schema.sql`: tables `price_dam`, `system_hh`, `system_hourly`, `weather_fc`.
  `src/sql/views.sql`: the analysis view `hourly_panel`. `src/sql/q*.sql`: Phase 2 queries.
- [`reports/data_quality_log.md`](reports/data_quality_log.md): missing intervals, duplicates,
  DST days, outliers, negative prices, frozen-feed detection and cross-source validation.

## Key decisions

Every choice that could change a result (study period, forecast vintage, weather model,
locations, time handling) is logged with evidence, costs and checks in
[`docs/DECISIONS.md`](docs/DECISIONS.md).

## Battery optimisation (Phase 4)

For each DAM trading day with hourly periods $t = 1..T$ ($T$ = 23, 24 or 25), price $p_t$
(EUR/MWh), period length $\Delta t$ = 1 h, charge $c_t$ and discharge $d_t$ (MW), state of charge
$s_t$ (MWh) and a binary mode $u_t$:

$$
\max_{c,d,s,u} \; \sum_{t=1}^{T} p_t \,(d_t - c_t)\,\Delta t
$$

subject to

$$
\begin{aligned}
s_t &= s_{t-1} + \eta_c\, c_t\, \Delta t - \frac{d_t\, \Delta t}{\eta_d}, \qquad s_0 = s_T = \underline{S} && \text{(SoC continuity, energy-neutral day)}\\
\underline{S} &\le s_t \le \overline{S}, \qquad \underline{S} = 0.05E,\; \overline{S} = 0.95E && \text{(SoC limits)}\\
0 \le c_t &\le P\,u_t, \qquad 0 \le d_t \le P\,(1-u_t), \qquad u_t \in \{0,1\} && \text{(power, no simultaneous charge/discharge)}\\
\sum_{t} \frac{d_t\,\Delta t}{\eta_d} &\le \overline{S} - \underline{S} && \text{(at most one full cycle per day)}
\end{aligned}
$$

with $P$ = 1 MW, $E$ = 2 MWh and $\eta_c = \eta_d = \sqrt{0.88}$. The binary is needed only
because negative prices otherwise make "charging and discharging at once" (burning energy in
losses) look profitable. Solved per day with HiGHS (`scipy.optimize.milp`), ~3 ms per day.
Implementation: [`src/battery.py`](src/battery.py).

## Methodology, results, limitations

To be written in later phases.
