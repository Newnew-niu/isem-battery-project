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

## D5. Source for actual wind and load: Energy-Charts (ENTSO-E data), validated against EirGrid

This decision changed twice. The path is recorded because each step taught us something about
the data.

**Attempt 1: EirGrid "System Data" files.** `cms.eirgrid.ie` returns HTTP 403 (Access denied)
to scripted requests. We did **not** try to get around that block.

**Attempt 2: EirGrid Smart Grid Dashboard (SGD).**
- *Data service.* Rate-limited. After a few 7-day chunks, almost every request returned 503,
  even with backoff of up to 5 minutes. We stopped rather than send requests faster.
- *Manual CSV exports from the dashboard's own button.* All-island wind exported correctly as
  26 back-to-back 30-day windows. We found that the "month" view covers the 30 days ending on
  `dateto` and ignores `datefrom`.
- *Demand.* Northern Ireland and all-island demand returned "No results" for every date
  range, including the most recent month. ROI demand worked once, then also failed.

**Attempt 3 (chosen): Energy-Charts public API** (Fraunhofer ISE), which republishes ENTSO-E
Transparency Platform data. It needs no account and has 30-minute data for both I-SEM control
areas:
- `country=ie`: Republic of Ireland (EirGrid area)
- `country=uk`: **Northern Ireland**. The returned load is about 600–1,500 MW, so this is not
  Great Britain. Since Brexit, NI is the only UK area that still reports to ENTSO-E.

**Validation: why we trust it.** `build_db.py` section 3 compares all-island wind
(ie + uk) with the 26 EirGrid CSV exports over 35,948 overlapping half-hours. Results:
correlation **0.998**, MAE **45 MW**, mean difference **+2.6 MW**, and the best fit is at zero
time shift. A spot check also agrees: ROI load at 2025-01-01 00:00 UTC is 3,535.6 MW on
Energy-Charts and 3,536 MW on the SGD data service.

**Correction to an earlier inference.** At first we concluded that SGD data-service
timestamps were UTC. The reasoning was that the service reports local 01:00–01:45 on the
spring DST day, when that local time does not exist. The cross-check shows this was
**wrong**:
- The JSON series lines up with Energy-Charts only after a −60 minute shift.
- The CSV export, which is in Irish local time (the autumn repeated hour proves this), lines
  up with no shift.

So both SGD products use local time, and the data service apparently fills in the
non-existent spring hour. Lesson: an inference from a single edge case is weaker than a direct
comparison against an independent series. Energy-Charts uses Unix timestamps, so it has no
ambiguity. A separate check confirms its time convention: the ROI evening load peak falls at
18:00 UTC in winter and 17:00 UTC in summer, which is the expected one-hour DST shift.

**Costs / risks**
- The data comes through a second-hand publisher. Mitigation: we validated it against EirGrid,
  as shown above.
- *Licence.* The API documentation we could reach does not state a licence. We attribute
  Fraunhofer ISE Energy-Charts and ENTSO-E as the sources, and the licence is still to be
  confirmed before any redistribution. Raw files are not committed to the repo.
- *Frozen feed.* On 2025-05-01 from 01:30 to 09:00 UTC, ROI wind and load are both flat for
  8 hours. EirGrid's own export is flat over the same hours, which points to an upstream
  SCADA outage. Rule applied: any value ≥ 5 MW repeated for 4 or more consecutive
  half-hours is set to NULL. Flat near-zero wind on calm nights is real, so values below
  5 MW are kept.

EirGrid's CSV also contains a `FORECAST WIND(MW)` column. We do **not** use it as a feature,
because the publication time (vintage) of each value is unknown and leakage cannot be ruled
out.

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

---

## D8. Geography: all-island wind output; wind penetration measured on ROI

**What the data allow** (study period, half-hours missing):

| Series | Missing |
|---|---|
| ROI wind | 0.30% |
| NI wind | 1.15% |
| ROI load | 0.33% |
| NI load | **55%** (none at all from 23 Oct 2025; large gaps in Jan–Mar 2025) |

The NI load gap comes from the original data publisher. It appears on both Energy-Charts and
EirGrid's dashboard.

**Options for the wind-penetration metric (wind ÷ demand)**
- (A) ROI wind ÷ ROI load. *(chosen)*
- (B) All-island wind ÷ all-island load. This would have valid values for only about 45% of
  hours, and almost none in the second (test) year.
- (C) All-island wind ÷ ROI load. Rejected: the numerator and denominator cover different
  areas, so the share would be biased upward and would have no physical meaning.
- (D) Estimate NI load by scaling ROI load. Rejected: that is synthetic data.

**Why A.** It is complete, consistent and easy to interpret. ROI is about 80% of the system:
over the study period, ROI wind averages 1,308 MW against 279 MW for NI, and ROI load averages
3,982 MW against roughly 800 MW for NI. ROI and NI weather move together because they sit in
the same weather systems.

**Where all-island numbers are used.** Wind output in MW (`wind_all_mw` = ROI + NI) is used for
descriptive charts and for the "perfect wind forecast" upper bound in Phase 3, because the
price reflects wind across the whole island.

**Cost.** The price is set for the whole island, but our penetration metric leaves out NI. If
NI wind is out of step with ROI wind, the metric misses it. The main effect is extra noise in
the merit-order relationship, which makes the estimated effect, if anything, slightly smaller
than the true one.
