# Validation data: EirGrid Smart Grid Dashboard exports (all-island wind)

These files are **not** a pipeline input. They are EirGrid's own figures, used in
`src/build_db.py` (section 3) to validate the Energy-Charts wind series (see docs/DECISIONS.md, D5).

How they were obtained: each link below was opened in a browser, then 'View In Table' ->
'Download .CSV', and the file saved to `data/raw/sgd_manual/`. Notes:

- The 'month' view covers the **30 days ending on `dateto`** and ignores `datefrom`, so the
  links are back-to-back 30-day windows.
- Exports are in Irish local time. In the repeated autumn DST hour, actual wind is duplicated
  across both copies, so that hour is dropped during validation.
- Demand exports (NI and all-island) returned 'No results' and were not obtained.

## All-island wind generation (26 windows)

1. 2024-09-01 to 2024-09-30: https://www.smartgriddashboard.com/all/wind/?datefrom=01-Sep-2024&dateto=30-Sep-2024&duration=month
2. 2024-10-01 to 2024-10-30: https://www.smartgriddashboard.com/all/wind/?datefrom=01-Oct-2024&dateto=30-Oct-2024&duration=month
3. 2024-10-31 to 2024-11-29: https://www.smartgriddashboard.com/all/wind/?datefrom=31-Oct-2024&dateto=29-Nov-2024&duration=month
4. 2024-11-30 to 2024-12-29: https://www.smartgriddashboard.com/all/wind/?datefrom=30-Nov-2024&dateto=29-Dec-2024&duration=month
5. 2024-12-30 to 2025-01-28: https://www.smartgriddashboard.com/all/wind/?datefrom=30-Dec-2024&dateto=28-Jan-2025&duration=month
6. 2025-01-29 to 2025-02-27: https://www.smartgriddashboard.com/all/wind/?datefrom=29-Jan-2025&dateto=27-Feb-2025&duration=month
7. 2025-02-28 to 2025-03-29: https://www.smartgriddashboard.com/all/wind/?datefrom=28-Feb-2025&dateto=29-Mar-2025&duration=month
8. 2025-03-30 to 2025-04-28: https://www.smartgriddashboard.com/all/wind/?datefrom=30-Mar-2025&dateto=28-Apr-2025&duration=month
9. 2025-04-29 to 2025-05-28: https://www.smartgriddashboard.com/all/wind/?datefrom=29-Apr-2025&dateto=28-May-2025&duration=month
10. 2025-05-29 to 2025-06-27: https://www.smartgriddashboard.com/all/wind/?datefrom=29-May-2025&dateto=27-Jun-2025&duration=month
11. 2025-06-28 to 2025-07-27: https://www.smartgriddashboard.com/all/wind/?datefrom=28-Jun-2025&dateto=27-Jul-2025&duration=month
12. 2025-07-28 to 2025-08-26: https://www.smartgriddashboard.com/all/wind/?datefrom=28-Jul-2025&dateto=26-Aug-2025&duration=month
13. 2025-08-27 to 2025-09-25: https://www.smartgriddashboard.com/all/wind/?datefrom=27-Aug-2025&dateto=25-Sep-2025&duration=month
14. 2025-09-26 to 2025-10-25: https://www.smartgriddashboard.com/all/wind/?datefrom=26-Sep-2025&dateto=25-Oct-2025&duration=month
15. 2025-10-26 to 2025-11-24: https://www.smartgriddashboard.com/all/wind/?datefrom=26-Oct-2025&dateto=24-Nov-2025&duration=month
16. 2025-11-25 to 2025-12-24: https://www.smartgriddashboard.com/all/wind/?datefrom=25-Nov-2025&dateto=24-Dec-2025&duration=month
17. 2025-12-25 to 2026-01-23: https://www.smartgriddashboard.com/all/wind/?datefrom=25-Dec-2025&dateto=23-Jan-2026&duration=month
18. 2026-01-24 to 2026-02-22: https://www.smartgriddashboard.com/all/wind/?datefrom=24-Jan-2026&dateto=22-Feb-2026&duration=month
19. 2026-02-23 to 2026-03-24: https://www.smartgriddashboard.com/all/wind/?datefrom=23-Feb-2026&dateto=24-Mar-2026&duration=month
20. 2026-03-25 to 2026-04-23: https://www.smartgriddashboard.com/all/wind/?datefrom=25-Mar-2026&dateto=23-Apr-2026&duration=month
21. 2026-04-24 to 2026-05-23: https://www.smartgriddashboard.com/all/wind/?datefrom=24-Apr-2026&dateto=23-May-2026&duration=month
22. 2026-05-24 to 2026-06-22: https://www.smartgriddashboard.com/all/wind/?datefrom=24-May-2026&dateto=22-Jun-2026&duration=month
23. 2026-06-23 to 2026-07-22: https://www.smartgriddashboard.com/all/wind/?datefrom=23-Jun-2026&dateto=22-Jul-2026&duration=month
24. 2026-07-23 to 2026-08-21: https://www.smartgriddashboard.com/all/wind/?datefrom=23-Jul-2026&dateto=21-Aug-2026&duration=month
25. 2026-08-22 to 2026-09-20: https://www.smartgriddashboard.com/all/wind/?datefrom=22-Aug-2026&dateto=20-Sep-2026&duration=month
26. 2026-09-21 to 2026-10-20: https://www.smartgriddashboard.com/all/wind/?datefrom=21-Sep-2026&dateto=20-Oct-2026&duration=month
