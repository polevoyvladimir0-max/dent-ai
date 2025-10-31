import os
from dataclasses import dataclass


@dataclass(slots=True)
class BotConfig:
    token: str
    api_base_url: str = "http://127.0.0.1:8000"

    @classmethod
    def from_env(cls) -> "BotConfig":
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        api_base = os.getenv("PRICING_API_BASE", cls.api_base_url)
        return cls(token=token, api_base_url=api_base)
