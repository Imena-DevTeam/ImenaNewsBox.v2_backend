"""Application settings, read from environment variables (and `.env` locally)."""

from email.utils import parseaddr
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
    # Only read by the test suite. Its name must end in `_test`, because tests reset its schema.
    test_database_url: str = ""

    # Auth. ACCESS_TOKEN_SECRET signs access tokens (JWT); REFRESH_TOKEN_SECRET and CODE_SECRET
    # key the HMACs stored for refresh tokens and email codes. Lifetimes are in seconds.
    access_token_secret: SecretStr
    refresh_token_secret: SecretStr
    code_secret: SecretStr
    access_token_expires_in: int = 900
    refresh_token_expires_in: int = 7_776_000

    # Google sign-in: the OAuth client ID of the frontend's "Sign in with Google" button.
    # Public (it's in the frontend too), so not a secret. Empty turns Google sign-in off.
    google_client_id: str = ""

    # First admin (seed script only)
    first_admin_family_name: str = ""
    first_admin_email: str = ""
    first_admin_password: SecretStr = SecretStr("")
    # The name of an existing sub-family (they come from a migration); it's never created.
    first_admin_sub_family: str = ""

    # v1's MongoDB (scripts/import_v1.py only). The database name comes from the URI.
    v1_mongodb_uri: SecretStr = SecretStr("")

    # Email over SMTP. Until SMTP_HOST and EMAIL_FROM are set, sending fails clearly.
    # EMAIL_FROM is e.g. "Imena NewsBox <no-reply@example.org>".
    email_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    # "starttls" (usually port 587), "tls" (usually 465), or "none" for a relay on the
    # same machine, which then can't take a username or password.
    smtp_security: Literal["starttls", "tls", "none"] = "starttls"
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")

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

    @field_validator("cors_origins")
    @classmethod
    def _reject_wildcard_origin(cls, value: list[str]) -> list[str]:
        # CORS runs with credentials, and the refresh/logout Origin check reads this list.
        if "*" in value:
            raise ValueError("CORS_ORIGINS must list explicit origins, never '*'")
        return value

    @field_validator("email_from")
    @classmethod
    def _check_email_from(cls, value: str) -> str:
        if value and "@" not in parseaddr(value)[1]:
            raise ValueError(
                'EMAIL_FROM must be an address, e.g. "Imena NewsBox <no-reply@example.org>"'
            )
        return value

    @model_validator(mode="after")
    def _no_smtp_password_in_the_clear(self) -> Self:
        if self.smtp_security == "none" and (
            self.smtp_username or self.smtp_password.get_secret_value()
        ):
            raise ValueError(
                "SMTP_USERNAME and SMTP_PASSWORD need SMTP_SECURITY=starttls or tls, "
                "so the password is never sent unencrypted"
            )
        return self

    @model_validator(mode="after")
    def _require_token_secrets_in_production(self) -> Self:
        if self.environment == "production":
            for name in ("access_token_secret", "refresh_token_secret", "code_secret"):
                if len(getattr(self, name).get_secret_value()) < 32:
                    raise ValueError(f"{name} must be at least 32 characters in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
