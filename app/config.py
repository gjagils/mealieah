from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mealie_url: str = "http://mealie:9000"
    mealie_api_token: str = ""

    postgres_user: str = "mealieuser"
    postgres_password: str = "mealiepass"
    postgres_server: str = "mealie-db"
    postgres_port: int = 5432
    postgres_db: str = "mealie"

    log_level: str = "INFO"
    mealie_external_url: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_server}:{self.postgres_port}/{self.postgres_db}"
        )

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
