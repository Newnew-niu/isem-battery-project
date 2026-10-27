# Decision Log

Every modelling or data choice that could change a result is recorded here: the options
considered, the evidence used, what the choice costs, and how we would know it was wrong.
Numbers quoted are reproducible from the commands/scripts referenced.

---

## D1. Study period: 2024-10-01 to 2026-09-30 (two full gas years)

**Options considered**
- (A) 2024-10-01 → 2026-09-30: two full Oct–Sep years. *(chosen)*
- (B) 2024-03 → 2026-10: longest window where day-ahead weather forecasts exist (~2.6 years).
- (C) 2018-10 → 2026-10: the full I-SEM history.

**Evidence.** Ember hourly prices summarised by gas year (Oct–Sep):

| Gas year | Mean €/MWh | Std | Negative hours | Median daily spread |
|---|---|---|---|---|
| 2019/20 | 36.4 | 19.4 | 334 | 37.8 |
| 2021/22 | 235.8 | 98.2 | 33 | 152.3 |
| 2022/23 | 144.6 | 70.5 | 35 | 85.2 |
| 2023/24 | 103.1 | 40.0 | 64 | 82.4 |
| 2024/25 | 120.8 | 58.0 | 39 | 99.8 |
| 2025/26 | 135.2 | 57.3 | 30 | 107.4 |

The market has changed a lot since 2018. Prices were very low during COVID (2019/20). The
2021/22 gas crisis roughly doubled the price level. The two most recent years are similar to
each other: mean 121–135 €/MWh and std ≈ 57–58.

**Why A**
- The whole window falls inside one market regime, so a model trained on year 1 and tested
  on year 2 is learning patterns that still apply.
- Every source covers it. Day-ahead weather forecasts are only archived from about
  March 2024, so (C) cannot support Phase 3 at all.
- Full Oct–Sep years remove seasonal bias, so annual battery revenue is a real annual
  number and not something that depends on which months happened to be included.
- Gas years match how gas-indexed power is usually hedged.

**Costs / risks**
- Two years is a small sample: only two of each season, and only one test year.
- B would add about 7 months of training data, but the window would then start and end
  at different points in the year, so the training set would contain more of some
  seasons than others.
- One year of test data cannot show whether results hold across different gas-price
  environments.

**Mitigation.** The Phase 2 price-only questions (Q4 spread over time) are also shown for
the full 2018–2026 history as context, so the reader can see how the study window compares
with earlier years. The September 2024 data is loaded as a warm-up period so that lagged
price features exist from day 1.

---

## D2. Weather forecast vintage: 48-hour-ahead (`previous_day2`) for all hours

**Context.** SEMOpx day-ahead orders close at 11:00 Irish time on D-1. A feature is legal
only if it was **published** before then.

**How Open-Meteo defines the series.** `_previous_day1` is "the value predicted 24 hours
before valid time", `_previous_day2` is the value predicted 48 hours before
([docs](https://open-meteo.com/en/docs/previous-runs-api)). On top of that, a model run
reaches users a few hours after its start time (we assume up to about 8 h for ECMWF open
data).

**Timing check (UTC, worst case = last hour of the trading day)**
- The latest delivery hour of trading day D is 22:00 UTC on D (23:00 IST in summer).
- The `previous_day2` value for that hour comes from a run started at about 22:00 UTC
  on D-2. Even with an 8 h delay it is available by about 06:00 UTC on D-1. Gate closure
  in summer is 10:00 UTC on D-1, so there is at least 4 h of margin. **No leakage for any
  hour.**
- The `previous_day1` value for the same hour comes from a run started at about 22:00 UTC
  on D-1, which is about 12 h after gate closure. **That is leakage.** Day-1 forecasts are
  legal only for the first ~2 delivery hours of D.

**Options considered**
- (a) day2 for all hours. *(chosen)*
- (b) Hybrid: day1 where it is legal, day2 elsewhere.
- (c) Day1 everywhere (simplest, but leaks).

**Why (a).** (b) would use day1 for only about 2 of 24 hours, so it adds complexity for
almost no gain. (c) breaks the core rule of the project.

**Costs.** Our features are somewhat staler than a real trader's. A trader at 10:30 on D-1
would use runs from 00 or 06 UTC on D-1, which have lead times of about 17–46 h. Our
forecasts have lead times of about 48 h, which is roughly a day older. Our forecast accuracy
is therefore a **conservative lower bound** on what a desk could achieve.

---

## D3. Weather model: ECMWF IFS 0.25° (`ecmwf_ifs025`), not `best_match`

**Evidence (coverage probe, `previous_day2`, North Mayo point):**

| Model | Sep-2024 | Oct-2024 | Jun-2025 | Oct-2026 |
|---|---|---|---|---|
| best_match | ✓ | ✓ | ✓ | ✓ |
| ecmwf_ifs025 | ✓ | ✓ | ✓ | ✓ |
| ecmwf_ifs (9 km) | ✗ | ✗ | ✗ | ✓ |
| ukmo_seamless | ✗ | ✗ | ✗ | ✗ |
| icon_seamless | ✓ | ✓ | ✓ | ✓ |

**Why.** `best_match` is opaque: the documentation does not say which model it chooses or
whether that choice changes over time. If the underlying model changed in the middle of the
window, the feature's statistical behaviour would change too, and the price model would
misread it. A single, named model keeps the feature consistent. ECMWF is also the standard
reference model on European trading desks.

**Costs.** At 0.25° (~28 km) the grid is coarse. It will not resolve local terrain effects
at individual wind farms. We accept this because we only need **system-level** wind
conditions, not output at specific farms.

**Robustness option (Phase 3).** Rerun with `icon_seamless` to check that the conclusions do
not depend on the choice of model.

---

## D4. Forecast locations: six points across the main wind regions

| Point | Lat, Lon | Rationale |
|---|---|---|
| Donegal | 54.95, −8.05 | Largest concentration of NW onshore wind |
| North Mayo (Oweninny) | 54.13, −9.62 | Atlantic exposure; BnM/ESB Oweninny wind farm |
| Kerry | 52.25, −9.55 | Major SW wind cluster |
| West Cork | 51.85, −9.05 | Southern upland wind farms |
| Midlands (Offaly) | 53.25, −7.25 | BnM bog wind farms (Mount Lucas, Cloncreen); inland regime |
| Tyrone (NI) | 54.65, −7.00 | I-SEM is all-island; NI wind is ~1/4 of capacity |

**Why several points rather than one.** Weather fronts take hours to cross the island. If we
used a single point, the forecast would be early or late for the rest of the fleet.

**Costs.** The weights are equal, but installed capacity is not spread evenly across these
regions.

**How we will check.** In Phase 3 we will fit the relationship between forecast wind speed
and actual all-island wind output using the **training period only**. If a capacity-weighted
or learned combination clearly beats the simple mean, we will use it. Because this is
fitted on training data only, it does not leak test-period information.

---

## D5. Source for actual wind and demand: EirGrid Smart Grid Dashboard data service

**What happened.** The historical "System Data" files on `cms.eirgrid.ie` return
HTTP 403 (Access denied) to scripted requests. We **did not** try to get around that block.
The Smart Grid Dashboard data service (EirGrid's own public dashboard) returns 15-minute
all-island wind and demand data without an account.

**Costs / risks.** The service is undocumented and rate-limited: it returns intermittent
HTTP 503 errors. Its figures are operational SCADA readings and may differ slightly from
quality-controlled settlement data.

**Mitigation.** The downloader is polite: it waits between requests, backs off and retries
on 503, and caches every response in `data/raw/`. Gaps are logged, not filled. With these
settings the download takes several hours, about one 7-day chunk every 1–2 minutes. We chose
to accept that rather than send requests faster.

**Time convention of SGD timestamps: UTC (fixed GMT).** Evidence:
1. On the spring DST day (2025-03-30), Irish local 01:00–01:45 does not exist, yet SGD
   reports those four quarter-hours.
2. On the autumn DST day (2025-10-26), local 01:00–01:45 happens twice, yet SGD reports it
   once.

A third, independent check runs in `build_db.py`. Demand follows local clocks, so in UTC the
evening demand peak should come one hour earlier in summer than in winter. The result is
written to `reports/data_quality_log.md`.

*Risk if wrong.* Wind and demand would be one hour out of line with prices for half the year.
That would weaken the merit-order relationship in summer.

---

## D6. Time handling: UTC key, and the trading day defined as 23:00–23:00 Irish local time

All series are joined on UTC timestamps. A `local_time` column (Europe/Dublin) is kept for
behavioural analysis such as peak hours.

**Trading day.** The Ember file starts at 2018-10-01 23:00 local time and every daily block
ends at 22:00–23:00 local. This fits the I-SEM day-ahead trading day running from 23:00 to
23:00 Irish time, which is 00:00–24:00 CET. "Daily" quantities that relate to the day-ahead
auction are therefore computed per trading day: the battery's daily optimisation, the daily
price spread, and the forecast target. *Status: inferred from the data. To be confirmed
against a SEMOpx document.*

**Cost.** Commercial colleagues tend to think in calendar days, so charts label trading days
by their delivery date (D).

---

## D7. Python 3.10 instead of 3.11+

Only Python 3.10 is installed on the development machine. Every dependency installs and
imports correctly on 3.10, and the code avoids 3.11-only syntax. The project brief targets
3.11+, so moving to it is a one-line environment change if needed.
