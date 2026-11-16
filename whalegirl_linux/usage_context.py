"""Task-local numeric usage attribution; no prompt or message content."""
from contextvars import ContextVar
from functools import wraps
import hashlib

CURRENT_USAGE = ContextVar('whalegirl_usage_context', default={})


def set_usage_context(account, chat, decision):
    CURRENT_USAGE.set({'account': account, 'chat': chat,
                       'decision_id': hashlib.sha256(str(decision).encode()).hexdigest()[:24]})


def scoped_usage(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        token = CURRENT_USAGE.set({})
        try:
            return await function(*args, **kwargs)
        finally:
            CURRENT_USAGE.reset(token)
    return wrapped
