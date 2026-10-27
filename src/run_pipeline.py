"""One command for the whole project: download inputs (cached), build the database, run
every phase and regenerate all reports.

    python src/run_pipeline.py            # everything
    python src/run_pipeline.py --data     # downloads + database only
"""
import runpy
import sys
from pathlib import Path

import build_db
import download_ember
import download_energycharts
import download_openmeteo
import download_semopx

SRC = Path(__file__).resolve().parent
STEPS = ["phase2_analysis.py", "phase3_forecast.py", "report_phase3.py", "phase4_backtest.py",
         "report_phase4.py", "phase5_excel.py", "phase5_commentary.py"]


def main():
    download_ember.main()
    download_energycharts.main()
    download_openmeteo.main()
    download_semopx.main()
    build_db.main()
    if "--data" in sys.argv:
        return
    for step in STEPS:
        print(f"\n=== {step}")
        runpy.run_path(str(SRC / step), run_name="__main__")


if __name__ == "__main__":
    main()
