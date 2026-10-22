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

    zetaris_mode: Literal["live", "snapshot"] = "snapshot"

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

    nvidia_api_key: SecretStr = SecretStr("")
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
