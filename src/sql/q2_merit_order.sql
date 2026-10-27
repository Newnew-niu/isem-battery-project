-- Q2. Merit-order effect: price distribution by band of wind penetration
-- (actual all-island wind / all-island system demand). Descriptive use of actuals only.
WITH banded AS (
    SELECT price_eur_mwh,
           wind_share,
           CASE
               WHEN wind_share < 0.10 THEN '0-10%'
               WHEN wind_share < 0.20 THEN '10-20%'
               WHEN wind_share < 0.30 THEN '20-30%'
               WHEN wind_share < 0.40 THEN '30-40%'
               WHEN wind_share < 0.50 THEN '40-50%'
               WHEN wind_share < 0.60 THEN '50-60%'
               ELSE '60%+'
           END AS wind_band
    FROM hourly_panel
    WHERE in_study AND wind_share IS NOT NULL
)
SELECT wind_band,
       COUNT(*)                                   AS n_hours,
       AVG(price_eur_mwh)                         AS mean_price,
       quantile_cont(price_eur_mwh, 0.10)         AS p10,
       quantile_cont(price_eur_mwh, 0.25)         AS p25,
       MEDIAN(price_eur_mwh)                      AS median_price,
       quantile_cont(price_eur_mwh, 0.75)         AS p75,
       quantile_cont(price_eur_mwh, 0.90)         AS p90,
       AVG((price_eur_mwh <= 0)::INT)             AS share_nonpositive
FROM banded
GROUP BY wind_band
ORDER BY wind_band;
