"""Keyboard layouts bundled with the payment plugin."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def is_admin(user_id: int, admin_ids: list) -> bool:
    """Return True if user_id is in the admin list."""
    return user_id in admin_ids


def amounts_kb():
    """Amount picker shown when the user taps Pay Now."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("₹10", callback_data="pay:10"),
            InlineKeyboardButton("₹50", callback_data="pay:50"),
            InlineKeyboardButton("₹100", callback_data="pay:100"),
        ],
        [
            InlineKeyboardButton("₹500", callback_data="pay:500"),
            InlineKeyboardButton("₹1000", callback_data="pay:1000"),
            InlineKeyboardButton("₹2000", callback_data="pay:2000"),
        ],
        [InlineKeyboardButton("✏️ Custom", callback_data="pay:custom")],
    ])


def waiting_kb():
    """Cancel button shown while a QR is waiting for payment."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Payment", callback_data="pay:cancel")],
    ])


def result_kb():
    """Pay Again / Menu buttons shown after a payment completes or expires."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Pay Again", callback_data="pay:start")],
        [InlineKeyboardButton("🏠 Menu", callback_data="nav:home")],
    ])


def admin_kb():
    """Top-level admin panel keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Dashboard", callback_data="admin:dash")],
        [
            InlineKeyboardButton("💰 Recent", callback_data="admin:recent"),
            InlineKeyboardButton("👥 Members", callback_data="admin:users"),
        ],
        [InlineKeyboardButton("🔍 Search", callback_data="admin:search")],
        [
            InlineKeyboardButton("🚫 Block", callback_data="admin:block"),
            InlineKeyboardButton("✅ Unblock", callback_data="admin:unblock"),
        ],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin:broadcast")],
        [InlineKeyboardButton("🏠 Menu", callback_data="nav:home")],
    ])


def back_admin_kb():
    """Single ◀️ Back button returning to the admin panel."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back", callback_data="admin:panel")],
    ])
