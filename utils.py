import asyncio
import random
from typing import Any


async def safe_delete_message(bot: Any, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as error:
        print("Ошибка удаления сообщения:", repr(error))


async def safe_send_message(bot: Any, chat_id: int, text: str, **kwargs: Any) -> Any | None:
    try:
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception as error:
        print("Ошибка отправки сообщения:", repr(error))
        return None


async def human_pause(message: Any | None = None) -> None:
    """Короткая пауза не даёт ответам выглядеть мгновенно-машинными,
    но и не заставляет игрока ждать. Фальшивое «Думаю…» больше не показываем.
    """
    await asyncio.sleep(random.uniform(0.15, 0.55))


def chunk_buttons(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index:index + size] for index in range(0, len(items), size)]
