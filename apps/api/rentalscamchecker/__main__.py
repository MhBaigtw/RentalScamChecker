"""Run the API: `python -m rentalscamchecker` (port from API_PORT, default 8100)."""

import uvicorn

from rentalscamchecker.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "rentalscamchecker.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=settings.api_port,
        # Keep our JSON logging config instead of uvicorn's default one.
        log_config=None,
    )


if __name__ == "__main__":
    main()
