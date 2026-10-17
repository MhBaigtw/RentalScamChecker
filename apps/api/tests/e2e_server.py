"""The backend for the browser end-to-end test (apps/web/e2e, M7), in one process tree:
prepare the e2e database, start a worker as a child process, then serve the API.

Playwright starts this as a web server and stops it after the tests. It uses its own database
(tests/e2e_database.py), photo folder and port, so it can run next to the dev servers.
Only signals that make no network calls are enabled: no Anthropic, Nominatim or TinEye request
is ever made from an e2e run.

Run from apps/api: .venv/Scripts/python.exe -m tests.e2e_server
"""

import os
import subprocess
import sys

from rentalscamchecker.__main__ import main as serve_api
from rentalscamchecker.config import get_settings
from tests.e2e_database import prepare

E2E_ENV = {
    "API_PORT": os.environ.get("E2E_API_PORT", "8110"),
    "WEB_ORIGINS": os.environ.get("E2E_WEB_ORIGIN", "http://127.0.0.1:3110"),
    "ENABLED_SIGNALS": "price_vs_market,photo_reuse_internal,photo_reuse_web,contact_reuse",
    "TINEYE_ENABLED": "false",
    "PHOTO_STORAGE_DIR": "var/e2e-photos",
    "CHECK_TIMEOUT_SECONDS": "60",
    "WORKER_POLL_INTERVAL_SECONDS": "0.2",
}


def main() -> None:
    os.environ.update(E2E_ENV)
    os.environ["DATABASE_URL"] = prepare()
    # prepare() read the settings before DATABASE_URL pointed at the e2e database: read them
    # again, or the API would use the cached dev database URL.
    get_settings.cache_clear()
    assert get_settings().database_url.endswith("_e2e_test")
    worker = subprocess.Popen([sys.executable, "-m", "rentalscamchecker.worker"], env=os.environ)
    try:
        serve_api()
    finally:
        worker.terminate()
        worker.wait(timeout=30)


if __name__ == "__main__":
    main()
