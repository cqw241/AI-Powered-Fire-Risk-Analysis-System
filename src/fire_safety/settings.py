"""Application settings for the fire risk analysis system."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fire_safety import PROJECT_ROOT


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(
        # Anchor to the project root so the file is found regardless of CWD.
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 7860
    qwen_base_url: str | None = None
    qwen_api_key: SecretStr | None = None
    qwen_model: str = "Qwen3.8-27B"
    qwen_reasoning_effort: Literal["low", "medium", "xhigh"] = "low"

    max_image_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_image_width: int = Field(default=8192, gt=0)
    max_image_height: int = Field(default=8192, gt=0)
    max_image_pixels: int = Field(default=40_000_000, gt=0)
    allowed_image_formats: tuple[str, ...] = ("JPEG", "PNG", "WEBP")

    @field_validator("allowed_image_formats")
    @classmethod
    def validate_image_formats(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        supported = {"JPEG", "PNG", "WEBP"}
        if not normalized:
            raise ValueError("at least one image format is required")
        if len(set(normalized)) != len(normalized):
            raise ValueError("image formats must not contain duplicates")
        if unsupported := set(normalized) - supported:
            raise ValueError(f"unsupported image formats: {', '.join(sorted(unsupported))}")
        return normalized

    @property
    def qwen_configured(self) -> bool:
        """Return whether the values needed by the future Qwen client are present."""

        return bool(self.qwen_base_url and self.qwen_api_key)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
