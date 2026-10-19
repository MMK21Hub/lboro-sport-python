"""Environment-backed configuration for the Legend API client."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Credentials and endpoints for the Legend facility-hire API.

    Values are read from the environment or a local ``.env`` file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    api_base_url: str = Field(validation_alias="LBORO_API_BASE_URL")
    oauth_token_url: str = Field(validation_alias="LBORO_OAUTH_TOKEN_URL")
    client_id: str = Field(validation_alias="LBORO_CLIENT_ID")
    client_secret: str = Field(validation_alias="LBORO_CLIENT_SECRET")
    username: str | None = Field(default=None, validation_alias="LBORO_USERNAME")
    password: str | None = Field(default=None, validation_alias="LBORO_PASSWORD")

    @field_validator("api_base_url", "oauth_token_url")
    @classmethod
    def _strip_whitespace(cls, value: str) -> str:
        return value.strip()

    @property
    def has_user_credentials(self) -> bool:
        return bool(self.username and self.password)
