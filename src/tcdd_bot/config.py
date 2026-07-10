import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    redis_url: str
    admin_chat_id: int | None
    allowed_chat_ids: frozenset[int]  # empty = open to everyone
    timezone: str
    log_level: str
    max_alarms_per_user: int
    search_rate_per_hour: int
    check_interval_min: int  # how often the alarm checker runs
    tcdd_mode: str  # "stub" or "live"
    # Fly deploy token so /stop can stop this machine via the Machines API.
    # Unset (e.g. local dev) => /stop replies "not configured" instead of stopping.
    fly_api_token: str | None = None


def _parse_chat_ids(raw: str | None) -> frozenset[int]:
    if not raw:
        return frozenset()
    return frozenset(int(part) for part in raw.split(",") if part.strip())


def load_settings() -> Settings:
    admin = os.getenv("ADMIN_CHAT_ID")
    return Settings(
        bot_token=os.environ["BOT_TOKEN"],
        redis_url=os.environ["REDIS_URL"],
        admin_chat_id=int(admin) if admin else None,
        allowed_chat_ids=_parse_chat_ids(os.getenv("ALLOWED_CHAT_IDS")),
        timezone=os.getenv("TIMEZONE", "Europe/Istanbul"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        max_alarms_per_user=int(os.getenv("MAX_ALARMS_PER_USER", "5")),
        search_rate_per_hour=int(os.getenv("SEARCH_RATE_PER_HOUR", "10")),
        check_interval_min=int(os.getenv("CHECK_INTERVAL_MIN", "10")),
        tcdd_mode=os.getenv("TCDD_MODE", "live"),
        fly_api_token=os.getenv("FLY_API_TOKEN") or None,
    )
