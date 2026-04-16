"""
Minimal bot example — bharatpe-payment-plugin
==============================================

Copy this file into your project, fill in the six required environment variables
below, and run it.  The bot will accept UPI payments via /pay and give
admins a full dashboard via /admin.

Requirements:
    pip install python-telegram-bot psycopg2-binary requests pillow qrcode[pil]

Files needed alongside this script (copy from the plugin repo root):
    payment_plugin/     bharatpe.py     qr_generator.py     database.py
"""

import os
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from payment_plugin import PaymentConfig, register_payment_handlers, register_admin_handlers
from database import init_db

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s — %(message)s",
    level=logging.INFO,
)

# ── Configuration — set these as environment variables (never hard-code secrets) ──

cfg = PaymentConfig(
    upi_id       = os.environ["UPI_ID"],          # e.g. "yourname@yesbankltd"
    merchant_name= os.environ["MERCHANT_NAME"],   # displayed on QR card
    merchant_id  = os.environ["MERCHANT_ID"],     # BharatPe merchant ID
    api_token    = os.environ["API_TOKEN"],        # from BharatPe DevTools (token header)
    api_cookie   = os.environ["API_COOKIE"],       # from BharatPe DevTools (Cookie header)
    db_url       = os.environ["DATABASE_URL"],     # PostgreSQL connection string

    # Optional — sensible defaults are already set, override if needed:
    admin_ids    = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()],
    timeout      = 300,   # seconds before QR expires (default: 5 min)
    poll_interval= 4,     # seconds between BharatPe API checks
    min_amount   = 1.0,
    max_amount   = 50_000.0,
    max_per_hour = 10,
    max_concurrent = 3,
)


# ── /start handler (your own — the plugin does not add one) ──────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Pay Now", callback_data="pay:start")],
    ])
    await update.message.reply_text(
        f"👋 Welcome to *{cfg.merchant_name}*!\n\n"
        "Tap *Pay Now* or send `/pay <amount>` to make a UPI payment.",
        reply_markup=kb,
        parse_mode="Markdown",
    )


# ── nav:home handler (required — the plugin's post-payment keyboard emits this) ─

async def on_home(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle the 🏠 Menu button shown after every payment result."""
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Pay Now", callback_data="pay:start")],
    ])
    await q.message.reply_text(
        "Main menu — choose an option:",
        reply_markup=kb,
    )


# ── Wire everything together ─────────────────────────────────────────────────

def main():
    bot_token = os.environ["BOT_TOKEN"]

    # 1. Create tables / run idempotent migrations
    init_db(cfg.db_url)

    # 2. Build the Telegram application
    app = Application.builder().token(bot_token).build()

    # 3. Your own handlers
    app.add_handler(CommandHandler("start", cmd_start))

    # 4. Plugin handlers — payment flow (/pay command + QR + polling)
    register_payment_handlers(app, cfg)

    # 5. Plugin handlers — admin panel (/admin + /renewcredentials + /cancel)
    #    Remove this line if you don't want admin features.
    register_admin_handlers(app, cfg)

    # 6. nav:home must be registered AFTER the plugin handlers
    app.add_handler(CallbackQueryHandler(on_home, pattern=r"^nav:home$"))

    print(f"Bot starting — merchant: {cfg.merchant_name} ({cfg.merchant_id})")
    app.run_polling()


if __name__ == "__main__":
    main()
