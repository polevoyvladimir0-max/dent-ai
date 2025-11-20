"""
Утилиты для работы с голосовыми сообщениями в Telegram боте.
Включает скачивание и транскрипцию через Whisper.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from aiogram import Bot
from aiogram.types import Message, Voice

from faster_whisper import WhisperModel


# Глобальный экземпляр модели Whisper (lazy loading)
_whisper_model: Optional[WhisperModel] = None


def get_whisper_model() -> WhisperModel:
    """
    Получает или создаёт экземпляр модели Whisper для транскрипции.
    Использует модель "base" для баланса скорости и качества.
    """
    global _whisper_model
    if _whisper_model is None:
        # Используем модель base для баланса скорости и качества
        # Модели: tiny, base, small, medium, large
        model_name = os.getenv("WHISPER_MODEL", "base")
        device = os.getenv("WHISPER_DEVICE", "cpu")  # cpu или cuda
        compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")  # int8, float16, float32
        
        logging.info(f"Loading Whisper model: {model_name} on {device} with {compute_type}")
        try:
            _whisper_model = WhisperModel(
                model_name,
                device=device,
                compute_type=compute_type,
            )
            logging.info("Whisper model loaded successfully")
        except Exception as e:
            logging.error(f"Failed to load Whisper model: {e}")
            raise
    
    return _whisper_model


async def download_voice(bot: Bot, message: Message) -> Path:
    """
    Скачивает голосовое сообщение из Telegram и возвращает путь к файлу.
    
    Args:
        bot: Экземпляр бота
        message: Сообщение с голосовым файлом
    
    Returns:
        Path к скачанному файлу (обычно .oga формат)
    
    Raises:
        ValueError: Если сообщение не содержит голосового файла
        Exception: При ошибках скачивания
    """
    if not message.voice:
        raise ValueError("Message does not contain a voice file")
    
    voice: Voice = message.voice
    
    # Получаем информацию о файле
    file_info = await bot.get_file(voice.file_id)
    file_path = file_info.file_path
    
    # Создаём временную директорию для хранения голосовых файлов
    temp_dir = Path(tempfile.gettempdir()) / "dent_ai_voices"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Создаём путь для сохранения файла
    # Используем file_id как имя файла для уникальности
    local_file_path = temp_dir / f"{voice.file_id}.oga"
    
    # Скачиваем файл
    logging.info(f"Downloading voice file {file_path} to {local_file_path}")
    try:
        await bot.download_file(file_path, destination=local_file_path)
        logging.info(f"Voice file downloaded: {local_file_path}")
    except Exception as e:
        logging.error(f"Failed to download voice file: {e}")
        raise
    
    return local_file_path


async def transcribe_voice(file_path: Path, language: Optional[str] = "ru") -> str:
    """
    Транскрибирует голосовой файл в текст с помощью Whisper.
    
    Args:
        file_path: Путь к голосовому файлу
        language: Код языка (по умолчанию "ru" для русского)
    
    Returns:
        Транскрибированный текст
    
    Raises:
        FileNotFoundError: Если файл не найден
        Exception: При ошибках транскрипции
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Voice file not found: {file_path}")
    
    try:
        model = get_whisper_model()
        
        logging.info(f"Transcribing voice file: {file_path}")
        
        # Транскрибируем файл
        # Используем asyncio для неблокирующего выполнения
        loop = asyncio.get_event_loop()
        
        def _transcribe():
            segments, info = model.transcribe(
                str(file_path),
                language=language,
                beam_size=5,  # Улучшает качество распознавания
                vad_filter=True,  # Фильтрация голосовой активности
            )
            
            # Собираем весь текст из сегментов
            text_parts = []
            for segment in segments:
                text_parts.append(segment.text.strip())
            
            return " ".join(text_parts)
        
        # Выполняем транскрипцию в executor, чтобы не блокировать event loop
        text = await loop.run_in_executor(None, _transcribe)
        
        # Очищаем файл после транскрипции (опционально)
        # Можно оставить для отладки или удалить для экономии места
        # file_path.unlink()
        
        logging.info(f"Transcription completed: {text[:50]}...")
        return text.strip()
    
    except Exception as e:
        logging.error(f"Transcription failed for {file_path}: {e}")
        raise


async def cleanup_old_voice_files(max_age_hours: int = 24) -> None:
    """
    Удаляет старые голосовые файлы из временной директории.
    
    Args:
        max_age_hours: Максимальный возраст файла в часах перед удалением
    """
    import time
    
    temp_dir = Path(tempfile.gettempdir()) / "dent_ai_voices"
    if not temp_dir.exists():
        return
    
    current_time = time.time()
    max_age_seconds = max_age_hours * 3600
    
    deleted_count = 0
    for file_path in temp_dir.glob("*.oga"):
        try:
            file_age = current_time - file_path.stat().st_mtime
            if file_age > max_age_seconds:
                file_path.unlink()
                deleted_count += 1
        except Exception as e:
            logging.warning(f"Failed to delete old voice file {file_path}: {e}")
    
    if deleted_count > 0:
        logging.info(f"Cleaned up {deleted_count} old voice files")

