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
    
    async def adapt_plan(
        self,
        original_plan: str,
        original_codes: List[str],
        new_context: str,
        temperature: float = 0.4,
        max_tokens: int = 500,
    ) -> Optional[Dict[str, Any]]:
        """
        Адаптирует план лечения под новый контекст (бренды, локализация и т.д.).
        
        Args:
            original_plan: Исходный идеологический план
            original_codes: Исходная последовательность кодов
            new_context: Новый контекст (новая диктовка врача)
            temperature: Температура для генерации
            max_tokens: Максимальное количество токенов
        
        Returns:
            Dict с ключами:
            - adapted_plan: Адаптированный идеологический план
            - adapted_codes: Адаптированная последовательность кодов (может быть None, если не изменилась)
            - changes: Описание изменений
        """
        system_prompt = (
            "Ты ассистент стоматолога. Твоя задача - адаптировать план лечения под новый контекст. "
            "Меняй только параметры (бренды, номера зубов, локализацию), но сохраняй структуру плана и последовательность услуг. "
            "Отвечай строго в формате JSON: {\"adapted_plan\": \"текст\", \"adapted_codes\": [\"коды\"], \"changes\": \"что изменилось\"}"
        )
        
        user_prompt = (
            f"Исходный план: {original_plan}\n"
            f"Исходные коды: {', '.join(original_codes)}\n"
            f"Новый контекст: {new_context}\n\n"
            "Адаптируй план под новый контекст. Если коды не меняются, оставь adapted_codes пустым массивом."
        )
        
        messages = [
            {"role": "system", "text": system_prompt},
            {"role": "user", "text": user_prompt},
        ]
        
        response_text = await self.generate(messages, temperature=temperature, max_tokens=max_tokens)
        if not response_text:
            return None
        
        # Парсим JSON ответ
        try:
            import json
            # Убираем markdown code blocks если есть
            response_clean = response_text.strip()
            if response_clean.startswith("```"):
                response_clean = response_clean.split("```")[1]
                if response_clean.startswith("json"):
                    response_clean = response_clean[4:].strip()
            response_clean = response_clean.strip()
            
            result = json.loads(response_clean)
            
            # Валидация результата
            if not isinstance(result, dict):
                return None
            
            return {
                "adapted_plan": result.get("adapted_plan", new_context),
                "adapted_codes": result.get("adapted_codes") or original_codes,
                "changes": result.get("changes", "Параметры обновлены"),
            }
        except Exception as parse_exc:
            logging.warning(f"Failed to parse LLM adaptation response: {parse_exc}")
            # Fallback: возвращаем новый контекст без изменений кодов
            return {
                "adapted_plan": new_context,
                "adapted_codes": original_codes,
                "changes": "Не удалось адаптировать автоматически",
            }
    
    async def understand_intent(
        self,
        user_message: str,
        current_state: Optional[str] = None,
        state_data: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.5,
        max_tokens: int = 300,
    ) -> Optional[Dict[str, Any]]:
        """
        Понимает намерение пользователя из сообщения с учётом контекста (состояние FSM, история диалога).
        
        Args:
            user_message: Сообщение пользователя
            current_state: Текущее состояние FSM (например, "patient", "intake", "plan_codes")
            state_data: Данные текущего состояния (пациент, план, коды и т.д.)
            conversation_history: История диалога в формате [{"role": "user/assistant", "text": "..."}]
            temperature: Температура для генерации
            max_tokens: Максимальное количество токенов
        
        Returns:
            Dict с ключами:
            - intent: Намерение пользователя ("create_plan", "update_profile", "add_codes", "ask_question", "unclear")
            - action: Рекомендуемое действие ("continue_state", "redirect_to_profile", "redirect_to_intake", "answer_question")
            - explanation: Объяснение намерения
            - extracted_data: Извлечённые данные (пациент, коды, вопросы и т.д.)
        """
        # Формируем контекст для LLM
        context_parts = []
        
        if current_state:
            state_descriptions = {
                "patient": "Ожидается ввод имени пациента",
                "card_number": "Ожидается номер амбулаторной карты",
                "intake": "Ожидается описание плана лечения (диктовка)",
                "template_selection": "Ожидается выбор шаблона плана из истории",
                "plan_codes": "Ожидается ввод кодов услуг или описание услуг",
                "plan_disambiguation": "Ожидается выбор кодов из предложенных вариантов",
                "plan_confirm": "Ожидается подтверждение или правки плана",
                "doctor_name": "Ожидается ввод ФИО врача",
                "doctor_specialization": "Ожидается специализация врача",
            }
            context_parts.append(f"Текущее состояние: {state_descriptions.get(current_state, current_state)}")
        
        if state_data:
            relevant_data = {}
            if state_data.get("patient"):
                relevant_data["patient"] = state_data.get("patient")
            if state_data.get("intake"):
                relevant_data["plan_description"] = state_data.get("intake")[:100] + "..."
            if state_data.get("doctor"):
                relevant_data["doctor"] = state_data.get("doctor")
            if relevant_data:
                context_parts.append(f"Контекст сессии: {relevant_data}")
        
        context_text = "\n".join(context_parts) if context_parts else "Начало сессии"
        
        system_prompt = (
            "Ты умный ассистент стоматолога. Твоя задача - понять намерение пользователя по его сообщению "
            "с учётом текущего контекста (состояние диалога, введённые данные). "
            "Определи, что хочет сделать пользователь, и предложи действие. "
            "Отвечай строго в формате JSON: {\"intent\": \"намерение\", \"action\": \"действие\", "
            "\"explanation\": \"объяснение\", \"extracted_data\": {\"ключ\": \"значение\"}}. "
            "\n\nВозможные намерения:\n"
            "- \"create_plan\": Создать новый план лечения\n"
            "- \"update_profile\": Обновить профиль врача\n"
            "- \"add_codes\": Добавить коды услуг в план\n"
            "- \"ask_question\": Задать вопрос или получить помощь\n"
            "- \"continue_state\": Продолжить текущий шаг (например, ввести имя пациента)\n"
            "- \"unclear\": Непонятное намерение, нужны уточнения\n"
            "\nВозможные действия:\n"
            "- \"continue_state\": Продолжить в текущем состоянии\n"
            "- \"redirect_to_profile\": Перенаправить на обновление профиля\n"
            "- \"redirect_to_intake\": Перенаправить на ввод плана\n"
            "- \"redirect_to_codes\": Перенаправить на ввод кодов\n"
            "- \"answer_question\": Ответить на вопрос\n"
            "- \"ask_clarification\": Попросить уточнить намерение"
        )
        
        # Формируем историю диалога
        messages = [{"role": "system", "text": system_prompt}]
        
        if conversation_history:
            # Добавляем историю диалога (последние 5 сообщений для контекста)
            recent_history = conversation_history[-5:]
            for msg in recent_history:
                messages.append(msg)
        
        user_prompt = f"Контекст: {context_text}\n\nСообщение пользователя: {user_message}\n\nОпредели намерение и предложи действие."
        messages.append({"role": "user", "text": user_prompt})
        
        response_text = await self.generate(messages, temperature=temperature, max_tokens=max_tokens)
        if not response_text:
            return None
        
        try:
            import json
            import re
            
            # Убираем markdown code blocks если есть
            response_clean = response_text.strip()
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', response_clean, re.DOTALL)
            if json_match:
                response_clean = json_match.group(1)
            elif response_clean.startswith("```"):
                response_clean = response_clean.split("```")[1]
                if response_clean.startswith("json"):
                    response_clean = response_clean[4:].strip()
            
            # Ищем JSON объект в тексте
            json_match = re.search(r'\{.*\}', response_clean, re.DOTALL)
            if json_match:
                response_clean = json_match.group(0)
            
            result = json.loads(response_clean)
            
            # Валидация и нормализация результата
            if not isinstance(result, dict):
                return None
            
            intent = result.get("intent", "unclear")
            action = result.get("action", "ask_clarification")
            explanation = result.get("explanation", "Не удалось определить намерение")
            extracted_data = result.get("extracted_data", {})
            
            return {
                "intent": intent,
                "action": action,
                "explanation": explanation,
                "extracted_data": extracted_data,
            }
        except Exception as parse_exc:
            logging.warning(f"Failed to parse LLM intent response: {parse_exc}. Response: {response_text[:200]}")
            # Fallback: пробуем определить базовое намерение по ключевым словам
            message_lower = user_message.lower()
            if any(word in message_lower for word in ["профиль", "данные", "обновить"]):
                return {
                    "intent": "update_profile",
                    "action": "redirect_to_profile",
                    "explanation": "Пользователь хочет обновить профиль",
                    "extracted_data": {},
                }
            if any(word in message_lower for word in ["новый", "план", "создать"]):
                return {
                    "intent": "create_plan",
                    "action": "redirect_to_intake",
                    "explanation": "Пользователь хочет создать новый план",
                    "extracted_data": {},
                }
            return {
                "intent": "unclear",
                "action": "ask_clarification",
                "explanation": "Не удалось определить намерение",
                "extracted_data": {},
            }
