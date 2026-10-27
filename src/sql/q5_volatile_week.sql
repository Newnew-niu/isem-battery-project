-- Q5. Most volatile 7-trading-day window in the study period
-- (standard deviation of hourly price across the window).
WITH days AS (
    SELECT DISTINCT trading_date FROM hourly_panel WHERE in_study
),
windows AS (
    SELECT d.trading_date                    AS week_start,
           d.trading_date + INTERVAL 6 DAY   AS week_end,
           STDDEV_SAMP(h.price_eur_mwh)      AS price_std,
           MAX(h.price_eur_mwh) - MIN(h.price_eur_mwh) AS price_range,
           AVG(h.price_eur_mwh)              AS mean_price,
           AVG(h.wind_share)                 AS mean_wind_share,
           COUNT(*)                          AS n_hours
    FROM days d
    JOIN hourly_panel h
      ON h.trading_date BETWEEN d.trading_date AND d.trading_date + INTERVAL 6 DAY
     AND h.in_study
    GROUP BY d.trading_date
    HAVING COUNT(*) >= 7 * 23
)
SELECT * FROM windows ORDER BY price_std DESC LIMIT 5;
