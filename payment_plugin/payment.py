"""Payment handler — full /pay flow: amount picker, QR, polling, result."""

import asyncio
import time
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

from bharatpe import find_payment, CredentialsExpiredError
from qr_generator import make_qr
from database import (
    is_amount_in_use, insert_payment, complete_payment,
    fail_payment, expire_stale, log_activity,
)
from .config import PaymentConfig
from .keyboards import amounts_kb, waiting_kb, result_kb
from .middleware import track_user, check_blocked, check_rate_limit

log = logging.getLogger(__name__)


def register_payment_handlers(app, cfg: PaymentConfig):
    """Register all payment-related handlers, closed over cfg.

    Args:
        app: python-telegram-bot Application.
        cfg: PaymentConfig — all settings consumed by the payment flow.
    """

    _credentials_alert_sent = [False]

    async def _alert_admins_credentials_expired(bot) -> None:
        if _credentials_alert_sent[0]:
            return
        _credentials_alert_sent[0] = True
        text = (
            "⚠️ *BharatPe Credentials Expired*\n\n"
            "The bot can no longer verify payments because the BharatPe "
            "API token or session cookie has expired.\n\n"
            "*Fix:*\n"
            "1. Log in to dashboard.bharatpe.com\n"
            "2. Open DevTools → Network tab\n"
            "3. Copy the new `token` header and `Cookie` value\n"
            "4. Send /renewcredentials in Telegram (admin only)\n\n"
            "Payments will time out until credentials are refreshed."
        )
        for admin_id in cfg.admin_ids:
            try:
                await bot.send_message(admin_id, text, parse_mode="Markdown")
            except Exception as e:
                log.error(f"Failed to alert admin {admin_id}: {e}")
        log.error("BharatPe credentials expired — admin alert sent")

    class ActiveSession:
        __slots__ = ("order_id", "amount", "created_at", "expire_at", "status", "user_id")

        def __init__(self, order_id, amount, user_id):
            self.order_id = order_id
            self.amount = amount
            self.user_id = user_id
            self.created_at = datetime.now()
            self.expire_at = self.created_at + timedelta(seconds=cfg.timeout)
            self.status = "PENDING"

    def _make_order_id(amount: float) -> str:
        return f"TG{int(time.time())}{int(amount * 100):05d}"

    def _find_free_amount(base: float) -> float | None:
        for i in range(100):
            candidate = round(base + 0.01 * i, 2)
            if not is_amount_in_use(candidate):
                return candidate
        return None

    async def _start_payment(message, ctx: ContextTypes.DEFAULT_TYPE, amount: float, user_id: int):
        """Initiate a payment session for the given user.

        Args:
            message:  The Telegram Message to reply to (for QR photo + result text).
            ctx:      Handler context.
            amount:   Requested base amount in ₹.
            user_id:  Telegram user ID of the person initiating the payment.
                      Always pass update.effective_user.id or q.from_user.id —
                      never message.chat_id, which is the chat (group/channel/user).
        """
        if amount < cfg.min_amount:
            await message.reply_text(f"❌ Minimum ₹{cfg.min_amount:.0f}")
            return
        if amount > cfg.max_amount:
            await message.reply_text(f"❌ Maximum ₹{cfg.max_amount:,.0f}")
            return

        err = await check_rate_limit(user_id, cfg.max_per_hour, cfg.max_concurrent)
        if err:
            await message.reply_text(err)
            return

        expire_stale()

        session_amount = _find_free_amount(amount)
        if session_amount is None:
            await message.reply_text(
                "⚠️ Too many concurrent payments. Wait a moment.",
                reply_markup=result_kb(),
            )
            return

        order_id = _make_order_id(amount)
        expire_at = datetime.now() + timedelta(seconds=cfg.timeout)

        s = ActiveSession(order_id, session_amount, user_id)
        ctx.user_data["active_order"] = order_id

        log.info(f"NEW | {order_id} | ₹{session_amount} | user={user_id}")
        log_activity(user_id, "payment_start", f"{order_id} ₹{session_amount}")

        qr_buf = make_qr(session_amount, order_id, cfg)

        qr_msg = await message.reply_photo(
            photo=qr_buf,
            caption=(
                f"💳 *Pay ₹{session_amount:.2f}*\n\n"
                f"📱 Scan with any UPI app\n"
                f"⚠️ Pay exactly *₹{session_amount:.2f}*\n"
                f"⏱ {cfg.timeout // 60} min timeout\n\n"
                f"🔄 Verifying..."
            ),
            reply_markup=waiting_kb(),
            parse_mode="Markdown",
        )

        insert_payment(order_id, user_id, amount, session_amount, expire_at, qr_msg.message_id)

        # ── Poll loop ──────────────────────────────────────
        elapsed = 0
        while elapsed < cfg.timeout:
            await asyncio.sleep(cfg.poll_interval)
            elapsed += cfg.poll_interval

            if s.status != "PENDING":
                break

            try:
                match = find_payment(s.amount, s.created_at, s.expire_at, cfg)
            except CredentialsExpiredError:
                await _alert_admins_credentials_expired(ctx.bot)
                continue
            except Exception as e:
                log.error(f"BharatPe API error during poll: {e}")
                continue

            if match:
                s.status = "SUCCESS"
                complete_payment(order_id, match["utr"], match.get("vpa", ""))
                log_activity(user_id, "payment_success", f"{order_id} UTR={match['utr']}")

                try:
                    await qr_msg.edit_caption(
                        caption=f"💳 ₹{s.amount:.2f}\n\n✅ *Payment Verified*",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

                payer_name = match.get("payer_name", "")
                payer_handle = match.get("payer_handle", "")
                vpa = match.get("vpa", "")

                if payer_name and payer_handle:
                    payer_line = f"👤 From: {payer_name} via {payer_handle}"
                elif payer_name:
                    payer_line = f"👤 From: {payer_name}"
                elif vpa:
                    payer_line = f"👤 From: {vpa}"
                else:
                    payer_line = "👤 From: N/A"

                await qr_msg.reply_text(
                    f"✅ *Payment Successful!*\n\n"
                    f"💰 Amount: *₹{match['amount']:.2f}*\n"
                    f"🔗 UTR: `{match['utr']}`\n"
                    f"{payer_line}\n"
                    f"🕐 {match['timestamp']}\n"
                    f"📝 `{order_id}`",
                    reply_markup=result_kb(),
                    parse_mode="Markdown",
                )

                log.info(f"OK  | {order_id} | UTR={match['utr']}")
                ctx.user_data.pop("active_order", None)
                return

        # ── Expired ────────────────────────────────────────
        if s.status == "PENDING":
            s.status = "FAILURE"
            fail_payment(order_id)
            log_activity(user_id, "payment_expired", order_id)

        try:
            await qr_msg.edit_caption(
                caption=f"💳 ₹{s.amount:.2f}\n\n❌ *Expired*",
                parse_mode="Markdown",
            )
        except Exception:
            pass

        await qr_msg.reply_text(
            f"❌ *Payment Expired*\n\n"
            f"No payment received in {cfg.timeout // 60} min.\n"
            f"Order: `{order_id}`",
            reply_markup=result_kb(),
            parse_mode="Markdown",
        )

        log.info(f"EXP | {order_id}")
        ctx.user_data.pop("active_order", None)

    async def cmd_pay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await track_user(update)
        if await check_blocked(update):
            return
        user_id = update.effective_user.id
        if ctx.args:
            try:
                await _start_payment(update.message, ctx, float(ctx.args[0]), user_id)
                return
            except ValueError:
                pass
        await update.message.reply_text("Select amount or enter custom:", reply_markup=amounts_kb())

    async def on_pay_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        user_id = q.from_user.id
        _, action = q.data.split(":", 1)

        if action in ("start", "custom"):
            if await check_blocked(update):
                return
            await q.message.reply_text("Enter the amount (₹):")
            ctx.user_data["input"] = "pay_amount"
        elif action == "cancel":
            oid = ctx.user_data.pop("active_order", None)
            if oid:
                fail_payment(oid)
                log_activity(user_id, "cancel", oid)
            await q.message.reply_text("❌ Payment cancelled.", reply_markup=result_kb())
        else:
            if await check_blocked(update):
                return
            await _start_payment(q.message, ctx, float(action), user_id)

    async def on_amount_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if ctx.user_data.get("input") != "pay_amount":
            return
        ctx.user_data.pop("input", None)
        text = update.message.text.strip().replace("₹", "").replace(",", "")
        try:
            amount = float(text)
        except ValueError:
            await update.message.reply_text("❌ Enter a valid number. Example: `100`", parse_mode="Markdown")
            return
        await _start_payment(update.message, ctx, amount, update.effective_user.id)

    app.add_handler(CommandHandler("pay", cmd_pay))
    app.add_handler(CallbackQueryHandler(on_pay_button, pattern=r"^pay:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_amount_text), group=1)
