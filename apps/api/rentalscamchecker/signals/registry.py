"""Which signals exist, and which are enabled (ENABLED_SIGNALS).

`enabled_signals()` runs when the API and the worker start, so a bad configuration (unknown
signal name, missing NOMINATIM_CONTACT_EMAIL for address_sanity) stops them immediately instead
of failing jobs later.
"""

from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.config import Settings
from rentalscamchecker.contacts import InvalidContact, normalize_email
from rentalscamchecker.geocoding import NominatimGeocoder
from rentalscamchecker.signals.address_sanity import AddressSanitySignal
from rentalscamchecker.signals.base import Signal
from rentalscamchecker.signals.price_vs_market import PriceVsMarketSignal
from rentalscamchecker.signals.stub import StubSignal

SignalFactory = Callable[[Settings, sessionmaker[Session]], Signal]


def _stub(settings: Settings, _sessions: sessionmaker[Session]) -> Signal:
    return StubSignal(settings.stub_signal_sleep_seconds)


def _price_vs_market(settings: Settings, sessions: sessionmaker[Session]) -> Signal:
    return PriceVsMarketSignal(
        sessions,
        medium_below_percent=settings.price_medium_below_percent,
        high_below_percent=settings.price_high_below_percent,
    )


def _address_sanity(settings: Settings, sessions: sessionmaker[Session]) -> Signal:
    email = (settings.nominatim_contact_email or "").strip()
    if not email:
        raise ValueError(
            "ENABLED_SIGNALS includes address_sanity, but NOMINATIM_CONTACT_EMAIL is not set. "
            "Nominatim's usage policy requires identifying the application; set a contact email."
        )
    try:
        normalize_email(email)
    except InvalidContact as exc:
        raise ValueError("NOMINATIM_CONTACT_EMAIL is not a valid email address") from exc
    geocoder = NominatimGeocoder(
        sessions,
        contact_email=email,
        base_url=settings.nominatim_base_url,
        min_interval_seconds=settings.nominatim_min_interval_seconds,
        timeout_seconds=settings.geocoder_timeout_seconds,
        connect_timeout_seconds=settings.geocoder_connect_timeout_seconds,
        lock_wait_seconds=settings.geocoder_lock_wait_seconds,
    )
    return AddressSanitySignal(geocoder)


# name -> factory. Each real signal is added here in its milestone.
AVAILABLE: dict[str, SignalFactory] = {
    "stub": _stub,
    "price_vs_market": _price_vs_market,
    "address_sanity": _address_sanity,
}


def enabled_signals(settings: Settings, session_factory: sessionmaker[Session]) -> list[Signal]:
    """The enabled signals, in configured order. Fails at startup on a bad configuration."""
    unknown = [n for n in settings.enabled_signal_names if n not in AVAILABLE]
    if unknown:
        raise ValueError(f"ENABLED_SIGNALS has unknown signal(s): {', '.join(unknown)}")
    return [AVAILABLE[name](settings, session_factory) for name in settings.enabled_signal_names]
