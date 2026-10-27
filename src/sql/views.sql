-- Analysis-layer views. Restricted to the study period via the study_period table,
-- which build_db.py fills from src/config.py.

-- Mean forecast across the six locations, per model (D4: equal weights for now).
CREATE OR REPLACE VIEW weather_fc_mean AS
SELECT ts_utc,
       model,
       AVG(wind_speed_100m) AS wind_speed_100m_mean,
       AVG(temperature_2m)  AS temperature_2m_mean,
       COUNT(*)             AS n_locations
FROM weather_fc
GROUP BY ts_utc, model;

-- One row per delivery hour: price, actual system conditions, primary-model forecast.
CREATE OR REPLACE VIEW hourly_panel AS
SELECT p.ts_utc,
       p.ts_local,
       p.trading_date,
       p.price_eur_mwh,
       s.wind_mw,
       s.demand_mw,
       s.wind_mw / NULLIF(s.demand_mw, 0) AS wind_share,
       w.wind_speed_100m_mean            AS fc_wind_speed_100m,
       w.temperature_2m_mean             AS fc_temperature_2m,
       p.trading_date BETWEEN sp.start_date AND sp.end_date AS in_study
FROM price_dam p
CROSS JOIN study_period sp
LEFT JOIN system_hourly s USING (ts_utc)
LEFT JOIN weather_fc_mean w ON w.ts_utc = p.ts_utc AND w.model = 'ecmwf_ifs025';
