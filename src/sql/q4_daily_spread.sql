-- Q4. Daily price spread (max - min within a DAM trading day), full I-SEM history.
-- The study-period flag lets charts show the study window against earlier years.
SELECT trading_date,
       MAX(price_eur_mwh)                       AS max_price,
       MIN(price_eur_mwh)                       AS min_price,
       MAX(price_eur_mwh) - MIN(price_eur_mwh)  AS spread,
       AVG(price_eur_mwh)                       AS mean_price,
       COUNT(*)                                 AS n_hours,
       BOOL_AND(in_study)                       AS in_study
FROM hourly_panel
GROUP BY trading_date
HAVING COUNT(*) >= 23          -- drop the partial first/last trading days of the file
ORDER BY trading_date;
