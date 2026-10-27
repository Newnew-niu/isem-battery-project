"""One-command data pipeline: download all inputs (cached), then build the DuckDB database
and the data-quality log.

    python src/run_pipeline.py
"""
import build_db
import download_ember
import download_energycharts
import download_openmeteo


def main():
    download_ember.main()
    download_energycharts.main()
    download_openmeteo.main()
    build_db.main()


if __name__ == "__main__":
    main()
