import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    upstash_url: str
    upstash_token: str
    admin_chat_id: int | None
    timezone: str
    log_level: str
    max_alarms_per_user: int
    search_rate_per_hour: int
    tcdd_mode: str  # "stub" or "live"


def load_settings() -> Settings:
    admin = os.getenv("ADMIN_CHAT_ID")
    return Settings(
        bot_token=os.environ["BOT_TOKEN"],
        upstash_url=os.environ["UPSTASH_REDIS_REST_URL"],
        upstash_token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
        admin_chat_id=int(admin) if admin else None,
        timezone=os.getenv("TIMEZONE", "Europe/Istanbul"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        max_alarms_per_user=int(os.getenv("MAX_ALARMS_PER_USER", "5")),
        search_rate_per_hour=int(os.getenv("SEARCH_RATE_PER_HOUR", "10")),
        tcdd_mode=os.getenv("TCDD_MODE", "stub"),
    )
