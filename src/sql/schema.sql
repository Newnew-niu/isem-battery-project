-- I-SEM analysis database schema (DuckDB).
-- All timestamps are UTC (TIMESTAMP, interval start). Local Irish time is kept for
-- behavioural analysis; trading_date is the I-SEM DAM trading day (23:00-23:00 Irish local).

CREATE OR REPLACE TABLE price_dam (
    ts_utc        TIMESTAMP PRIMARY KEY,  -- start of the delivery hour
    ts_local      TIMESTAMP NOT NULL,     -- Europe/Dublin wall-clock time
    trading_date  DATE      NOT NULL,
    price_eur_mwh DOUBLE    NOT NULL      -- SEMOpx DAM clearing price (via Ember); can be negative
);

CREATE OR REPLACE TABLE system_qh (
    ts_utc     TIMESTAMP PRIMARY KEY,     -- start of the 15-minute interval
    wind_mw    DOUBLE,                    -- all-island actual wind generation (SCADA)
    demand_mw  DOUBLE                     -- all-island system demand (SCADA)
);

CREATE OR REPLACE TABLE system_hourly (
    ts_utc       TIMESTAMP PRIMARY KEY,
    wind_mw      DOUBLE,                  -- mean of available quarter-hours
    demand_mw    DOUBLE,
    n_qh_wind    INTEGER,                 -- quarter-hours present (4 = complete)
    n_qh_demand  INTEGER
);

CREATE OR REPLACE TABLE weather_fc (
    ts_utc          TIMESTAMP NOT NULL,   -- valid time
    model           VARCHAR   NOT NULL,   -- e.g. ecmwf_ifs025
    location        VARCHAR   NOT NULL,
    wind_speed_100m DOUBLE,               -- m/s, forecast issued ~48 h before valid time
    temperature_2m  DOUBLE,               -- deg C, same vintage
    PRIMARY KEY (ts_utc, model, location)
);
