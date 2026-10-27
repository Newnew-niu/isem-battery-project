-- Q3. How often do negative / near-zero prices occur, and under what conditions?
-- Compares system conditions in low-price hours with all other hours.
SELECT
    CASE
        WHEN price_eur_mwh < 0  THEN '1. negative'
        WHEN price_eur_mwh <= 5 THEN '2. near zero (0-5)'
        ELSE '3. other'
    END                                                   AS price_class,
    COUNT(*)                                              AS n_hours,
    COUNT(*) / SUM(COUNT(*)) OVER ()                      AS share_of_hours,
    AVG(wind_share)                                       AS mean_wind_share,
    AVG(wind_mw)                                          AS mean_wind_mw,
    AVG(demand_mw)                                        AS mean_demand_mw,
    AVG(CASE WHEN hour(ts_local) BETWEEN 0 AND 6 THEN 1 ELSE 0 END) AS share_night_0_6,
    AVG(CASE WHEN isodow(ts_local) >= 6 THEN 1 ELSE 0 END)          AS share_weekend
FROM hourly_panel
WHERE in_study
GROUP BY price_class
ORDER BY price_class;
