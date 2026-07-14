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
    mode = random.random()
    if mode < 0.45:
        return
    if mode < 0.8:
        await asyncio.sleep(random.uniform(2, 5))
        return

    thinking_message = None
    if message is not None:
        try:
            thinking_message = await message.answer("🤔 Думаю...")
        except Exception:
            thinking_message = None
    await asyncio.sleep(random.uniform(2, 5))
    if thinking_message is not None:
        try:
            await thinking_message.delete()
        except Exception:
            pass


def chunk_buttons(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index:index + size] for index in range(0, len(items), size)]
