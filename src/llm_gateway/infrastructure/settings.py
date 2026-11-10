"""Service settings from `<ENV_PREFIX>*` environment variables. Read only in bootstrap."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .. import ENV_PREFIX


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")

    environment: str = "dev"
    log_level: str = Field("INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
