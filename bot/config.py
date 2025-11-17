import os
from dataclasses import dataclass


@dataclass(slots=True)
class BotConfig:
    token: str
    api_base_url: str = "http://127.0.0.1:8000"
    yandex_gpt_api_key: str | None = None
    yandex_gpt_folder_id: str | None = None
    yandex_gpt_model: str = "yandexgpt-lite"
    yandex_gpt_timeout: float = 6.0

    @classmethod
    def from_env(cls) -> "BotConfig":
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        api_base = os.getenv("PRICING_API_BASE", cls.api_base_url)
        yandex_api_key = os.getenv("YANDEX_GPT_API_KEY")
        yandex_folder_id = os.getenv("YANDEX_GPT_FOLDER_ID")
        yandex_model = os.getenv("YANDEX_GPT_MODEL", cls.yandex_gpt_model)
        try:
            yandex_timeout = float(os.getenv("YANDEX_GPT_TIMEOUT", str(cls.yandex_gpt_timeout)))
        except ValueError:
            yandex_timeout = cls.yandex_gpt_timeout
        return cls(
            token=token,
            api_base_url=api_base,
            yandex_gpt_api_key=yandex_api_key,
            yandex_gpt_folder_id=yandex_folder_id,
            yandex_gpt_model=yandex_model,
            yandex_gpt_timeout=yandex_timeout,
        )
