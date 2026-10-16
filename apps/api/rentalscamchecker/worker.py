"""Run the background worker: `python -m rentalscamchecker.worker`.

Same codebase and settings as the API, different entrypoint (ED-001). Stops gracefully on
SIGTERM / SIGINT (Ctrl+C), and on Windows also on CTRL_BREAK (SIGBREAK): running jobs finish,
no new jobs are claimed.
"""

import signal
from types import FrameType

import structlog
from sqlalchemy.orm import sessionmaker

from rentalscamchecker.config import get_settings
from rentalscamchecker.db import make_engine
from rentalscamchecker.log_config import configure_logging
from rentalscamchecker.pipeline import build_worker

log = structlog.get_logger("rentalscamchecker.worker")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url, settings.database_connect_timeout)
    worker = build_worker(settings, sessionmaker(engine, expire_on_commit=False))

    def handle_stop(signum: int, _frame: FrameType | None) -> None:
        if not worker.stopping:
            log.info("shutdown_requested", signal=signal.Signals(signum).name)
        worker.request_stop()

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    if hasattr(signal, "SIGBREAK"):  # Windows only
        signal.signal(signal.SIGBREAK, handle_stop)

    worker.run()
    engine.dispose()


if __name__ == "__main__":
    main()
