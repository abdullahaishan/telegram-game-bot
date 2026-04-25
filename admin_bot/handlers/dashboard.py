"""
Admin Dashboard Handler
Displays main dashboard with system stats and navigation buttons.
"""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import ADMIN_IDS, CURRENCY_NAME
from database import async_fetchone, async_fetchall
from admin_bot.utils import admin_guard, log_admin_action


async def _get_dashboard_stats() -> dict:
    """Fetch all dashboard statistics from the database."""
    stats = {}

    # Total users
    row = await async_fetchone("SELECT COUNT(*) as cnt FROM users")
    stats["total_users"] = row["cnt"] if row else 0

    # Active game sessions
    row = await async_fetchone("SELECT COUNT(*) as cnt FROM game_sessions WHERE status = 'active'")
    stats["active_sessions"] = row["cnt"] if row else 0

    # Pending withdrawals
    row = await async_fetchone("SELECT COUNT(*) as cnt FROM withdrawals WHERE status = 'pending'")
    stats["pending_withdrawals"] = row["cnt"] if row else 0

    # Active promotions
    row = await async_fetchone("SELECT COUNT(*) as cnt FROM promotions WHERE status = 'active'")
    stats["active_promotions"] = row["cnt"] if row else 0

    # Total SAR in system
    row = await async_fetchone("SELECT COALESCE(SUM(balance), 0) as total FROM wallets")
    stats["total_sar"] = row["total"] if row else 0

    # Banned users
    row = await async_fetchone("SELECT COUNT(*) as cnt FROM users WHERE is_banned = 1")
    stats["banned_users"] = row["cnt"] if row else 0

    # Pending game approvals
    row = await async_fetchone("SELECT COUNT(*) as cnt FROM games WHERE is_approved = 0")
    stats["pending_games"] = row["cnt"] if row else 0

    # Total games
    row = await async_fetchone("SELECT COUNT(*) as cnt FROM games")
    stats["total_games"] = row["cnt"] if row else 0

    # Store items
    row = await async_fetchone("SELECT COUNT(*) as cnt FROM store_items")
    stats["store_items"] = row["cnt"] if row else 0

    # Required channels
    row = await async_fetchone("SELECT COUNT(*) as cnt FROM required_channels WHERE is_enabled = 1")
    stats["active_channels"] = row["cnt"] if row else 0

    return stats


def _build_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Build the dashboard inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("👥 Users", callback_data="admin_users"),
            InlineKeyboardButton("🎮 Games", callback_data="admin_games"),
        ],
        [
            InlineKeyboardButton("💰 Wallets", callback_data="admin_wallets"),
            InlineKeyboardButton("📢 Promotions", callback_data="admin_promotions"),
        ],
        [
            InlineKeyboardButton("📺 Channels", callback_data="admin_channels"),
            InlineKeyboardButton("💸 Withdrawals", callback_data="admin_withdrawals"),
        ],
        [
            InlineKeyboardButton("🛒 Store", callback_data="admin_store"),
            InlineKeyboardButton("📋 Logs", callback_data="admin_logs"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_refresh"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def _format_dashboard_message(stats: dict) -> str:
    """Format the dashboard stats into a display message."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"🖥 *Admin Dashboard*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 *System Overview*\n"
        f"├ Total Users: `{stats['total_users']}`\n"
        f"├ Banned Users: `{stats['banned_users']}`\n"
        f"├ Total Games: `{stats['total_games']}`\n"
        f"├ Pending Approvals: `{stats['pending_games']}`\n"
        f"├ Active Sessions: `{stats['active_sessions']}`\n"
        f"├ Active Promotions: `{stats['active_promotions']}`\n"
        f"├ Pending Withdrawals: `{stats['pending_withdrawals']}`\n"
        f"├ Store Items: `{stats['store_items']}`\n"
        f"├ Active Channels: `{stats['active_channels']}`\n"
        f"└ Total {CURRENCY_NAME}: `{stats['total_sar']:.2f}` SAR\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Last updated: {now}"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command - show admin dashboard."""
    if not await admin_guard(update, context):
        return

    admin_id = update.effective_user.id
    await log_admin_action(admin_id, "dashboard_view", details="Viewed admin dashboard")

    stats = await _get_dashboard_stats()
    text = _format_dashboard_message(stats)
    keyboard = _build_dashboard_keyboard()

    await update.message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def cb_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin_dashboard callback - navigate back to dashboard."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()

    admin_id = update.effective_user.id
    await log_admin_action(admin_id, "dashboard_view", details="Navigated to admin dashboard")

    stats = await _get_dashboard_stats()
    text = _format_dashboard_message(stats)
    keyboard = _build_dashboard_keyboard()

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def cb_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin_refresh callback - refresh dashboard stats."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer("Refreshing...")

    stats = await _get_dashboard_stats()
    text = _format_dashboard_message(stats)
    keyboard = _build_dashboard_keyboard()

    try:
        await query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    except Exception:
        # If message content is identical, just acknowledge
        pass
