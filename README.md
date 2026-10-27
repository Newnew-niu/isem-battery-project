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

## Methodology, results, limitations

To be written in later phases.
