from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_api_key: str
    llm_base_url: str = "https://api.x.ai/v1"
    llm_model: str = "grok-4-fast"
    llm_auto_select: bool = True
    llm_prefer_order: str = ""
    llm_fallback_model: str | None = None
    llm_fallback_base_url: str | None = None
    ph_api_token: str | None = None
    discord_webhook_url: str | None = None
    discord_webhook_url_published: str | None = None
    tz_label: str = "Asia/Kolkata"
    guides_per_run: int = 3
    source_mode: str = "merge"
    history_days: int = 40
    email_llm_api_key: str | None = None
    email_llm_base_url: str | None = None
    email_llm_model: str | None = None
    email_llm_auto_select: bool = True
    email_llm_prefer_order: str = ""
    gmail_user: str | None = None
    gmail_app_password: str | None = None
    gmail_sender: str = "noreply@redditmail.com"
    gmail_lookback_days: int = 7
    gmail_allowed_subreddits: str = (
        "selfhosted,appideas,indiehackers,startups,startup_ideas,sideproject"
    )
    dry_run: bool = False


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
