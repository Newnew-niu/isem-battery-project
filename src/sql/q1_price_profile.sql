-- Q1. Average DAM price by local hour of day, day type and season (study period only).
-- Seasons are meteorological: winter = Dec-Feb, spring = Mar-May, summer = Jun-Aug, autumn = Sep-Nov.
SELECT
    CASE
        WHEN month(ts_local) IN (12, 1, 2) THEN 'winter'
        WHEN month(ts_local) IN (3, 4, 5)  THEN 'spring'
        WHEN month(ts_local) IN (6, 7, 8)  THEN 'summer'
        ELSE 'autumn'
    END                                              AS season,
    CASE WHEN isodow(ts_local) >= 6 THEN 'weekend' ELSE 'weekday' END AS day_type,
    hour(ts_local)                                   AS hour_local,
    AVG(price_eur_mwh)                               AS mean_price,
    MEDIAN(price_eur_mwh)                            AS median_price,
    COUNT(*)                                         AS n_hours
FROM hourly_panel
WHERE in_study
GROUP BY ALL
ORDER BY season, day_type, hour_local;
