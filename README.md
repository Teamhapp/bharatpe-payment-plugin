# bharatpe-payment-plugin

A drop-in UPI payment system for any [python-telegram-bot](https://python-telegram-bot.org/) project, powered by the [BharatPe](https://bharatpe.com/) merchant API.

> **Built for India.** Works with GPay, PhonePe, Paytm, BHIM, and all UPI apps.

---

## Features

- **One-command payment** — `/pay <amount>` generates a branded QR code in chat
- **Auto-verification** — polls BharatPe every 4 seconds, confirms the moment money arrives
- **Unique micro-amounts** — adds ₹0.01 increments to avoid collision when multiple users pay simultaneously
- **Admin dashboard** — recent payments, member list, search by UTR/order, block/unblock, broadcast
- **Live credential renewal** — `/renewcredentials` updates the BharatPe session without restarting the bot
- **Credential expiry alerts** — admins get a Telegram message the moment the API token expires
- **Rate limiting** — configurable per-user hourly and concurrent payment limits
- **PostgreSQL-backed** — auto-creates tables, runs idempotent migrations on startup

---

## Quick Start

### 1. Install

```bash
pip install bharatpe-payment-plugin
# or copy the payment_plugin/ folder directly into your project
```

### 2. Integrate

```python
from telegram.ext import Application
from payment_plugin import PaymentConfig, register_payment_handlers, register_admin_handlers
from database import init_db

cfg = PaymentConfig(
    upi_id="yourname@yesbankltd",
    merchant_name="My Store",
    merchant_id="12345678",
    api_token="<token from BharatPe DevTools>",
    api_cookie="<Cookie header from BharatPe DevTools>",
    db_url="postgresql://user:pass@localhost/mybot",
    admin_ids=[123456789],   # your Telegram user ID
)

app = Application.builder().token("YOUR_BOT_TOKEN").build()

init_db(cfg.db_url)
register_payment_handlers(app, cfg)
register_admin_handlers(app, cfg)   # optional — /admin, /renewcredentials

app.run_polling()
```

That's it. Your bot now accepts UPI payments.

---

## How It Works

```
User: /pay 500
Bot:  [sends QR for ₹500.03]     ← unique micro-amount avoids collisions
      [polls BharatPe every 4s]
User: [scans + pays ₹500.03 on GPay]
Bot:  ✅ Payment Verified! ₹500.03 — UTR: 426...
```

1. User sends `/pay <amount>` (or picks from the keyboard)
2. Bot assigns a unique session amount (₹500 + ₹0.01–₹0.99 to avoid collision)
3. Bot generates a branded QR code and sends it to the chat
4. Bot polls the BharatPe transaction API every 4 seconds
5. When a matching transaction appears → success message with UTR and payer info
6. If 5 minutes pass with no match → QR expires, amount is freed for reuse

---

## PaymentConfig Reference

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `upi_id` | Yes | — | Merchant UPI address (e.g. `name@yesbankltd`) |
| `merchant_name` | Yes | — | Display name on QR card |
| `merchant_id` | Yes | — | BharatPe merchant ID |
| `api_token` | Yes | — | BharatPe session token (from DevTools) |
| `api_cookie` | Yes | — | BharatPe Cookie header (from DevTools) |
| `db_url` | Yes | — | PostgreSQL connection string |
| `admin_ids` | No | `[]` | Telegram user IDs with admin access |
| `timeout` | No | `300` | Seconds before QR expires |
| `poll_interval` | No | `4` | Seconds between API checks |
| `min_amount` | No | `1.0` | Minimum payment in ₹ |
| `max_amount` | No | `50000.0` | Maximum payment in ₹ |
| `max_per_hour` | No | `10` | Max payments per user per hour |
| `max_concurrent` | No | `3` | Max simultaneous pending payments per user |

---

## Getting BharatPe Credentials

BharatPe does not offer a public developer API — credentials are browser session tokens:

1. Log in to [dashboard.bharatpe.com](https://dashboard.bharatpe.com)
2. Open **DevTools** → **Network** tab → reload the page
3. Click any request to `payments-tesseract.bharatpe.in`
4. Copy the `token` request header → `api_token`
5. Copy the full `Cookie` request header → `api_cookie`
6. Your `merchant_id` is in the URL of those requests

These credentials **expire every few days**. Use `/renewcredentials` in Telegram to refresh them without restarting.

---

## Bot Commands

### User commands
| Command | Description |
|---------|-------------|
| `/pay <amount>` | Start a payment (shows amount picker if no amount given) |

### Admin commands (requires `admin_ids`)
| Command | Description |
|---------|-------------|
| `/admin` | Open dashboard — stats, recent payments, members |
| `/renewcredentials` | Update BharatPe token + cookie without restart |
| `/cancel` | Abort any in-progress admin input flow |

---

## Database Tables

Auto-created by `init_db(db_url)`:

| Table | Columns |
|-------|---------|
| `users` | `chat_id`, `username`, `total_paid`, `payment_count`, `is_blocked`, ... |
| `payments` | `order_id`, `user_id` (no FK), `session_amount`, `status`, `utr`, ... |
| `activity_log` | `user_id`, `action`, `detail`, `created_at` |

Migrations are **idempotent** — safe to run on every startup.

---

## Multiple Merchants / Bots

Multiple bots using the same BharatPe merchant must share one database to avoid micro-amount collisions:

```python
# Bot A
cfg_a = PaymentConfig(merchant_id="123", db_url="postgresql://same_db", ...)

# Bot B
cfg_b = PaymentConfig(merchant_id="123", db_url="postgresql://same_db", ...)
```

Different merchants can use separate databases.

---

## nav:home Callback

The post-payment keyboard includes a **🏠 Menu** button that emits `nav:home`. The plugin does not handle this — add your own handler:

```python
from telegram.ext import CallbackQueryHandler

async def on_home(update, ctx):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Main menu:", reply_markup=your_main_kb())

app.add_handler(CallbackQueryHandler(on_home, pattern=r"^nav:home$"))
```

---

## Full Example Bot

See the [Pay0 bot](https://github.com/pay0bot/pay0) for a complete working bot built on this plugin, including `/start`, history, stats, and the admin panel.

---

## License

MIT — see [LICENSE](LICENSE).

Contributions welcome. Please open an issue before submitting large PRs.
