"""M3 signals wired into the app: startup checks, and the full journey with both signals.
Reference data is synthetic and Nominatim is faked (no live network in tests)."""

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.geocoding import NominatimGeocoder, query_key
from rentalscamchecker.jobs.worker import Worker, WorkerConfig
from rentalscamchecker.main import create_app
from rentalscamchecker.pipeline import build_handlers, build_worker
from rentalscamchecker.reference_rents import load_rows, read_csv
from rentalscamchecker.signals.address_sanity import AddressSanitySignal
from rentalscamchecker.signals.base import Signal
from rentalscamchecker.signals.price_vs_market import PriceVsMarketSignal
from tests.helpers import UNREACHABLE_DB_URL, image_bytes, make_settings
from tests.nominatim_fake import FakeNominatim, place

FIXTURE = Path(__file__).parent / "fixtures" / "rms_synthetic.csv"
BOTH = "price_vs_market,address_sanity"


@pytest.mark.parametrize("email", [None, "", "   "])
def test_api_refuses_to_start_with_address_sanity_but_no_contact_email(
    email: str | None,
) -> None:
    settings = make_settings(
        UNREACHABLE_DB_URL, enabled_signals=BOTH, nominatim_contact_email=email
    )

    with pytest.raises(ValueError, match="NOMINATIM_CONTACT_EMAIL is not set"):
        create_app(settings)


def test_worker_refuses_to_start_with_address_sanity_but_no_contact_email() -> None:
    settings = make_settings(UNREACHABLE_DB_URL, enabled_signals="address_sanity")

    with pytest.raises(ValueError, match="NOMINATIM_CONTACT_EMAIL is not set"):
        build_worker(settings, sessionmaker())


def test_invalid_contact_email_is_refused() -> None:
    settings = make_settings(
        UNREACHABLE_DB_URL, enabled_signals="address_sanity", nominatim_contact_email="not-an-email"
    )

    with pytest.raises(ValueError, match="not a valid email"):
        create_app(settings)


def test_price_vs_market_alone_needs_no_contact_email() -> None:
    create_app(make_settings(UNREACHABLE_DB_URL, enabled_signals="price_vs_market"))


@pytest.mark.integration
def test_check_runs_both_signals_and_the_report_shows_their_evidence(
    session_factory: sessionmaker[Session], photo_dir: Path
) -> None:
    with session_factory.begin() as s:
        load_rows(s, read_csv(FIXTURE))
    settings = make_settings(
        photo_dir=photo_dir, enabled_signals=BOTH, nominatim_contact_email="ops@example.invalid"
    )
    client = TestClient(create_app(settings))
    fake = FakeNominatim({query_key("1430 Trafalgar Rd", "Oakville"): [place(city="Oakville")]})
    geocoder = NominatimGeocoder(
        session_factory,
        contact_email="ops@example.invalid",
        base_url="https://nominatim.example.invalid",
        min_interval_seconds=1.0,
        timeout_seconds=5,
        transport=fake.transport,
    )
    signals: list[Signal] = [
        PriceVsMarketSignal(session_factory, medium_below_percent=30, high_below_percent=50),
        AddressSanitySignal(geocoder),
    ]
    worker = Worker(
        session_factory,
        build_handlers(session_factory, signals, settings),
        WorkerConfig(concurrency=1, lease_seconds=30, job_timeout_seconds=20),
    )

    created = client.post(
        "/checks",
        data={
            "listing_text": "Bright 2 bed near Sheridan, owner abroad, deposit to hold.",
            "monthly_rent": "1100",
            "bedrooms": "2",
            "address_text": "1430 Trafalgar Rd",
            "city": "Oakville",
        },
        files=[("photos", ("p.jpg", image_bytes(), "image/jpeg"))],
    )
    check_id = uuid.UUID(created.json()["check_id"])
    while worker.run_once():
        pass

    body = client.get(f"/checks/{check_id}").json()
    assert body["status"] == "complete"
    by_name = {s["name"]: s for s in body["signals"]}
    assert set(by_name) == {"price_vs_market", "address_sanity"}

    price = by_name["price_vs_market"]
    assert (price["status"], price["severity"]) == ("ok", "high")
    keys = ("reference_rent_cents", "survey_year", "cma", "rent_basis", "percent_below_reference")
    assert {k: price["evidence"][k] for k in keys} == {
        "reference_rent_cents": 260000,
        "survey_year": 2025,
        "cma": "Toronto",
        "rent_basis": "vacant_units",
        "percent_below_reference": 57.7,
    }

    address = by_name["address_sanity"]
    assert (address["status"], address["severity"]) == ("ok", "none")
    assert address["evidence"]["matched_city"] == "Oakville"
    assert len(fake.requests) == 1
