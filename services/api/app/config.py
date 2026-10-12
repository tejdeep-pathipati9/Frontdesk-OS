from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./frontdesk.db"
    demo_mode: bool = True
    session_secret: str = "local-demo-only-change-before-live-use"
    owner_email: str = "owner@frontdesk.local"
    owner_password: str = ""
    web_origin: str = "http://127.0.0.1:3000"
    vapi_webhook_secret: str = ""
    vapi_public_key: str = ""
    vapi_restaurant_assistant_id: str = ""
    vapi_salon_assistant_id: str = ""
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    agent_api_token: str = ""
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:9b"
    square_access_token: str = ""
    square_location_id: str = ""
    square_environment: str = "sandbox"
    square_api_version: str = "2026-01-22"
    toast_client_id: str = ""
    toast_client_secret: str = ""
    toast_restaurant_guid: str = ""
    toast_api_url: str = "https://ws-api.toasttab.com"
    toast_order_write_enabled: bool = False


@lru_cache
def settings():
    return Settings()
