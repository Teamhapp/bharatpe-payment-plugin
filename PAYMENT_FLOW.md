# Payment Flow — End-to-End Reference

This document describes how the UPI payment system works from the moment a
user sends `/pay` to the moment the bot confirms receipt. It also covers the
BharatPe credential lifecycle and every error state the plugin handles.

---

## Table of Contents

1. [High-Level Sequence](#1-high-level-sequence)
2. [Step-by-Step Breakdown](#2-step-by-step-breakdown)
3. [Micro-Amount Collision Avoidance](#3-micro-amount-collision-avoidance)
4. [QR Code Generation](#4-qr-code-generation)
5. [Payment Verification Loop](#5-payment-verification-loop)
6. [Payment Status Reference](#6-payment-status-reference)
7. [BharatPe API Reference](#7-bharatpe-api-reference)
8. [Credential Expiry Lifecycle](#8-credential-expiry-lifecycle)
9. [Admin Panel Reference](#9-admin-panel-reference)
10. [Database Schema](#10-database-schema)

---

## 1. High-Level Sequence

```
User                    Bot                     BharatPe API          PostgreSQL
 │                       │                            │                    │
 │──/pay 500─────────────▶│                            │                    │
 │                       │──check rate limit──────────────────────────────▶│
 │                       │◀──ok───────────────────────────────────────────│
 │                       │──find free amount──────────────────────────────▶│
 │                       │◀──₹500.03 is free──────────────────────────────│
 │                       │──insert_payment(PENDING)───────────────────────▶│
 │◀──[QR image ₹500.03]──│                            │                    │
 │                       │                            │                    │
 │  [user scans & pays]  │                            │                    │
 │                       │                            │                    │
 │                       │──poll every 4s────────────▶│                    │
 │                       │◀──no match────────────────│                    │
 │                       │──poll every 4s────────────▶│                    │
 │                       │◀──MATCH ₹500.03 UTR:42x───│                    │
 │                       │──complete_payment(SUCCESS)─────────────────────▶│
 │◀──✅ Payment Verified──│                            │                    │
 │                       │                            │                    │
```

---

## 2. Step-by-Step Breakdown

### Step 1 — User invokes `/pay`

The user either:
- Sends `/pay 500` (amount inline), or
- Sends `/pay` and selects from the amount picker keyboard, or
- Taps **✏️ Custom** and types any amount

The bot runs three pre-checks before proceeding:

| Check | What it does |
|-------|-------------|
| **Blocked?** | Looks up `users.is_blocked` — blocked users get an error and cannot pay |
| **Rate limit (hourly)** | Counts payments in the last 60 minutes — rejects if ≥ `max_per_hour` |
| **Rate limit (concurrent)** | Counts active PENDING sessions — rejects if ≥ `max_concurrent` |

### Step 2 — Assign a unique session amount

The bot scans the database for a free slot starting from the requested amount:

```
₹500.00 → in use by another session → try next
₹500.01 → in use → try next
₹500.02 → in use → try next
₹500.03 → free  → assign this amount
```

See [Section 3](#3-micro-amount-collision-avoidance) for full details.

### Step 3 — Generate and send QR code

The bot:
1. Builds a UPI deep link: `upi://pay?pa=<upi_id>&am=500.03&pn=<merchant>&tn=<order_id>`
2. Renders it as a branded QR card (dark header with merchant name + amount, QR in the centre)
3. Sends the image to Telegram with a caption showing the exact amount and timeout

See [Section 4](#4-qr-code-generation) for QR format details.

### Step 4 — Insert payment record

```
payments.insert(
  order_id        = "TG17432187903050",
  user_id         = 123456789,      ← Telegram user ID (not chat ID)
  base_amount     = 500.00,
  session_amount  = 500.03,
  status          = "PENDING",
  expire_at       = NOW() + 5min,
)
```

### Step 5 — Poll loop

Every `poll_interval` seconds (default: 4s), the bot fetches recent QR
transactions from the BharatPe API and looks for a match:

- **Amount match**: `|tx_amount - session_amount| < ₹0.001`
- **Time window**: `payment_start ≤ tx_timestamp ≤ expire_at`
- **Type + status**: `type = PAYMENT_RECV` and `status = SUCCESS`

### Step 6a — Success path

When a match is found:
1. `payments` row updated to `SUCCESS`, UTR and payer VPA stored
2. `users.total_paid += amount`, `users.payment_count += 1`
3. QR caption edited to "✅ Payment Verified"
4. Success message sent with amount, UTR, payer name, and order ID
5. Result keyboard shown (Pay Again / 🏠 Menu)

### Step 6b — Expiry path

If `timeout` seconds pass with no match:
1. `payments` row updated to `FAILURE`
2. `users.failed_count += 1`
3. QR caption edited to "❌ Expired"
4. Expiry message sent with order ID

---

## 3. Micro-Amount Collision Avoidance

**The problem:** If two users both try to pay ₹500 at the same time and both
pay exactly ₹500, the bot cannot tell which payment belongs to which user.

**The solution:** Each session gets a unique amount by adding ₹0.01 increments:

```python
for i in range(100):
    candidate = round(base + 0.01 * i, 2)   # 500.00, 500.01, 500.02, ...
    if not is_amount_in_use(candidate):
        return candidate                      # first free slot wins
return None                                   # all 100 slots in use
```

`is_amount_in_use(amount)` checks the database for any PENDING payment with
that exact session_amount whose `expire_at` is in the future.

**What the user sees:** The QR says ₹500.03 and the caption says
"⚠️ Pay exactly ₹500.03". The user pays the shown amount. The extra ₹0.03
goes to the merchant — there is no way to avoid this.

**Slot capacity:** Up to 100 simultaneous payments per base amount (₹500.00
through ₹500.99). If all 100 are in use, the user gets a "Too many concurrent
payments" error.

**Cross-bot safety:** If you run multiple bots on the same merchant account,
they **must** share the same PostgreSQL database. The `is_amount_in_use` check
is the global lock that prevents two bots from assigning ₹500.03 to different
users simultaneously.

---

## 4. QR Code Generation

The QR image is a branded PNG card built by `qr_generator.py`:

```
┌─────────────────────────────────┐
│  My Store              ₹500.03  │  ← dark header, merchant name + amount
├─────────────────────────────────┤
│                                 │
│         [QR CODE]               │  ← UPI deep link, error correction H
│                                 │
├─────────────────────────────────┤
│  Order: TG174...    Scan UPI    │  ← light footer
└─────────────────────────────────┘
```

The UPI deep link embedded in the QR:

```
upi://pay?pa=yourname@yesbankltd&am=500.03&pn=My%20Store&tn=TG17432187903050
```

| Parameter | Value |
|-----------|-------|
| `pa` | Payee UPI address (`upi_id` in config) |
| `am` | Exact session amount |
| `pn` | Merchant display name (`merchant_name` in config) |
| `tn` | Order ID (shown as transaction note in payer's UPI app) |

All major UPI apps (GPay, PhonePe, Paytm, BHIM, Amazon Pay) parse this URI.

---

## 5. Payment Verification Loop

```python
elapsed = 0
while elapsed < cfg.timeout:               # default 5 minutes
    await asyncio.sleep(cfg.poll_interval) # default 4 seconds
    elapsed += cfg.poll_interval

    try:
        match = find_payment(session_amount, created_at, expire_at, cfg)
    except CredentialsExpiredError:
        await _alert_admins_credentials_expired(bot)
        continue                            # keep polling — do not abort
    except Exception:
        continue                            # network error — keep polling

    if match:
        # → Success path
```

The loop runs inside an `asyncio.Task` so it is fully non-blocking. Multiple
users can have active payment sessions simultaneously.

**CredentialsExpiredError behaviour:** The loop continues polling even after a
credential error. If an admin renews credentials while a payment is in progress,
the very next poll will succeed. The error alert is sent only once per bot
session (guarded by a `[False]` flag) to avoid flooding admins.

---

## 6. Payment Status Reference

| Status | Meaning | DB value |
|--------|---------|----------|
| Pending | QR sent, waiting for payment | `PENDING` |
| Success | Payment matched and verified | `SUCCESS` |
| Failed / Expired | Timeout or cancelled | `FAILURE` |

### Error messages shown to users

| Condition | Message |
|-----------|---------|
| Amount below `min_amount` | ❌ Minimum ₹{min_amount} |
| Amount above `max_amount` | ❌ Maximum ₹{max_amount:,} |
| User is blocked | 🚫 Your account is blocked. Contact admin. |
| Hourly limit reached | ⚠️ Rate limit: max {max_per_hour} payments per hour. |
| Concurrent limit reached | ⚠️ You have {n} active payment(s). Complete or wait for them to expire. |
| All 100 micro-amount slots in use | ⚠️ Too many concurrent payments. Wait a moment. |
| QR expired (timeout with no payment) | ❌ Payment Expired — No payment received in {timeout // 60} min. |

---

## 7. BharatPe API Reference

BharatPe does not publish a public merchant API. The plugin uses the same
private REST endpoint that the BharatPe merchant dashboard uses in the browser.

### Endpoint

```
GET https://payments-tesseract.bharatpe.in/api/v1/merchant/transactions
```

### Query parameters

| Parameter | Value |
|-----------|-------|
| `module` | `PAYMENT_QR` |
| `merchantId` | Your merchant ID |
| `sDate` | Start date (`YYYY-MM-DD`, 2 days ago in IST) |
| `eDate` | End date (`YYYY-MM-DD`, tomorrow in IST) |

> **Why IST?** BharatPe stores timestamps in Indian Standard Time (UTC+5:30).
> Using UTC would cause late-night payments (after 18:30 UTC = midnight IST)
> to fall outside the query window. All dates are computed in IST and `eDate`
> is extended by one extra day as a safety buffer.

### Authentication headers

| Header | Value |
|--------|-------|
| `token` | BharatPe session token |
| `Cookie` | Full browser cookie string |
| `User-Agent` | A real browser UA string |

### Extracting credentials from DevTools

1. Log in to [dashboard.bharatpe.com](https://dashboard.bharatpe.com)
2. Open **DevTools** (F12 on Windows/Linux — Cmd+Opt+I on Mac)
3. Go to the **Network** tab
4. Reload the page (Ctrl+R / Cmd+R)
5. In the filter box type: `payments-tesseract`
6. Click any request that appears in the list
7. Scroll to the **Request Headers** section on the right panel
8. Copy the value of the **`token`** header → this is your `api_token`
9. Copy the full value of the **`Cookie`** header → this is your `api_cookie`
10. Your `merchant_id` is visible in the request URL after `merchantId=`

> **Tip:** Click a request that shows `"message": "SUCCESS"` in its Response
> body to confirm the credentials are still active before copying them.

### Success response shape

```json
{
  "status": true,
  "message": "SUCCESS",
  "data": {
    "transactions": [
      {
        "type":               "PAYMENT_RECV",
        "status":             "SUCCESS",
        "amount":             500.03,
        "paymentTimestamp":   1743200000000,
        "bankReferenceNo":    "426123456789",
        "payerVpa":           "user@okaxis",
        "payerName":          "Rahul Sharma",
        "payerHandle":        "okaxis"
      }
    ]
  }
}
```

### Credential error response

```json
{ "status": false, "message": "UNAUTHORIZED" }
```

The plugin raises `CredentialsExpiredError` on messages `UNAUTHORIZED`,
`UNAUTHENTICATED`, `TOKEN_EXPIRED`, `SESSION_EXPIRED`, or HTTP 401/403.

---

## 8. Credential Expiry Lifecycle

BharatPe session tokens typically expire every few days. Here is the complete
lifecycle when that happens and how the bot recovers:

```
Token expires mid-operation
         │
         ▼
BharatPe returns UNAUTHORIZED
         │
         ▼
Plugin raises CredentialsExpiredError
         │
         ▼
Bot sends ONE alert to all admin_ids:
  "⚠️ BharatPe Credentials Expired
   ...send /renewcredentials"
         │
         ▼
Admin sends /renewcredentials
         │
         ▼
Bot: "Step 1 of 2 — paste new API token"
         │
Admin pastes token
         ▼
Bot: "Step 2 of 2 — paste Cookie header"
         │
Admin pastes cookie
         ▼
Bot validates against live API
         │
    ┌────┴────────────────────────────────┐
    ▼                                     ▼
  API → ok                           API → expired / unknown
  Credentials applied in memory      Credentials applied in memory only
  Credentials saved to DB            NOT saved (bad creds won't survive
  ✅ "Credentials updated"           a restart)
                                     ⚠️ "Check values / retry when back"
```

### Renewal outcome table

| API response after renewal | Applied in memory | Saved to DB | Admin message |
|---------------------------|-------------------|-------------|---------------|
| `ok` — API accepts new creds | ✅ Yes | ✅ Yes | ✅ Credentials updated successfully |
| `expired` — API still rejects | ✅ Yes | ❌ No | ⚠️ Still rejected — check token & cookie |
| `unknown` — network error | ✅ Yes | ❌ No | 🟡 API unreachable — run /renewcredentials again when back |

---

## 9. Admin Panel Reference

Send `/admin` to open the panel (restricted to users listed in `admin_ids`).

### Dashboard view

| Metric | Source |
|--------|--------|
| BharatPe API status | Live call to BharatPe API at view time |
| Today's revenue | `SUM(session_amount)` WHERE `status='SUCCESS'` AND today |
| All-time revenue | `SUM(session_amount)` WHERE `status='SUCCESS'` |
| Success / Failed / Pending counts | `payments` table counts |
| Total members | `COUNT(users)` |
| Active (24h) | Users with `last_seen > NOW() - 24h` |
| Blocked users | `COUNT(users)` WHERE `is_blocked = TRUE` |

### All admin actions

| Button / Command | What it does |
|-----------------|-------------|
| 📊 Dashboard | Live stats and BharatPe API health |
| 💰 Recent | Last 10 payments — status, amount, user, time, UTR |
| 👥 Members | Top 15 users by total paid, with block indicator |
| 🔍 Search | Find a specific payment by Order ID or UTR |
| 🚫 Block | Block a user by their Telegram chat ID |
| ✅ Unblock | Re-enable a previously blocked user |
| 📢 Broadcast | Send a message to every registered user |
| `/renewcredentials` | 2-step interactive flow to update BharatPe credentials |
| `/cancel` | Abort any pending admin input (search, block, broadcast, renewal) |

---

## 10. Database Schema

All tables are auto-created by `init_db(db_url)` on first startup. Migrations
are idempotent — safe to call on every restart.

### `users`

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | Auto-increment |
| `chat_id` | BIGINT UNIQUE | Telegram user/chat ID |
| `username` | VARCHAR(128) | Telegram @handle (nullable) |
| `first_name` | VARCHAR(128) | Telegram first name (nullable) |
| `is_blocked` | BOOLEAN | Default FALSE |
| `is_verified` | BOOLEAN | Reserved for future use |
| `total_paid` | NUMERIC(14,2) | Lifetime successful payments in ₹ |
| `payment_count` | INT | Successful payment count |
| `failed_count` | INT | Failed/expired payment count |
| `first_seen` | TIMESTAMP | First interaction |
| `last_seen` | TIMESTAMP | Most recent interaction |

### `payments`

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | Auto-increment |
| `order_id` | VARCHAR(64) UNIQUE | `TG<unix_timestamp><amount_paise>` |
| `user_id` | BIGINT | Telegram user ID — **no FK** (portable) |
| `base_amount` | NUMERIC(12,2) | Amount the user requested |
| `session_amount` | NUMERIC(12,2) | Unique micro-amount actually charged |
| `status` | VARCHAR(16) | `PENDING` → `SUCCESS` or `FAILURE` |
| `utr` | VARCHAR(64) | Bank reference number (populated on success) |
| `payer_vpa` | VARCHAR(128) | Payer UPI address (populated on success) |
| `created_at` | TIMESTAMP | QR generation time |
| `expire_at` | TIMESTAMP | Session expiry time |
| `completed_at` | TIMESTAMP | Payment verification time |
| `message_id` | BIGINT | Telegram message ID of the QR image |

> `payments.user_id` has **no foreign key** to `users`. This keeps the plugin
> portable — if your bot uses a different user table schema, payment records
> still work. Pass `update.effective_user.id` (never `message.chat_id`).

### `activity_log`

Append-only audit trail.

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | Auto-increment |
| `user_id` | BIGINT | Telegram user ID |
| `action` | VARCHAR(64) | Event type (see below) |
| `detail` | TEXT | Free-form detail string |
| `created_at` | TIMESTAMP | Event timestamp |

**Action values:** `payment_start` · `payment_success` · `payment_expired` ·
`cancel` · `block_user` · `unblock_user` · `broadcast` · `renew_credentials`

### `bot_config`

Key-value store for persisted runtime settings.

| Column | Type | Notes |
|--------|------|-------|
| `merchant_id` | VARCHAR(64) PK | BharatPe merchant ID |
| `key` | VARCHAR(64) PK | Setting name (`api_token`, `api_cookie`) |
| `value` | TEXT | Setting value |
| `updated_at` | TIMESTAMP | Last updated |

---

*This document reflects the plugin as of v0.1.0.*
