"""A signal that checks nothing: it sleeps, then returns a fixed result. It exists to exercise
the job pipeline end to end (M2) before the real signals arrive (M3 onward)."""

import time

from rentalscamchecker.signals.base import CheckInput, SignalContext, SignalResult


class StubSignal:
    name = "stub"
    version = "1"

    def __init__(self, sleep_seconds: float) -> None:
        self.sleep_seconds = sleep_seconds

    def run(self, check: CheckInput, ctx: SignalContext) -> SignalResult:
        time.sleep(self.sleep_seconds)
        return SignalResult(
            name=self.name,
            version=self.version,
            status="ok",
            severity="none",
            evidence={"stub": True, "slept_seconds": self.sleep_seconds},
            explanation="Pipeline test signal: no real check was performed.",
            next_step="Nothing to do. This signal only tests the processing pipeline.",
        )
