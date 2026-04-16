"""Pre-check middleware bundled with the payment plugin."""

import logging
from telegram import Update
from database import upsert_user, is_blocked, user_active_count, user_hourly_count

log = logging.getLogger(__name__)


async def track_user(update: Update):
    """Record (or refresh) the user in the database on every interaction."""
    u = update.effective_user
    if u:
        upsert_user(u.id, u.username, u.first_name)


async def check_blocked(update: Update) -> bool:
    """Return True and send an error message if the user is blocked."""
    u = update.effective_user
    if u and is_blocked(u.id):
        msg = update.message or update.callback_query.message
        await msg.reply_text("🚫 Your account is blocked. Contact admin.")
        return True
    return False


async def check_rate_limit(user_id: int, max_per_hour: int, max_concurrent: int) -> str | None:
    """Return an error string if the user is rate-limited, or None if OK.

    Args:
        user_id:        Telegram user ID to check limits against.
        max_per_hour:   Maximum payments allowed per user per hour.
        max_concurrent: Maximum simultaneous PENDING payments per user.
    """
    active = user_active_count(user_id)
    if active >= max_concurrent:
        return f"⚠️ You have {active} active payment(s). Complete or wait for them to expire."

    hourly = user_hourly_count(user_id)
    if hourly >= max_per_hour:
        return f"⚠️ Rate limit: max {max_per_hour} payments per hour."

    return None
