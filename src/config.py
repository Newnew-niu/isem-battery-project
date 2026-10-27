"""Shared paths and study-period settings (see docs/DECISIONS.md)."""
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
SQL_DIR = ROOT / "src" / "sql"
REPORTS = ROOT / "reports"
DB_PATH = PROCESSED / "isem.duckdb"

LOCAL_TZ = "Europe/Dublin"
# I-SEM trading day D runs 23:00 Irish local on D-1 to 23:00 on D, i.e. the CET calendar day.
TRADING_DAY_TZ = "Europe/Brussels"

# D1: two full gas years; September 2024 is warm-up for lagged features only.
STUDY_START = date(2024, 10, 1)
STUDY_END = date(2026, 9, 30)  # inclusive trading date
WARMUP_START = date(2024, 9, 1)
