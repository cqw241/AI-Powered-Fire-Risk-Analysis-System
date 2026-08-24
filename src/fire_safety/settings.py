"""Application settings for the fire risk analysis system."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 7860
    qwen_base_url: str | None = None
    qwen_api_key: SecretStr | None = None
    qwen_model: str = "Qwen3.8-27B"

    @property
    def qwen_configured(self) -> bool:
        """Return whether the values needed by the future Qwen client are present."""

        return bool(self.qwen_base_url and self.qwen_api_key)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
