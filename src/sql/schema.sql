-- I-SEM analysis database schema (DuckDB).
-- All timestamps are UTC (TIMESTAMP, interval start). Local Irish time is kept for
-- behavioural analysis; trading_date is the I-SEM DAM trading day (23:00-23:00 Irish local).

CREATE OR REPLACE TABLE price_dam (
    ts_utc        TIMESTAMP PRIMARY KEY,  -- start of the delivery hour
    ts_local      TIMESTAMP NOT NULL,     -- Europe/Dublin wall-clock time
    trading_date  DATE      NOT NULL,
    price_eur_mwh DOUBLE,                 -- DAM clearing price; can be negative; NULL on unrecoverable Ember flat days
    price_source  VARCHAR   NOT NULL      -- 'ember', 'semopx' (official file, replaces an Ember flat day) or missing
);

-- Official SEMOpx half-hourly DAM prices (rolling ~12 months available): validation and
-- the 30-minute battery sensitivity in Phase 4.
CREATE OR REPLACE TABLE price_dam_hh (
    ts_utc        TIMESTAMP PRIMARY KEY,  -- start of the half-hour
    price_eur_mwh DOUBLE
);

-- GB day-ahead price (Ember), context only: never used as a model feature.
CREATE OR REPLACE TABLE price_gb (
    ts_utc        TIMESTAMP PRIMARY KEY,
    price_eur_mwh DOUBLE
);

-- Actual wind and load from Energy-Charts (ENTSO-E). ROI = EirGrid area, NI = SONI area.
CREATE OR REPLACE TABLE system_hh (
    ts_utc       TIMESTAMP PRIMARY KEY,   -- start of the half-hour
    wind_roi_mw  DOUBLE,
    wind_ni_mw   DOUBLE,
    wind_all_mw  DOUBLE,                  -- ROI + NI; NULL if either is missing
    load_roi_mw  DOUBLE,
    load_ni_mw   DOUBLE                   -- sparse: not published for long periods
);

CREATE OR REPLACE TABLE system_hourly (
    ts_utc         TIMESTAMP PRIMARY KEY,
    wind_roi_mw    DOUBLE,                -- mean of available half-hours
    wind_ni_mw     DOUBLE,
    wind_all_mw    DOUBLE,
    load_roi_mw    DOUBLE,
    load_ni_mw     DOUBLE,
    n_hh_wind_roi  INTEGER,               -- half-hours present (2 = complete)
    n_hh_load_roi  INTEGER
);

CREATE OR REPLACE TABLE weather_fc (
    ts_utc          TIMESTAMP NOT NULL,   -- valid time
    model           VARCHAR   NOT NULL,   -- e.g. ecmwf_ifs025
    location        VARCHAR   NOT NULL,
    wind_speed_100m DOUBLE,               -- m/s, forecast issued ~48 h before valid time
    temperature_2m  DOUBLE,               -- deg C, same vintage
    PRIMARY KEY (ts_utc, model, location)
);
