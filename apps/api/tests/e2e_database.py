"""Prepare the database for the browser end-to-end test (apps/web/e2e, M7): create it if missing,
migrate it, empty it and load the CMHC reference rents. Separate from the dev and integration
databases ("<dev database>_e2e_test"), so an e2e run never touches dev data.

Run from apps/api: .venv/Scripts/python.exe -m tests.e2e_database   (prints the database URL)
Playwright's global setup runs this before starting the API and the worker.
"""

import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from rentalscamchecker.config import get_settings
from rentalscamchecker.reference_rents import load_rows, read_csv
from tests.db import ensure_database_exists, migrate_to_head, truncate_all_tables

REFERENCE_CSV = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "reference"
    / "cmhc"
    / "rms_average_rent_2025_10.csv"
)


def e2e_database_url() -> str:
    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}_e2e_test").render_as_string(hide_password=False)


def prepare() -> str:
    url = e2e_database_url()
    ensure_database_exists(url)
    migrate_to_head(url)
    truncate_all_tables(url)
    engine = sa.create_engine(url)
    try:
        with Session(engine) as session, session.begin():
            load_rows(session, read_csv(REFERENCE_CSV))
    finally:
        engine.dispose()
    return url


if __name__ == "__main__":
    sys.stdout.write(prepare() + "\n")
