from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Validator Dashboard"
    APP_ENV: str = "dev"
    DEBUG: bool = True

    DATABASE_URL: str = "sqlite:///./validator_dashboard.db"
    API_V1_PREFIX: str = "/api/v1"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
