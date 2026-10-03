"""Central configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(_env(key, str(default)) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = _env("TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = _env("TELEGRAM_CHAT_ID")

    supabase_url: str = _env("SUPABASE_URL")
    supabase_key: str = _env("SUPABASE_SERVICE_KEY")

    fmp_api_key: str = _env("FMP_API_KEY")
    finnhub_api_key: str = _env("FINNHUB_API_KEY")
    ff_proxy_url: str = _env("FF_PROXY_URL")

    tz_name: str = _env("TZ_NAME", "Asia/Damascus")
    log_level: str = _env("LOG_LEVEL", "INFO")

    gold_binance_symbol: str = _env("GOLD_BINANCE_SYMBOL", "PAXGUSDT")
    gold_yahoo_symbol: str = _env("GOLD_YAHOO_SYMBOL", "GC=F")

    min_importance: str = _env("MIN_IMPORTANCE", "high").lower()
    allowed_countries: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            c.strip().upper()
            for c in _env("ALLOWED_COUNTRIES", "US").split(",")
            if c.strip()
        )
    )

    pre_event_minutes: int = _env_int("PRE_EVENT_MINUTES", 60)
    reminder_minutes: int = _env_int("REMINDER_MINUTES", 15)
    post_event_minutes: int = _env_int("POST_EVENT_MINUTES", 5)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)


settings = Settings()
