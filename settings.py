from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env.local", env_file_encoding="utf-8", extra="ignore"
    )

    # Runs everything on committed snapshots, with no Zetaris, Nightshift or network access.
    sample_mode: bool = False
    # run.py --serve binds here; 0.0.0.0 inside Docker.
    run_host: str = "127.0.0.1"
    run_port: int = 8000
    # POST /run: at most this many runs at once (503 when busy); "record": true needs API_ALLOW_RECORD=true.
    run_concurrency: int = 4
    api_allow_record: bool = False
    zetaris_mode: Literal["live", "snapshot"] = "snapshot"
    # "Now" for planning: the active fleet's last observed cycle (see data/fleet.py).
    reference_time: datetime = datetime(2026, 10, 22, tzinfo=timezone.utc)

    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_sslmode: str = "require"
    pg_db_telemetry: str = "telemetry"
    pg_db_cmms: str = "cmms"
    pg_db_knowledge: str = "knowledge"
    pg_loader_user: str = ""
    pg_loader_password: SecretStr = SecretStr("")
    pg_app_user: str = ""
    pg_app_password: SecretStr = SecretStr("")

    zetaris_base_url: str = ""
    zetaris_org_id: str = ""
    zetaris_api_key: SecretStr = SecretStr("")
    zetaris_query_limit: int = 1000
    zetaris_timeout_s: float = 10.0
    zetaris_budget_s: float = 30.0

    # Default meterless (SQLite fallback, flagged). Sample mode uses SQLite unless CONTEXT_STORE is set explicitly.
    context_store: Literal["meterless", "sqlite"] = "meterless"
    context_service_url: str = "http://127.0.0.1:8710"
    context_sqlite_path: Path = ROOT / ".state" / "context.sqlite"

    meterless_base_url: str = "http://127.0.0.1:8088/v1"
    meterless_runtime_token: SecretStr = SecretStr("")
    meterless_model: str = "meterless/auto"
    meterless_app: str = "turbofan-maintenance-agent"
    llm_timeout_s: float = 180.0
    run_timeout_s: float = 120.0
    meterless_critic_model: str = "meterless/auto"
    # Separate Nightshift app so its routing rule can keep the critic on another model family.
    meterless_critic_app: str = "turbofan-maintenance-critic"
    critic_timeout_s: float = 45.0
    # Live status strip and GET /health: per-check timeout and how long a result is reused.
    status_timeout_s: float = 2.5
    status_ttl_s: float = 45.0

    def explicit(self, name: str) -> bool:
        """True when the field was set (environment or .env.local), not left at its default."""
        return name in self.model_fields_set


@lru_cache
def get_settings() -> Settings:
    return Settings()
