# Integrating UPI Payments into Any Telegram Bot

This guide shows how to add the Pay0 UPI payment system to an existing
`python-telegram-bot` project. The payment system works as a self-contained
plugin: one config object and two function calls are all you need.

## Related Documentation

| Document | Purpose |
|----------|---------|
| [`README.md`](README.md) | Setup and configuration for the Pay0 bot itself |
| [`PAYMENT_FLOW.md`](PAYMENT_FLOW.md) | How the payment system works end-to-end |
| `INTEGRATION.md` | This file — how to plug it into your own bot |

---

## What Files You Need

Copy these files/folders into your project:

```
payment_plugin/       ← self-contained plugin package
  __init__.py
  config.py
  payment.py
  admin.py
  keyboards.py
  middleware.py
bharatpe.py           ← BharatPe API client
qr_generator.py       ← branded QR code generator
database.py           ← PostgreSQL layer
```

Then install the dependencies:

```bash
pip install python-telegram-bot psycopg2-binary requests pillow qrcode[pil]
```

---

## Prerequisites

1. **A BharatPe merchant account** — you need a Merchant ID, API token, and
   session cookie. See [PAYMENT_FLOW.md](PAYMENT_FLOW.md#7-bharatpe-api-reference)
   for how to extract these from browser DevTools.

2. **A PostgreSQL database** — the plugin creates its own tables on first run.

3. **python-telegram-bot v20+** — uses the async `Application` API.

4. **Shared database for shared merchant accounts** — if multiple bots use the
   same BharatPe merchant, they must share the same database. The `payments`
   table is the lock that prevents two bots assigning the same micro-amount.

---

## Quick Start (10 lines)

```python
from telegram.ext import Application
from payment_plugin import PaymentConfig, register_payment_handlers, register_admin_handlers
from database import init_db

cfg = PaymentConfig(
    upi_id="yourname@bank",
    merchant_name="My Store",
    merchant_id="12345678",
    api_token="<token from BharatPe DevTools>",
    api_cookie="<Cookie header from BharatPe DevTools>",
    db_url="postgresql://user:pass@localhost/mybot",
    admin_ids=[123456789],
)

app = Application.builder().token("YOUR_BOT_TOKEN").build()

init_db(cfg.db_url)                   # creates tables, runs migrations
register_payment_handlers(app, cfg)   # adds /pay command + QR flow
register_admin_handlers(app, cfg)     # optional: /admin dashboard + /renewcredentials

# ... add your own handlers ...

app.run_polling()
```

That's it. Your bot now accepts UPI payments with auto-verification.

---

## What the Plugin Adds to Your Bot

### User-facing commands
| Command | Description |
|---------|-------------|
| `/pay <amount>` | Start a payment — shows amount picker or takes amount directly |

### Admin-only commands (requires `admin_ids` in config)
| Command | Description |
|---------|-------------|
| `/admin` | Open the admin dashboard (stats, members, recent payments) |
| `/renewcredentials` | Update BharatPe token and cookie without restarting the bot |
| `/cancel` | Abort any pending admin input flow |

### Payment flow (automatic)
1. User calls `/pay` → bot assigns a unique micro-amount → sends branded QR code
2. User scans QR and pays via any UPI app
3. Bot polls BharatPe every `poll_interval` seconds for up to `timeout` seconds
4. When a matching transaction is found → sends success message with UTR and payer info
5. If timeout → QR marked expired, amount released for reuse

---

## PaymentConfig Reference

```python
from payment_plugin import PaymentConfig

cfg = PaymentConfig(
    # Required
    upi_id="yourname@bank",         # merchant UPI address
    merchant_name="My Store",       # shown on QR card and in payment notes
    merchant_id="12345678",         # BharatPe merchant ID
    api_token="...",                # BharatPe session token (expires periodically)
    api_cookie="...",               # BharatPe session cookie (expires periodically)

    # Optional — defaults shown
    bharatpe_api="https://payments-tesseract.bharatpe.in/api/v1/merchant/transactions",
    user_agent="Mozilla/5.0 ...",   # browser user-agent for API calls

    db_url="postgresql://postgres:postgres@localhost:5432/pay0_bot",

    timeout=300,                    # seconds before QR expires (default: 5 min)
    poll_interval=4,                # seconds between BharatPe API polls
    min_amount=1.0,                 # minimum payment in ₹
    max_amount=50000.0,             # maximum payment in ₹

    max_per_hour=10,                # max payments per user per hour
    max_concurrent=3,               # max simultaneous pending payments per user

    admin_ids=[123456789],          # Telegram user IDs with admin access
)
```

---

## Database Tables

The plugin creates three tables automatically on `init_db(cfg.db_url)`.
Migrations are idempotent — running init_db again is always safe.

| Table | Purpose |
|-------|---------|
| `users` | One row per Telegram user — tracks `total_paid`, `payment_count`, block status. Uses `chat_id` as the primary identifier (Telegram-specific). |
| `payments` | One row per payment session — `order_id`, `user_id` (generic integer, no FK), `session_amount`, `status`, `utr` |
| `activity_log` | Append-only event log — `payment_start`, `payment_success`, `payment_expired` |

### Key design decisions

- **`payments.user_id`** is a plain `BIGINT` with **no foreign key constraint**.
  This keeps the plugin portable: if your bot's user table uses a different schema,
  the payment records still work. The value is whatever integer you pass as the
  Telegram user ID.

- **Migration is automatic**: if you're upgrading from an older version that used
  `chat_id` in the payments/activity_log tables, the migration script in
  `init_db()` automatically renames the column. No manual SQL needed.

---

## Handling Credential Expiry

BharatPe session credentials expire periodically. When they do:

1. The bot catches `CredentialsExpiredError` and sends a **one-time alert** to all `admin_ids`
2. An admin sends `/renewcredentials` to the bot
3. The bot prompts for the new token, then the cookie (2-step flow)
4. The bot validates them against the API and applies in-memory — **no restart needed**

To extract fresh credentials: log in to [dashboard.bharatpe.com](https://dashboard.bharatpe.com),
open DevTools → Network tab, reload the page, and copy the `token` request header and
full `Cookie` header from any request to `payments-tesseract.bharatpe.in`.

---

## Routing the nav:home Callback

The plugin's post-payment keyboards include a **🏠 Menu** button that emits the
`nav:home` callback. The plugin itself does **not** register a `nav:home` handler —
that is intentionally left to the host application so you can display whatever
"home" screen makes sense for your bot.

If you don't want the Menu button, you can replace `result_kb()` with your own
keyboard. If you do want it, add a handler in your bot:

```python
from telegram.ext import CallbackQueryHandler

async def on_home(update, ctx):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Choose an option:", reply_markup=your_home_kb())

app.add_handler(CallbackQueryHandler(on_home, pattern=r"^nav:home$"))
```

---

## Shared Merchant Account Warning

If you run **multiple bots** on the same BharatPe merchant account:

- They **must** share the same PostgreSQL database
- The `payments` table is the pool that prevents two bots assigning the same
  micro-amount (e.g. ₹100.03) simultaneously to different users
- If they use separate databases, both bots may assign ₹100.03 independently
  and whichever polls first will "steal" the payment from the other user

Simplest setup: one shared database, all bots call `init_db(same_db_url)` at startup.
