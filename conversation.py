"""Обратная совместимость для существующих импортов проекта."""
from conversation_engine import (  # noqa: F401
    MAX_MESSAGES,
    MEMORY_SECONDS,
    activity,
    cleanup,
    consecutive_addresses,
    detect_category,
    display_names,
    is_dialog_continuation,
    last_category,
    last_explicit_address_at,
    memory,
    register_activity,
    remember,
    reply_for,
    should_respond,
)
