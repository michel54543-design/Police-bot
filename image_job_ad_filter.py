"""OCR-фильтр только для рекламных картинок с предложениями работы на дому."""

from __future__ import annotations

import asyncio
import io
import logging
import re

from typing import Any

logger = logging.getLogger("police.image_job_ad_filter")

# Проверяем только этот конкретный вид рекламы, без расширения на другие категории.
JOB_AD_PATTERNS = (
    re.compile(r"работ[аы]\s+(?:на\s+дому|из\s+дома)"),
    re.compile(r"(?:доход|заработок)[\s\S]{0,35}(?:в\s+день|день)"),
    re.compile(r"пиш(?:ите|и)[\s\S]{0,25}(?:менеджер|полин)"),
)


def normalize_ocr_text(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я+]+", " ", text)
    return " ".join(text.split())


def looks_like_job_ad_text(text: str) -> bool:
    """Определяет только рекламу работы на дому из примеров пользователя."""
    normalized = normalize_ocr_text(text)
    if not normalized:
        return False

    direct_phrase = any(pattern.search(normalized) for pattern in JOB_AD_PATTERNS)
    if direct_phrase:
        return True

    # OCR иногда искажает отдельные слова, поэтому используем сочетание признаков.
    has_job = any(word in normalized for word in ("работа", "работы", "заработок", "доход"))
    has_remote = any(phrase in normalized for phrase in ("на дому", "из дома"))
    has_daily = "в день" in normalized
    has_contact = "пишите" in normalized and any(word in normalized for word in ("менеджер", "полин"))
    has_adult = "18+" in normalized or "18 +" in normalized

    return (has_job and has_remote) or (has_job and has_daily and has_adult) or (has_job and has_contact)


def _prepare_variants(raw: bytes) -> list[Any]:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    image = Image.open(io.BytesIO(raw)).convert("RGB")
    max_side = 1800
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side))

    gray = ImageOps.grayscale(image)
    contrast = ImageEnhance.Contrast(gray).enhance(2.0)
    sharpened = contrast.filter(ImageFilter.SHARPEN)
    threshold = sharpened.point(lambda px: 255 if px > 155 else 0)
    return [image, sharpened, threshold]


def _recognize(raw: bytes) -> str:
    import pytesseract

    chunks: list[str] = []
    for image in _prepare_variants(raw):
        try:
            chunks.append(pytesseract.image_to_string(image, lang="rus+eng", config="--psm 6"))
        finally:
            image.close()
    return "\n".join(chunks)


async def image_contains_job_ad(bot: Any, message: Any) -> bool:
    """Скачивает крупнейшую фотографию, запускает OCR и проверяет нужные фразы."""
    if not message.photo:
        return False

    try:
        buffer = io.BytesIO()
        await bot.download(message.photo[-1], destination=buffer)
        raw = buffer.getvalue()
        if not raw:
            return False

        text = await asyncio.to_thread(_recognize, raw)
        matched = looks_like_job_ad_text(text)
        logger.info(
            "IMAGE_JOB_AD chat_id=%s user_id=%s matched=%s ocr=%r",
            getattr(getattr(message, "chat", None), "id", None),
            getattr(getattr(message, "from_user", None), "id", None),
            matched,
            " ".join(text.split())[:500],
        )
        return matched
    except Exception as error:
        # Ошибка OCR не должна останавливать весь бот.
        logger.exception("Не удалось проверить изображение через OCR: %r", error)
        return False
