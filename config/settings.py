from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    PROJECT_NAME: str = "Stock Predictor API"
    MONGO_URI: str = "mongodb://localhost:27017"
    DATABASE_NAME: str = Field(default="stock_predictor_db", alias="DB_NAME", validation_alias="DB_NAME")
    COLLECTION_NAME: str = "predictions_history"
    TWELVEDATA_API_KEY: str
    TWELVEDATA_OUTPUTSIZE: str = "100"
    ENVIRONMENT: str = Field(default="dev", alias="ENVIRONMENT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,   # allow both alias and field name
    )


settings = Settings()