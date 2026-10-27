# I-SEM Wind, Price & Battery Arbitrage Analysis

Analysis of the Irish Single Electricity Market (I-SEM) day-ahead market: how wind drives
prices, a leakage-free day-ahead price forecast, and a battery arbitrage backtest.

> Status: Phase 0 (setup and data-source check). Sections below are filled in as phases complete.

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
| Actual wind generation, system demand | [EirGrid Smart Grid Dashboard](https://www.smartgriddashboard.com/) data service | 15-minute | To be confirmed in Phase 1 (appears to be Irish local time) | Spot checks: 2018-10-01 and 2026-09-30 available | [Open data licence](https://www.smartgriddashboard.com/all/open-data-license/) – to be reviewed |
| Wind speed forecast at 100 m (proxy for wind forecast) | [Open-Meteo Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api) | Hourly | UTC (requested as GMT) | Day-1 / day-2 lead forecasts available from approx. March 2024 | CC-BY-4.0 |

**Attribution.** Price data: Ember (2026), *European Wholesale Electricity Price Data*, licensed
under CC-BY-4.0, original source SEMOpx. Weather forecast data: [Open-Meteo.com](https://open-meteo.com/), CC-BY-4.0.
Wind and demand data: EirGrid / SONI.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate      # Windows; use .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
```

## Methodology, results, limitations

To be written in later phases.
