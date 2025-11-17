from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import List, Dict, Any, Optional

import aiohttp


class YandexGPTClient:
    API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def __init__(
        self,
        api_key: str,
        folder_id: str,
        model: str = "yandexgpt-lite",
        timeout: float = 6.0,
    ) -> None:
        self.api_key = api_key
        self.folder_id = folder_id
        self.model = model or "yandexgpt-lite"
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None

    async def _session_safe(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout + 2)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session:
            with suppress(Exception):
                await self._session.close()

    async def generate(self, messages: List[Dict[str, Any]], temperature: float = 0.4, max_tokens: int = 220) -> Optional[str]:
        session = await self._session_safe()
        payload = {
            "modelUri": f"gpt://{self.folder_id}/{self.model}",
            "completionOptions": {
                "temperature": temperature,
                "maxTokens": max_tokens,
                "stream": False,
            },
            "messages": messages,
        }
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with session.post(self.API_URL, headers=headers, json=payload, timeout=self.timeout) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"YandexGPT HTTP {resp.status}: {text}")
                data = await resp.json()
        except asyncio.TimeoutError as exc:
            raise TimeoutError("YandexGPT request timed out") from exc

        try:
            alternatives = data["result"]["alternatives"]
            for alt in alternatives:
                message = alt.get("message") or {}
                text = message.get("text")
                if text:
                    return text.strip()
        except Exception as parse_err:
            logging.warning("Failed to parse YandexGPT response: %s", parse_err)
        return None

    async def smalltalk(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        messages = [
            {"role": "system", "text": system_prompt},
            {"role": "user", "text": user_prompt},
        ]
        return await self.generate(messages)
