"""Admin handlers — dashboard, members, search, block, broadcast, credential renewal."""

import logging
from telegram import Update
from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

from database import (
    admin_dashboard, admin_recent, admin_users, admin_search,
    block_user, unblock_user, log_activity,
)
from bharatpe import check_credentials, update_credentials
from .config import PaymentConfig
from .keyboards import admin_kb, back_admin_kb, is_admin

log = logging.getLogger(__name__)


def register_admin_handlers(app, cfg: PaymentConfig):
    """Register admin-only handlers, closed over cfg.

    Args:
        app: python-telegram-bot Application.
        cfg: PaymentConfig — provides admin_ids and BharatPe credentials.
    """

    def _is_admin(uid: int) -> bool:
        return is_admin(uid, cfg.admin_ids)

    def _admin_only(func):
        async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            uid = update.effective_user.id
            if not _is_admin(uid):
                msg = update.message or update.callback_query.message
                await msg.reply_text("🚫 Unauthorized.")
                return
            return await func(update, ctx)
        return wrapper

    @_admin_only
    async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🔐 *Admin Panel*", reply_markup=admin_kb(), parse_mode="Markdown")

    @_admin_only
    async def cmd_renew_credentials(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        ctx.user_data["input"] = "admin_renew_token"
        await update.message.reply_text(
            "🔑 *Renew BharatPe Credentials*\n\n"
            "Step 1 of 2 — paste the new *API token* "
            "(the `token` header value from BharatPe DevTools):\n\n"
            "Send /cancel to abort.",
            parse_mode="Markdown",
        )

    async def on_admin_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()

        if not _is_admin(q.from_user.id):
            await q.message.reply_text("🚫 Unauthorized.")
            return

        _, action = q.data.split(":", 1)

        if action == "panel":
            await q.message.reply_text("🔐 *Admin Panel*", reply_markup=admin_kb(), parse_mode="Markdown")

        elif action == "dash":
            d = admin_dashboard()
            api_state = check_credentials(cfg)
            api_status = {
                "ok":      "🟢 Connected",
                "expired": "🔴 Credentials Expired",
                "unknown": "🟡 Unreachable (network error)",
            }.get(api_state, "🟡 Unknown")
            await q.message.reply_text(
                f"📊 *Dashboard*\n\n"
                f"*BharatPe API*\n"
                f"  {api_status}\n\n"
                f"*Today*\n"
                f"  💰 ₹{d['today_rev']:,.2f} ({d['today_count']} payments)\n\n"
                f"*All Time*\n"
                f"  💰 Revenue: ₹{d['revenue']:,.2f}\n"
                f"  ✅ Success: {d['success']}\n"
                f"  ❌ Failed: {d['failed']}\n"
                f"  ⏳ Pending: {d['pending']}\n\n"
                f"*Members*\n"
                f"  👥 Total: {d['users']}\n"
                f"  🟢 Active (24h): {d['active']}\n"
                f"  🚫 Blocked: {d['blocked']}",
                reply_markup=back_admin_kb(),
                parse_mode="Markdown",
            )

        elif action == "recent":
            rows = admin_recent(10)
            if not rows:
                await q.message.reply_text("No payments.", reply_markup=back_admin_kb())
                return
            lines = ["💰 *Recent Payments*\n"]
            for r in rows:
                icon = {"SUCCESS": "✅", "FAILURE": "❌", "PENDING": "⏳"}.get(r["status"], "❓")
                who = f"@{r['username']}" if r.get("username") else str(r["user_id"])
                t = r["created_at"].strftime("%d/%m %H:%M")
                utr = f" `{r['utr']}`" if r.get("utr") else ""
                lines.append(f"{icon} ₹{r['session_amount']:.2f} {who} {t}{utr}")
            await q.message.reply_text("\n".join(lines), reply_markup=back_admin_kb(), parse_mode="Markdown")

        elif action == "users":
            rows = admin_users(15)
            if not rows:
                await q.message.reply_text("No members.", reply_markup=back_admin_kb())
                return
            lines = ["👥 *Members*\n"]
            for r in rows:
                name = f"@{r['username']}" if r.get("username") else (r.get("first_name") or "—")
                flag = " 🚫" if r["is_blocked"] else ""
                lines.append(
                    f"{'🚫' if r['is_blocked'] else '👤'} {name}{flag}\n"
                    f"   `{r['chat_id']}` • ₹{r['total_paid']:,.2f} • {r['payment_count']} txns"
                )
            await q.message.reply_text("\n".join(lines), reply_markup=back_admin_kb(), parse_mode="Markdown")

        elif action == "search":
            await q.message.reply_text("🔍 Enter Order ID or UTR:")
            ctx.user_data["input"] = "admin_search"

        elif action == "block":
            await q.message.reply_text("🚫 Enter Chat ID to block:")
            ctx.user_data["input"] = "admin_block"

        elif action == "unblock":
            await q.message.reply_text("✅ Enter Chat ID to unblock:")
            ctx.user_data["input"] = "admin_unblock"

        elif action == "broadcast":
            await q.message.reply_text("📢 Enter message to broadcast:")
            ctx.user_data["input"] = "admin_broadcast"

    async def on_admin_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        inp = ctx.user_data.get("input", "")
        if not inp.startswith("admin_"):
            return

        uid = update.effective_user.id
        if not _is_admin(uid):
            return

        ctx.user_data.pop("input", None)
        text = update.message.text.strip()

        if inp == "admin_search":
            r = admin_search(text)
            if not r:
                await update.message.reply_text(
                    f"❌ Not found: `{text}`",
                    parse_mode="Markdown",
                    reply_markup=back_admin_kb(),
                )
                return
            icon = {"SUCCESS": "✅", "FAILURE": "❌", "PENDING": "⏳"}.get(r["status"], "❓")
            await update.message.reply_text(
                f"🔍 *Payment Details*\n\n"
                f"{icon} Status: *{r['status']}*\n"
                f"📝 Order: `{r['order_id']}`\n"
                f"💰 Amount: ₹{r['session_amount']:.2f}\n"
                f"👤 User: `{r['user_id']}`\n"
                f"🔗 UTR: `{r.get('utr') or '—'}`\n"
                f"👤 Payer: {r.get('payer_vpa') or '—'}\n"
                f"🕐 {r['created_at'].strftime('%d/%m/%Y %H:%M:%S')}",
                reply_markup=back_admin_kb(),
                parse_mode="Markdown",
            )

        elif inp == "admin_block":
            try:
                target = int(text)
            except ValueError:
                await update.message.reply_text("❌ Invalid ID.", reply_markup=back_admin_kb())
                return
            if block_user(target):
                log_activity(uid, "block_user", str(target))
                await update.message.reply_text(
                    f"🚫 User `{target}` blocked.", parse_mode="Markdown", reply_markup=back_admin_kb()
                )
            else:
                await update.message.reply_text(
                    f"❌ User `{target}` not found.", parse_mode="Markdown", reply_markup=back_admin_kb()
                )

        elif inp == "admin_unblock":
            try:
                target = int(text)
            except ValueError:
                await update.message.reply_text("❌ Invalid ID.", reply_markup=back_admin_kb())
                return
            if unblock_user(target):
                log_activity(uid, "unblock_user", str(target))
                await update.message.reply_text(
                    f"✅ User `{target}` unblocked.", parse_mode="Markdown", reply_markup=back_admin_kb()
                )
            else:
                await update.message.reply_text(
                    f"❌ User `{target}` not found.", parse_mode="Markdown", reply_markup=back_admin_kb()
                )

        elif inp == "admin_broadcast":
            users = admin_users(limit=10000)
            sent, failed = 0, 0
            for u in users:
                try:
                    await ctx.bot.send_message(
                        u["chat_id"], f"📢 *Announcement*\n\n{text}", parse_mode="Markdown"
                    )
                    sent += 1
                except Exception:
                    failed += 1
            log_activity(uid, "broadcast", f"sent={sent} failed={failed}")
            await update.message.reply_text(
                f"📢 *Broadcast Complete*\n\n✅ Sent: {sent}\n❌ Failed: {failed}",
                reply_markup=back_admin_kb(),
                parse_mode="Markdown",
            )

        elif inp == "admin_renew_token":
            if not text:
                await update.message.reply_text("❌ Token cannot be empty. Try again or send /cancel.")
                ctx.user_data["input"] = "admin_renew_token"
                return
            ctx.user_data["pending_token"] = text
            ctx.user_data["input"] = "admin_renew_cookie"
            await update.message.reply_text(
                "🔑 *Renew BharatPe Credentials*\n\n"
                "Step 2 of 2 — paste the new *Cookie* header value:\n\n"
                "Send /cancel to abort.",
                parse_mode="Markdown",
            )

        elif inp == "admin_renew_cookie":
            token = ctx.user_data.pop("pending_token", "")
            if not text or not token:
                await update.message.reply_text(
                    "❌ Cookie cannot be empty. Start again with /renewcredentials."
                )
                return
            update_credentials(token, text, cfg)
            status = check_credentials(cfg)
            log_activity(uid, "renew_credentials", f"api_status={status}")
            if status == "ok":
                await update.message.reply_text(
                    "✅ *Credentials updated successfully!*\n\n"
                    "🟢 BharatPe API is responding. Payment verification is active.",
                    reply_markup=back_admin_kb(),
                    parse_mode="Markdown",
                )
                log.info(f"Admin {uid} renewed BharatPe credentials — API OK")
            elif status == "expired":
                await update.message.reply_text(
                    "⚠️ *Credentials applied but still rejected by BharatPe.*\n\n"
                    "Double-check you copied the correct `token` header and full `Cookie` value "
                    "from an active DevTools session and try again with /renewcredentials.",
                    reply_markup=back_admin_kb(),
                    parse_mode="Markdown",
                )
                log.warning(f"Admin {uid} renewed credentials but API still returns expired")
            else:
                await update.message.reply_text(
                    "🟡 *Credentials saved, but BharatPe API is currently unreachable.*\n\n"
                    "This may be a temporary network issue. "
                    "Check the dashboard with /admin → Dashboard to verify once the API comes back.",
                    reply_markup=back_admin_kb(),
                    parse_mode="Markdown",
                )
                log.warning(f"Admin {uid} renewed credentials — API status unknown (network)")

    async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        removed = ctx.user_data.pop("input", None)
        ctx.user_data.pop("pending_token", None)
        if removed and removed.startswith("admin_renew"):
            await update.message.reply_text("❌ Credential renewal cancelled.", reply_markup=back_admin_kb())
        elif removed:
            await update.message.reply_text("❌ Cancelled.", reply_markup=back_admin_kb())

    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("renewcredentials", cmd_renew_credentials))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_admin_button, pattern=r"^admin:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_admin_text), group=2)
