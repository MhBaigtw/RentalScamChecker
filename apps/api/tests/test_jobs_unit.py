import pytest
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.jobs.queue import backoff_seconds
from rentalscamchecker.signals.base import CheckInput, SignalContext
from rentalscamchecker.signals.registry import enabled_signals
from rentalscamchecker.signals.stub import StubSignal
from tests.helpers import UNREACHABLE_DB_URL, make_settings

# Signals are only constructed here, never run, so they get an unbound session factory.
NO_DB: sessionmaker[Session] = sessionmaker()


@pytest.mark.parametrize(
    ("attempt", "expected"), [(1, 2.0), (2, 4.0), (3, 8.0), (4, 16.0), (8, 256.0), (9, 300.0)]
)
def test_backoff_doubles_per_attempt_up_to_the_cap(attempt: int, expected: float) -> None:
    assert backoff_seconds(attempt, base=2.0, cap=300.0) == expected


def test_enabled_signals_follow_settings() -> None:
    settings = make_settings(UNREACHABLE_DB_URL, enabled_signals=" stub , ")

    assert [s.name for s in enabled_signals(settings, NO_DB)] == ["stub"]


def test_no_enabled_signals_is_allowed() -> None:
    assert enabled_signals(make_settings(UNREACHABLE_DB_URL, enabled_signals=""), NO_DB) == []


def test_unknown_signal_name_fails_at_startup() -> None:
    with pytest.raises(ValueError, match="unknown signal"):
        enabled_signals(make_settings(UNREACHABLE_DB_URL, enabled_signals="stub,tineye"), NO_DB)


def test_stub_signal_returns_its_fixed_result() -> None:
    import uuid

    check = CheckInput(uuid.uuid4(), "text", 100_000, 1, "1 Main St", "Oakville", None)
    ctx = SignalContext(settings=make_settings(UNREACHABLE_DB_URL))

    result = StubSignal(sleep_seconds=0).run(check, ctx)

    assert (result.name, result.version, result.status, result.severity) == (
        "stub",
        "1",
        "ok",
        "none",
    )
    assert result.explanation and result.next_step


def test_settings_reject_a_job_timeout_not_below_the_lease() -> None:
    with pytest.raises(ValueError, match="JOB_TIMEOUT_SECONDS"):
        make_settings(UNREACHABLE_DB_URL, job_lease_seconds=60, job_timeout_seconds=60)


def test_default_job_timeout_is_below_the_default_lease() -> None:
    settings = make_settings(UNREACHABLE_DB_URL)

    assert (settings.job_timeout_seconds, settings.job_lease_seconds) == (45.0, 60.0)


def test_geocoder_worst_case_must_fit_inside_the_job_timeout() -> None:
    # lock wait 30 + interval 1 + connect 5 + read 10 = 46 s > 45 s job timeout.
    with pytest.raises(ValueError, match="geocoder worst case"):
        make_settings(UNREACHABLE_DB_URL, geocoder_lock_wait_seconds=30)


def test_default_geocoder_budget_fits() -> None:
    settings = make_settings(UNREACHABLE_DB_URL)

    worst = (
        settings.geocoder_lock_wait_seconds
        + settings.nominatim_min_interval_seconds
        + settings.geocoder_connect_timeout_seconds
        + settings.geocoder_timeout_seconds
    )
    assert worst == 31 < settings.job_timeout_seconds
