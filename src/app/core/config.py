
from functools import lru_cache
from pydantic import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    model_config = SettingsConfigDict(env_file=".env",extra="ignore")

    database_url: str
    log_level: str = "INFO"

    access_token_secret_key: str
    access_token_expire_minutes: int

    refresh_token_secret_key: str
    refresh_token_expire_minutes: int


    sanity_project_id: str
    sanity_dataset: str
    sanity_api_read_token: str
    sanity_api_write_token: str
    sanity_webhook_secret: str


@lru_cache()   
def get_settings() -> Settings:
    return Settings()





    

