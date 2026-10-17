"""Settings, read from environment variables (and the repo-root .env file in development)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/rentalscamchecker/config.py -> repo root is three levels above this package.
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        # .env also holds Postgres and web variables that the API does not use.
        extra="ignore",
    )

    database_url: str
    database_connect_timeout: int = 2
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    api_port: int = 8100
    # Secret key for HMAC-SHA256 of phone numbers and emails (ED-004). Changing it breaks
    # matching against every contact hash stored before the change.
    contact_hash_pepper: SecretStr = Field(min_length=32)
    # Region used to parse phone numbers written without a country code.
    contact_default_region: str = "CA"
    # Root folder for the local-disk photo storage (dev). Relative paths resolve from the repo root.
    photo_storage_dir: Path = Path("var/photos")

    # --- Signals and orchestration (M2) ---
    # Comma-separated signal names to run for every new check (see signals/registry.py).
    enabled_signals: str = (
        "price_vs_market,address_sanity,text_red_flags,photo_reuse_internal,photo_reuse_web"
    )
    stub_signal_sleep_seconds: float = 1.0
    # The aggregator runs when every signal has a result, or after this long, whichever is first.
    check_timeout_seconds: float = 120.0

    # --- price_vs_market (M3) ---
    # Severity when the rent is at least this many percent below the CMHC reference rent.
    # Never shown to users (SPEC 10: don't expose rule thresholds).
    price_medium_below_percent: float = Field(default=30.0, gt=0, lt=100)
    price_high_below_percent: float = Field(default=50.0, gt=0, lt=100)

    # --- address_sanity / geocoding (M3) ---
    # Required when address_sanity is enabled: Nominatim's usage policy asks clients to identify
    # themselves; the address goes in our User-Agent so the operator can reach us. Never hard-coded.
    nominatim_contact_email: str | None = None
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    # Policy: an absolute maximum of 1 request per second, across all our workers.
    nominatim_min_interval_seconds: float = Field(default=1.0, ge=1.0)
    # HTTP timeouts for Nominatim. httpx applies each per phase (connect; each read), not as a
    # total, so the worst case is the sum; see _external_calls_fit_in_job_timeout.
    geocoder_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    geocoder_timeout_seconds: float = Field(default=10.0, gt=0)
    # Longest wait for our turn at the Nominatim rate limit (a Postgres lock_timeout).
    geocoder_lock_wait_seconds: float = Field(default=15.0, gt=0)

    # --- text_red_flags: Anthropic API (M4, ED-024) ---
    # Read from the environment / .env; never logged. Required when text_red_flags is enabled.
    anthropic_api_key: SecretStr | None = None
    # Configurable. Default chosen by measurement (ED-024): on the M4 comparison claude-haiku-5-5
    # matched claude-opus-5-5 on live checks at ~1/50 of the cost and about half the latency.
    anthropic_model: str = "claude-haiku-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    # Per-request HTTP timeout. SDK retries are off (the job queue retries), so this is the
    # whole wall-clock budget of one call.
    anthropic_timeout_seconds: float = Field(default=25.0, gt=0)
    anthropic_max_retries: int = Field(default=0, ge=0)
    # Output ceiling per call (thinking + JSON). Caps the worst-case cost of one call.
    anthropic_max_tokens: int = Field(default=8000, ge=1024)
    # Cost guards: at most this many API calls per UTC day across all workers (cache hits are
    # free), and listings longer than this go to the keyword fallback instead of the API.
    llm_daily_call_cap: int = Field(default=200, ge=0)
    llm_max_input_chars: int = Field(default=8000, ge=100)
    # Longest wait to reserve a call under the daily cap (a Postgres lock).
    llm_cap_lock_wait_seconds: float = Field(default=5.0, gt=0)

    # --- Photo signals (M5, ED-025) ---
    # Pretrained CLIP ViT-B/32 image encoder in ONNX (see photos/model_files.py for the choices).
    photo_embedding_model: str = "clip-vit-b32-int8"
    # Model files live here (gitignored). Relative paths resolve from the repo root.
    photo_model_dir: Path = Path("var/models")

    @property
    def photo_model_path(self) -> Path:
        return (
            self.photo_model_dir
            if self.photo_model_dir.is_absolute()
            else REPO_ROOT / self.photo_model_dir
        )

    # --- photo_reuse_web: paid reverse image search (M5). OFF until spend is approved. ---
    tineye_enabled: bool = False
    tineye_api_key: SecretStr | None = None
    tineye_api_url: str = "https://api.tineye.com/rest/"
    tineye_timeout_seconds: float = Field(default=15.0, gt=0)
    # Spend cap: paid searches per UTC day across all workers (cache hits are free).
    tineye_daily_search_cap: int = Field(default=20, ge=0)
    # Photos searched per check (cost and time); the rest are skipped and the result says so.
    tineye_max_photos_per_check: int = Field(default=2, ge=1, le=10)
    tineye_cap_lock_wait_seconds: float = Field(default=5.0, gt=0)

    # --- Internal endpoints ---
    # Bearer token for GET /internal/metrics. If unset, the endpoint is open in development/test
    # and disabled in production.
    internal_metrics_token: SecretStr | None = None

    # --- Job queue and worker (M2, ED-002) ---
    job_max_attempts: int = Field(default=5, ge=1)
    # A claimed job is reserved for this long; if the worker hasn't finished by then (crash,
    # hang), the job is handed out again. Must be longer than any single signal can take.
    job_lease_seconds: float = 60.0
    # Hard limit for one attempt of one job, always below the lease (checked at startup): an
    # attempt that overruns is failed as retryable before its lease can expire (ED-017).
    job_timeout_seconds: float = 45.0
    # Retry n waits min(max, base * 2^(n-1)) seconds.
    job_backoff_base_seconds: float = 2.0
    job_backoff_max_seconds: float = 300.0
    worker_concurrency: int = Field(default=2, ge=1)
    # How long an idle worker thread waits before polling the queue again.
    worker_poll_interval_seconds: float = 1.0
    # How often each worker process writes its counters for /internal/metrics.
    worker_heartbeat_interval_seconds: float = Field(default=5.0, gt=0)

    @model_validator(mode="after")
    def _timeout_below_lease(self) -> "Settings":
        if not 0 < self.job_timeout_seconds < self.job_lease_seconds:
            raise ValueError(
                f"JOB_TIMEOUT_SECONDS ({self.job_timeout_seconds}) must be > 0 and less than "
                f"JOB_LEASE_SECONDS ({self.job_lease_seconds})"
            )
        return self

    @model_validator(mode="after")
    def _external_calls_fit_in_job_timeout(self) -> "Settings":
        """Every external call must give up on its own before the job timeout (ED-017), so a
        slow service fails the attempt cleanly instead of leaving an abandoned thread behind.
        Add each new client's worst case here (M4: the Anthropic API)."""
        geocoder_worst_case = (
            self.geocoder_lock_wait_seconds
            + self.nominatim_min_interval_seconds
            + self.geocoder_connect_timeout_seconds
            + self.geocoder_timeout_seconds
        )
        if geocoder_worst_case >= self.job_timeout_seconds:
            raise ValueError(
                f"geocoder worst case ({geocoder_worst_case:g} s = lock wait + rate-limit interval "
                f"+ connect + read timeouts) must be below JOB_TIMEOUT_SECONDS "
                f"({self.job_timeout_seconds:g} s)"
            )
        # The SDK applies its timeout per attempt and retries timeouts, so the wall clock can
        # reach timeout x (max_retries + 1).
        anthropic_worst_case = self.llm_cap_lock_wait_seconds + self.anthropic_timeout_seconds * (
            self.anthropic_max_retries + 1
        )
        tineye_worst_case = self.tineye_cap_lock_wait_seconds + (
            self.tineye_max_photos_per_check * self.tineye_timeout_seconds
        )
        if self.tineye_enabled and tineye_worst_case >= self.job_timeout_seconds:
            raise ValueError(
                f"TinEye worst case ({tineye_worst_case:g} s = cap lock wait + photos per check x "
                f"timeout) must be below JOB_TIMEOUT_SECONDS ({self.job_timeout_seconds:g} s)"
            )
        if anthropic_worst_case >= self.job_timeout_seconds:
            raise ValueError(
                f"Anthropic worst case ({anthropic_worst_case:g} s = cap lock wait + timeout x "
                f"(retries + 1)) must be below JOB_TIMEOUT_SECONDS ({self.job_timeout_seconds:g} s)"
            )
        return self

    @model_validator(mode="after")
    def _price_thresholds_ordered(self) -> "Settings":
        if not self.price_medium_below_percent < self.price_high_below_percent:
            raise ValueError(
                "PRICE_MEDIUM_BELOW_PERCENT must be less than PRICE_HIGH_BELOW_PERCENT"
            )
        return self

    @property
    def enabled_signal_names(self) -> list[str]:
        return [name.strip() for name in self.enabled_signals.split(",") if name.strip()]

    @property
    def photo_storage_path(self) -> Path:
        if self.photo_storage_dir.is_absolute():
            return self.photo_storage_dir
        return REPO_ROOT / self.photo_storage_dir


@lru_cache
def get_settings() -> Settings:
    # mypy can't see that BaseSettings fills database_url from the environment.
    return Settings()  # type: ignore[call-arg]
