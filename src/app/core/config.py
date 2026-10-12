
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    model_config = SettingsConfigDict(env_file=".env",extra="ignore")

    database_name: str
    database_password: str
    database_user: str
    

    access_token_secret_key: str
    access_token_expire_minutes: int

    refresh_token_secret_key: str
    refresh_token_expire_minutes: int


    sanity_project_id: str
    sanity_dataset: str
    sanity_api_read_token: str
    sanity_api_write_token: str
    sanity_webhook_secret: str

    # to keep our code clean and understandable we use the @property annotation

    @property
    def database_url(self) -> str :
        return f"postgresql+asyncpg://{self.database_user}:{self.database_password}@localhost:5432/{self.database_name}"


@lru_cache()   
def get_settings() -> Settings:
    return Settings()





    

