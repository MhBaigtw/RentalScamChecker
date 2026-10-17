"""Which signals exist, and which are enabled (ENABLED_SIGNALS).

`enabled_signals()` runs when the API and the worker start, so a bad configuration (unknown
signal name, missing NOMINATIM_CONTACT_EMAIL for address_sanity) stops them immediately instead
of failing jobs later.
"""

from collections.abc import Callable

import anthropic
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.config import Settings
from rentalscamchecker.contacts import InvalidContact, normalize_email
from rentalscamchecker.geocoding import NominatimGeocoder
from rentalscamchecker.photos.embeddings import ClipEmbedder
from rentalscamchecker.photos.model_files import MODELS, model_path
from rentalscamchecker.photos.web_search import CachedCappedSearch, TinEyeClient
from rentalscamchecker.signals.address_sanity import AddressSanitySignal
from rentalscamchecker.signals.base import Signal
from rentalscamchecker.signals.photo_reuse_internal import PhotoReuseInternalSignal
from rentalscamchecker.signals.photo_reuse_web import PhotoReuseWebSignal
from rentalscamchecker.signals.price_vs_market import PriceVsMarketSignal
from rentalscamchecker.signals.stub import StubSignal
from rentalscamchecker.signals.text_red_flags import TextRedFlagsSignal
from rentalscamchecker.storage import LocalDiskStorage
from rentalscamchecker.text_flags.llm import ClaudeFlagExtractor

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


def _text_red_flags(settings: Settings, sessions: sessionmaker[Session]) -> Signal:
    key = (
        settings.anthropic_api_key.get_secret_value().strip() if settings.anthropic_api_key else ""
    )
    if not key:
        raise ValueError(
            "ENABLED_SIGNALS includes text_red_flags, but ANTHROPIC_API_KEY is not set. Set it, "
            "or remove text_red_flags from ENABLED_SIGNALS."
        )
    client = anthropic.Anthropic(
        api_key=key,
        timeout=settings.anthropic_timeout_seconds,
        max_retries=settings.anthropic_max_retries,
    )
    extractor = ClaudeFlagExtractor(
        sessions,
        client,
        model=settings.anthropic_model,
        effort=settings.anthropic_effort,
        max_tokens=settings.anthropic_max_tokens,
        daily_cap=settings.llm_daily_call_cap,
        max_input_chars=settings.llm_max_input_chars,
        cap_lock_wait_seconds=settings.llm_cap_lock_wait_seconds,
    )
    return TextRedFlagsSignal(extractor)


def _photo_reuse_internal(settings: Settings, sessions: sessionmaker[Session]) -> Signal:
    if settings.photo_embedding_model not in MODELS:
        raise ValueError(
            f"PHOTO_EMBEDDING_MODEL {settings.photo_embedding_model!r} is unknown; "
            f"known: {', '.join(MODELS)}"
        )
    path = model_path(settings.photo_embedding_model, settings.photo_model_path)
    if not path.exists():
        raise ValueError(
            f"photo_reuse_internal is enabled but the embedding model is missing at {path}. "
            "From apps/api run: .venv/Scripts/python.exe -m rentalscamchecker.photos.model_files"
        )
    storage = LocalDiskStorage(settings.photo_storage_path)
    return PhotoReuseInternalSignal(sessions, storage, ClipEmbedder(path))


def _photo_reuse_web(settings: Settings, sessions: sessionmaker[Session]) -> Signal:
    storage = LocalDiskStorage(settings.photo_storage_path)
    if not settings.tineye_enabled:
        return PhotoReuseWebSignal(sessions, storage, None, settings.tineye_max_photos_per_check)
    key = settings.tineye_api_key.get_secret_value().strip() if settings.tineye_api_key else ""
    if not key:
        raise ValueError("TINEYE_ENABLED is true but TINEYE_API_KEY is not set")
    client = TinEyeClient(key, settings.tineye_api_url, settings.tineye_timeout_seconds)
    search = CachedCappedSearch(
        sessions,
        client,
        daily_cap=settings.tineye_daily_search_cap,
        cap_lock_wait_seconds=settings.tineye_cap_lock_wait_seconds,
    )
    return PhotoReuseWebSignal(sessions, storage, search, settings.tineye_max_photos_per_check)


# name -> factory. Each real signal is added here in its milestone.
AVAILABLE: dict[str, SignalFactory] = {
    "stub": _stub,
    "price_vs_market": _price_vs_market,
    "address_sanity": _address_sanity,
    "text_red_flags": _text_red_flags,
    "photo_reuse_internal": _photo_reuse_internal,
    "photo_reuse_web": _photo_reuse_web,
}


def enabled_signals(settings: Settings, session_factory: sessionmaker[Session]) -> list[Signal]:
    """The enabled signals, in configured order. Fails at startup on a bad configuration."""
    unknown = [n for n in settings.enabled_signal_names if n not in AVAILABLE]
    if unknown:
        raise ValueError(f"ENABLED_SIGNALS has unknown signal(s): {', '.join(unknown)}")
    return [AVAILABLE[name](settings, session_factory) for name in settings.enabled_signal_names]
