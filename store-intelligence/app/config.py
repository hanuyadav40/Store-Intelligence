"""
Application configuration using Pydantic Settings.
All settings are read from environment variables with sensible defaults.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = (
        "postgresql+asyncpg://si_user:si_password@localhost:5432/store_intelligence"
    )
    sync_database_url: str = (
        "postgresql+psycopg2://si_user:si_password@localhost:5432/store_intelligence"
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Application
    environment: str = "development"
    secret_key: str = "dev-secret-key"
    log_level: str = "INFO"

    # Data paths
    store_layout_path: str = "./data/store_layout.json"
    pos_data_path: str = "./data/pos_transactions.csv"

    # Business logic thresholds
    reentry_window_minutes: int = 5
    stale_feed_threshold_minutes: int = 10
    dwell_emit_interval_seconds: int = 30
    pos_correlation_window_minutes: int = 5
    dead_zone_threshold_minutes: int = 30
    queue_spike_warn_threshold: int = 5
    queue_spike_critical_threshold: int = 10
    conversion_drop_warn_pct: float = 0.20
    conversion_drop_critical_pct: float = 0.35

    # API limits
    max_event_batch_size: int = 500


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
