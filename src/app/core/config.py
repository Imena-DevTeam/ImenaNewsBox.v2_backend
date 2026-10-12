"""Application settings, read from environment variables (and `.env` locally)."""

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Every variable here has a placeholder in `.env.example`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    environment: Literal["development", "test", "production"] = "development"
    # Comma-separated in the environment, e.g. "http://localhost:3000,https://example.com".
    cors_origins: Annotated[list[str], NoDecode] = []

    # Database: pooled URL for the app, direct URL for Alembic.
    database_url: str
    database_url_direct: str

    # Auth tokens. Lifetimes are in seconds.
    access_token_secret: SecretStr
    refresh_token_secret: SecretStr
    access_token_expires_in: int = 900
    refresh_token_expires_in: int = 7_776_000

    # First admin (seed script only)
    first_admin_family_name: str = ""
    first_admin_email: str = ""
    first_admin_password: SecretStr = SecretStr("")

    # Email (provider not chosen yet)
    email_from: str = ""
    resend_api_key: SecretStr = SecretStr("")
    gmail_client_id: str = ""
    gmail_client_secret: SecretStr = SecretStr("")
    gmail_refresh_token: SecretStr = SecretStr("")

    # Sanity CMS
    sanity_project_id: str = ""
    sanity_dataset: str = "production"
    sanity_api_read_token: SecretStr = SecretStr("")
    sanity_api_write_token: SecretStr = SecretStr("")
    sanity_webhook_secret: SecretStr = SecretStr("")

    # Cloudinary
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: SecretStr = SecretStr("")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _require_token_secrets_in_production(self) -> Self:
        if self.environment == "production":
            for name in ("access_token_secret", "refresh_token_secret"):
                if len(getattr(self, name).get_secret_value()) < 32:
                    raise ValueError(f"{name} must be at least 32 characters in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
