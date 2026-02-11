from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "", "case_sensitive": False}

    # Database
    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/remo"

    # Temporal
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_api_key: str | None = None
    temporal_task_queue: str = "remo-tasks"

    # Cloudflare R2
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "remo-images"

    # AI APIs
    anthropic_api_key: str = ""
    google_ai_api_key: str = ""
    exa_api_key: str = ""

    # App
    environment: str = "development"
    log_level: str = "INFO"
    presigned_url_expiry_seconds: int = 3600


settings = Settings()
