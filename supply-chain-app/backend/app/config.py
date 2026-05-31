from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql://localhost/supplychain"
    anthropic_api_key: str = ""
    tavily_api_key: str = ""
    alpha_vantage_api_key: str = ""
    secret_key: str = "change-me-in-production"

    model_config = {"env_file": ".env"}


settings = Settings()
