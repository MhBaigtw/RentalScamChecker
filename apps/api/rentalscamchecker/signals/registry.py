"""Which signals exist, and which are enabled (ENABLED_SIGNALS)."""

from collections.abc import Callable

from rentalscamchecker.config import Settings
from rentalscamchecker.signals.base import Signal
from rentalscamchecker.signals.stub import StubSignal

# name -> factory. Each real signal is added here in its milestone.
AVAILABLE: dict[str, Callable[[Settings], Signal]] = {
    "stub": lambda settings: StubSignal(settings.stub_signal_sleep_seconds),
}


def enabled_signals(settings: Settings) -> list[Signal]:
    """The enabled signals, in configured order. Fails at startup on an unknown name."""
    unknown = [n for n in settings.enabled_signal_names if n not in AVAILABLE]
    if unknown:
        raise ValueError(f"ENABLED_SIGNALS has unknown signal(s): {', '.join(unknown)}")
    return [AVAILABLE[name](settings) for name in settings.enabled_signal_names]
