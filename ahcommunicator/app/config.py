from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    database_url: str = "sqlite:///./data/ahcommunicator.db"
    log_level: str = "INFO"
    # Gedeelde pincode voor het gezin; leeg = geen beveiliging (alleen lokaal gebruiken!)
    app_pin: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
