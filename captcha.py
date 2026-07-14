"""Compatibility layer for old imports.

The join/captcha flow lives in join_manager.py. This module intentionally keeps
only safe aliases and does not store passed users.
"""

from join_manager import CAPTCHA_TIMEOUT_SECONDS, QUESTIONS, is_pending, make_question


__all__ = ["CAPTCHA_TIMEOUT_SECONDS", "QUESTIONS", "is_pending", "make_question"]
