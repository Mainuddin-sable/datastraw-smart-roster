from functools import lru_cache
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./smart_roster.db"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    app_env: str = "development"
    demo_auth_username: str = ""
    demo_auth_password: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def require_demo_credentials_in_production(self) -> "Settings":
        if self.demo_auth_username and (":" in self.demo_auth_username or not self.demo_auth_username.isascii()):
            raise ValueError("The HTTP Basic username must be ASCII and cannot contain a colon.")
        if self.demo_auth_password.get_secret_value() and not self.demo_auth_password.get_secret_value().isascii():
            raise ValueError("The HTTP Basic password must be ASCII.")
        if self.app_env.casefold() == "production":
            if not self.has_demo_credentials:
                raise ValueError("Demo HTTP Basic credentials must be configured in production.")
            if len(self.demo_auth_password.get_secret_value()) < 16:
                raise ValueError("The production demo password must be at least 16 characters.")
            origins = self.allowed_origins
            if not origins or "*" in origins:
                raise ValueError("Production CORS must list explicit frontend origins.")
            if any(not origin.startswith("https://") for origin in origins):
                raise ValueError("Production CORS origins must use HTTPS.")
        return self

    @property
    def has_demo_credentials(self) -> bool:
        return bool(self.demo_auth_username.strip() and self.demo_auth_password.get_secret_value())

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
