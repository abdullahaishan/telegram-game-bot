"""
Channel Promotion Handlers

Handles promotion status viewing, creation flow,
cancellation, queue position display, and auto-expiry awareness.
"""

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    CURRENCY_NAME,
    PROMOTION_MIN_SAR,
    PROMOTION_MAX_ACTIVE,
)
from database import async_execute, async_fetchone, async_fetchall, async_transaction

logger = logging.getLogger(__name__)


async def promotions_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all promotion-related callback queries."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = update.effective_user.id

    if data == "promotion_status":
        await _show_promotion_status(query, context, user_id)
    elif data == "promotion_create":
        await _start_promotion_creation(query, context, user_id)
    elif data.startswith("promotion_cancel:"):
        promo_id = data.split(":", 1)[1]
        await _cancel_promotion(query, context, user_id, promo_id)
    else:
        await _show_promotion_status(query, context, user_id)


async def _show_promotion_status(query, context, user_id) -> None:
    """Show user's active, queued, and recent promotions."""
    active_promos = await async_fetchall(
        "SELECT id, channel_link, duration_hours, price, status, started_at, created_at "
        "FROM promotions WHERE user_id = ? AND status = 'active' "
        "ORDER BY started_at DESC",
        (user_id,),
    )

    pending_promos = await async_fetchall(
        "SELECT id, channel_link, duration_hours, price, status, created_at "
        "FROM promotions WHERE user_id = ? AND status = 'pending' "
        "ORDER BY created_at ASC",
        (user_id,),
    )

    expired_promos = await async_fetchall(
        "SELECT id, channel_link, duration_hours, price, status, created_at "
        "FROM promotions WHERE user_id = ? AND status IN ('expired', 'cancelled') "
        "ORDER BY created_at DESC LIMIT 5",
        (user_id,),
    )

    total_active = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM promotions WHERE status = 'active'"
    )
    active_count = total_active["cnt"] if total_active else 0

    text = (
        "📢 <b>Promotion Status</b>\n\n"
        f"🟢 Active Promotions (Platform): {active_count}/{PROMOTION_MAX_ACTIVE}\n\n"
    )

    if active_promos:
        text += "🟢 <b>Your Active Promotions:</b>\n"
        for promo in active_promos:
            activated = promo["started_at"][:16] if promo["started_at"] else "N/A"
            expiry = _calculate_expiry(promo["started_at"], promo["duration_hours"])
            text += (
                f"  📢 {promo['channel_link']}\n"
                f"     Duration: {promo['duration_hours']}h | Cost: {promo['price']:.2f} {CURRENCY_NAME}\n"
                f"     Activated: {activated}\n"
                f"     Expires: {expiry}\n"
            )
            text += f"     [❌ Cancel](no-url) — use button below\n\n"

    if pending_promos:
        text += "🟡 <b>Your Queued Promotions:</b>\n"
        all_pending = await async_fetchall(
            "SELECT id, user_id FROM promotions WHERE status = 'pending' ORDER BY created_at ASC"
        )
        for i, q in enumerate(all_pending, 1):
            for promo in pending_promos:
                if q["id"] == promo["id"]:
                    text += (
                        f"  📢 {promo['channel_link']}\n"
                        f"     Queue Position: <b>#{i}</b>\n"
                        f"     Duration: {promo['duration_hours']}h | Cost: {promo['price']:.2f} {CURRENCY_NAME}\n"
                        f"     Created: {promo['created_at'][:16]}\n\n"
                    )
                    break

    if expired_promos:
        text += "🔴 <b>Recent Expired/Cancelled:</b>\n"
        for promo in expired_promos:
            status_emoji = "⏰" if promo["status"] == "expired" else "❌"
            text += (
                f"  {status_emoji} {promo['channel_link']} — {promo['status']}\n"
            )
        text += "\n"

    if not active_promos and not pending_promos and not expired_promos:
        text += (
            "You don't have any promotions yet.\n"
            "Create one to promote your channel to our community!\n\n"
        )

    text += (
        f"📋 Min. Promotion Cost: {PROMOTION_MIN_SAR} {CURRENCY_NAME}\n"
        f"🔄 Max Simultaneous Active: {PROMOTION_MAX_ACTIVE}"
    )

    keyboard_rows = []

    if active_promos or pending_promos:
        for promo in active_promos + pending_promos:
            keyboard_rows.append([
                InlineKeyboardButton(
                    f"❌ Cancel: {promo['channel_link'][:20]}",
                    callback_data=f"promotion_cancel:{promo['id']}",
                )
            ])

    keyboard_rows.append([
        InlineKeyboardButton("📢 Create New Promotion", callback_data="promotion_create"),
    ])
    keyboard_rows.append([
        InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _start_promotion_creation(query, context, user_id) -> None:
    """Start the promotion creation flow."""
    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if balance < PROMOTION_MIN_SAR:
        text = (
            f"❌ <b>Insufficient Balance</b>\n\n"
            f"Minimum promotion cost: <b>{PROMOTION_MIN_SAR} {CURRENCY_NAME}</b>\n"
            f"Your balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n\n"
            f"You need at least <b>{PROMOTION_MIN_SAR - balance:.2f} {CURRENCY_NAME}</b> more."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    active_count = await _get_active_promotion_count()
    user_queued = await _get_user_queued_count(user_id)

    text = (
        "📢 <b>Create Channel Promotion</b>\n\n"
        f"💰 Your Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n"
        f"📋 Min. Cost: {PROMOTION_MIN_SAR} {CURRENCY_NAME}\n"
        f"🟢 Active Promotions: {active_count}/{PROMOTION_MAX_ACTIVE}\n"
    )

    if user_queued > 0:
        text += f"🟡 Your Queued Promotions: {user_queued}\n"

    text += (
        "\n<b>How it works:</b>\n"
        "1️⃣ Enter your channel link (e.g., @my_channel or https://t.me/my_channel)\n"
        "2️⃣ Choose promotion duration\n"
        "3️⃣ Pay the fee\n"
        "4️⃣ Your promotion enters the queue (or goes active if a slot is open)\n"
        "5️⃣ Your channel will be shown to users in the platform!\n\n"
        "Please type your channel link:"
    )

    context.user_data["promotion_state"] = "awaiting_channel"
    context.user_data["promotion_price"] = PROMOTION_MIN_SAR

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="promotion_status")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _cancel_promotion(query, context, user_id, promo_id) -> None:
    """Cancel a promotion and refund the user."""
    promo = await async_fetchone(
        "SELECT id, user_id, channel_link, price, status, duration_hours "
        "FROM promotions WHERE id = ? AND user_id = ?",
        (promo_id, user_id),
    )

    if not promo:
        await query.answer("Promotion not found or not yours.", show_alert=True)
        return

    if promo["status"] not in ("active", "queued"):
        await query.answer("Can only cancel active or queued promotions.", show_alert=True)
        return

    now = datetime.utcnow().isoformat()

    async with async_transaction():
        await async_execute(
            "UPDATE promotions SET status = 'cancelled' WHERE id = ?",
            (promo_id,),
        )
        await async_execute(
            "UPDATE wallets SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
            (promo["price"], now, user_id),
        )
        await async_execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, "refund", promo["price"],
             f"Refund: cancelled promotion for {promo['channel_link']}", now),
        )

    text = (
        f"✅ <b>Promotion Cancelled</b>\n\n"
        f"📢 Channel: {promo['channel_link']}\n"
        f"💰 Refund: <b>{promo['price']:.2f} {CURRENCY_NAME}</b>\n\n"
        f"The refund has been added to your wallet."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Promotion Status", callback_data="promotion_status")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ──────────────────────────────────────────────
# Message Handler for Promotion Flow
# ──────────────────────────────────────────────

async def handle_promotion_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handle text messages during promotion creation flow.
    Returns True if the message was handled, False otherwise.
    """
    state = context.user_data.get("promotion_state")
    if not state:
        return False

    user_id = update.effective_user.id
    text = update.message.text.strip()

    if state == "awaiting_channel":
        channel_link = _validate_channel_link(text)
        if not channel_link:
            await update.message.reply_text(
                "❌ Invalid channel link. Please enter a valid Telegram channel link.\n\n"
                "Examples:\n"
                "• @my_channel\n"
                "• https://t.me/my_channel\n"
                "• t.me/my_channel"
            )
            return True

        context.user_data["promotion_channel"] = channel_link
        context.user_data["promotion_state"] = "awaiting_duration"

        duration_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("24h", callback_data="promo_duration:24"),
                InlineKeyboardButton("48h", callback_data="promo_duration:48"),
            ],
            [
                InlineKeyboardButton("72h", callback_data="promo_duration:72"),
                InlineKeyboardButton("168h (7d)", callback_data="promo_duration:168"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="promotion_status")],
        ])

        await update.message.reply_text(
            f"✅ Channel set: <b>{channel_link}</b>\n\n"
            f"Choose promotion duration:",
            parse_mode="HTML",
            reply_markup=duration_keyboard,
        )
        return True

    elif state == "awaiting_duration":
        return True

    return False


async def handle_promotion_duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle duration selection in promotion creation flow."""
    query = update.callback_query
    await query.answer()

    if not query.data.startswith("promo_duration:"):
        return

    duration_hours = int(query.data.split(":")[1])
    user_id = update.effective_user.id

    context.user_data["promotion_duration"] = duration_hours

    channel_link = context.user_data.get("promotion_channel", "")
    price = context.user_data.get("promotion_price", PROMOTION_MIN_SAR)

    price_per_hour = price / duration_hours

    text = (
        "📢 <b>Confirm Promotion</b>\n\n"
        f"📢 Channel: <b>{channel_link}</b>\n"
        f"⏱ Duration: <b>{duration_hours} hours</b>\n"
        f"💰 Cost: <b>{price:.2f} {CURRENCY_NAME}</b>\n\n"
        f"Confirm and pay?"
    )

    context.user_data["promotion_state"] = "awaiting_confirm"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm & Pay", callback_data="promotion_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="promotion_status"),
        ],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ──────────────────────────────────────────────
# Helper Functions
# ──────────────────────────────────────────────

async def _get_active_promotion_count() -> int:
    """Get the number of currently active promotions."""
    result = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM promotions WHERE status = 'active'"
    )
    return result["cnt"] if result else 0


async def _get_user_queued_count(user_id: int) -> int:
    """Get the number of queued promotions for a user."""
    result = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM promotions WHERE user_id = ? AND status = 'pending'",
        (user_id,),
    )
    return result["cnt"] if result else 0


def _validate_channel_link(link: str) -> str:
    """
    Validate and normalize a Telegram channel link.
    Returns normalized link or empty string if invalid.
    """
    link = link.strip()

    if link.startswith("@"):
        return link

    if link.startswith("https://t.me/"):
        channel_name = link.replace("https://t.me/", "")
        if channel_name:
            return f"@{channel_name}"

    if link.startswith("http://t.me/"):
        channel_name = link.replace("http://t.me/", "")
        if channel_name:
            return f"@{channel_name}"

    if link.startswith("t.me/"):
        channel_name = link.replace("t.me/", "")
        if channel_name:
            return f"@{channel_name}"

    if link.isalnum() and len(link) >= 5:
        return f"@{link}"

    return ""


def _calculate_expiry(started_at: str, duration_hours: int) -> str:
    """Calculate and format the expiry time of a promotion."""
    if not started_at:
        return "N/A"

    try:
        activated = datetime.fromisoformat(started_at)
        from datetime import timedelta
        expiry = activated + timedelta(hours=duration_hours)
        now = datetime.utcnow()
        remaining = expiry - now

        if remaining.total_seconds() <= 0:
            return "Expired"

        hours = int(remaining.total_seconds() // 3600)
        minutes = int((remaining.total_seconds() % 3600) // 60)

        return f"{hours}h {minutes}m remaining"
    except Exception:
        return "N/A"
