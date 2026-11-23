from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> project root is 3 levels above "core"
ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    app_name: str = "Pinessa AI Order-to-Delivery Copilot (dev)"
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    order_duplicate_days: int = 7
    delivery_storage: Path = ROOT_DIR / "storage" / "deliveries"
    delivery_upload_limit: int = 10 * 1024 * 1024
    dev_seed_password: str | None = None

    @field_validator("jwt_secret_key")
    @classmethod
    def secret_must_be_real(cls, value: str) -> str:
        if len(value) < 32 or value.startswith("replace_with"):
            raise ValueError("JWT_SECRET_KEY must be a random string of at least 32 characters")
        return value

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
