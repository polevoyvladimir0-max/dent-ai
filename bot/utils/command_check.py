"""
Утилита для проверки команд в обработчиках FSM состояний.
Обеспечивает приоритет команд над состояниями FSM.
"""

from typing import Optional, Callable, Awaitable
from aiogram.types import Message
from aiogram.fsm.context import FSMContext


async def check_commands_and_redirect(
    message: Message,
    state: FSMContext,
    command_handlers: dict[str, Callable[[Message, FSMContext], Awaitable[None]]],
) -> bool:
    """
    Проверяет, является ли сообщение командой, и перенаправляет на соответствующий обработчик.
    
    Args:
        message: Сообщение от пользователя
        state: FSM контекст
        command_handlers: Словарь {команда: обработчик}
    
    Returns:
        True, если команда была обработана, False иначе
    """
    if not message.text:
        return False
    
    text_lower = message.text.strip().lower()
    
    # Проверяем команды вида /command
    if text_lower in command_handlers:
        await command_handlers[text_lower](message, state)
        return True
    
    # Проверяем текстовые команды
    for cmd, handler in command_handlers.items():
        if cmd.startswith("/"):
            continue  # Уже проверили выше
        if text_lower == cmd.lower():
            await handler(message, state)
            return True
    
    return False

