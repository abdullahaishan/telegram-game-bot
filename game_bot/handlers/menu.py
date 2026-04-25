"""
Main Menu Navigation Handler

Handles all callback queries for main menu navigation,
including sub-menu displays and back-to-main navigation.
"""

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    CURRENCY_NAME,
    WIN_REWARD,
    SHARE_REWARD,
    WITHDRAWAL_MIN_SAR,
    WITHDRAWAL_METHODS,
)
from database import async_fetchone, async_fetchall

logger = logging.getLogger(__name__)


async def _get_internal_user_id(telegram_id: int):
    """Resolve a telegram_id to the internal users.id. Returns None if not found."""
    row = await async_fetchone("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    return row["id"] if row else None


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the main menu inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🎮 Games", callback_data="games_menu"),
            InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu"),
        ],
        [
            InlineKeyboardButton("🛒 Marketplace", callback_data="marketplace_menu"),
            InlineKeyboardButton("👤 Profile", callback_data="profile_menu"),
        ],
        [
            InlineKeyboardButton("📢 Promote", callback_data="promote_menu"),
            InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_menu"),
        ],
        [
            InlineKeyboardButton("🧠 Game Builder", callback_data="builder_menu"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_back_keyboard() -> InlineKeyboardMarkup:
    """Build a keyboard with just a back-to-main button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
    ])


async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all menu-related callback queries."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = update.effective_user.id

    handlers = {
        "main_menu": show_main_menu,
        "games_menu": show_games_menu,
        "wallet_menu": show_wallet_menu,
        "marketplace_menu": show_marketplace_menu,
        "profile_menu": show_profile_menu,
        "promote_menu": show_promote_menu,
        "withdraw_menu": show_withdraw_menu,
        "builder_menu": show_builder_menu,
        "back_to_main": show_main_menu,
    }

    handler = handlers.get(data)
    if handler:
        await handler(update, context)
    else:
        await show_main_menu(update, context)


async def _safe_edit(query, text, keyboard, parse_mode="HTML"):
    """Safely edit a message or reply if editing fails."""
    try:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode=parse_mode, reply_markup=keyboard)


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the main menu."""
    query = update.callback_query
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name or "Player"

    internal_id = await _get_internal_user_id(user_id)

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (internal_id,))
    balance = wallet["balance"] if wallet else 0.0

    profile = await async_fetchone(
        "SELECT title, badge FROM profiles WHERE user_id = ?", (internal_id,)
    )
    title = profile["title"] if profile else "New Player"
    badge = profile["badge"] if profile else "🟢"

    text = (
        f"🎮 <b>Game Platform — Main Menu</b>\n\n"
        f"{badge} <b>{first_name}</b> — {title}\n"
        f"💰 Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n\n"
        f"Choose an option below:"
    )

    await _safe_edit(query, text, build_main_menu_keyboard())


async def show_games_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the games menu with a browse button."""
    query = update.callback_query

    text = (
        "🎮 <b>Games</b>\n\n"
        "Browse our collection of multiplayer games!\n\n"
        "🏆 Win games to earn rewards!\n"
        f"💰 Win Reward: <b>{WIN_REWARD} {CURRENCY_NAME}</b>\n\n"
        "What would you like to do?"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Browse Games", callback_data="browse_games")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
    ])

    await _safe_edit(query, text, keyboard)


async def show_wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the wallet menu with balance and actions."""
    query = update.callback_query
    user_id = update.effective_user.id

    internal_id = await _get_internal_user_id(user_id)

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (internal_id,))
    balance = wallet["balance"] if wallet else 0.0

    recent_tx = await async_fetchall(
        "SELECT type, amount, description, created_at FROM transactions "
        "WHERE user_id = ? ORDER BY created_at DESC LIMIT 3",
        (internal_id,),
    )

    text = (
        f"💰 <b>Wallet</b>\n\n"
        f"Current Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n\n"
    )

    if recent_tx:
        text += "📊 <b>Recent Transactions:</b>\n"
        for tx in recent_tx:
            sign = "+" if tx["amount"] > 0 else ""
            text += f"  {sign}{tx['amount']:.2f} — {tx['description'][:30]}\n"
    else:
        text += "No transactions yet.\n"

    text += f"\n💡 Earn {CURRENCY_NAME} by winning games, sharing, or referring friends!"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📜 Full History", callback_data="wallet_history:1"),
            InlineKeyboardButton("📥 Deposit Info", callback_data="wallet_deposit_info"),
        ],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
    ])

    await _safe_edit(query, text, keyboard)


async def show_marketplace_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the marketplace menu."""
    query = update.callback_query
    user_id = update.effective_user.id

    internal_id = await _get_internal_user_id(user_id)

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (internal_id,))
    balance = wallet["balance"] if wallet else 0.0

    items = await async_fetchall(
        "SELECT id, name, price_sar, description FROM store_items WHERE is_active = 1 ORDER BY price_sar ASC"
    )

    text = (
        f"🛒 <b>Marketplace</b>\n\n"
        f"💰 Your Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n\n"
        "Available items:\n\n"
    )

    keyboard_rows = []
    for item in items:
        text += f"🔹 <b>{item['name']}</b> — {item['price_sar']:.2f} {CURRENCY_NAME}\n"
        text += f"   {item['description']}\n\n"
        keyboard_rows.append([
            InlineKeyboardButton(
                f"Buy {item['name']} ({item['price_sar']:.2f})",
                callback_data=f"buy_item:{item['id']}",
            )
        ])

    # Add Game Creation License if not purchased
    license_record = await async_fetchone(
        "SELECT id FROM owned_features WHERE user_id = ? AND feature_type = 'game_creation_license'",
        (internal_id,),
    )
    if not license_record:
        text += "🧠 <b>Game Creation License</b> — 10.00 SAR\n"
        text += "   Unlock the Game Builder to create your own games!\n\n"
        keyboard_rows.append([
            InlineKeyboardButton("🧠 Buy Creation License (10.00)", callback_data="buy_game_creation_license"),
        ])

    keyboard_rows.append([
        InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)
    await _safe_edit(query, text, keyboard)


async def show_profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the profile menu."""
    query = update.callback_query

    text = (
        "👤 <b>Profile</b>\n\n"
        "View and customize your gaming profile.\n\n"
        "What would you like to do?"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 View Profile", callback_data="profile_view")],
        [InlineKeyboardButton("📈 Detailed Stats", callback_data="profile_stats")],
        [InlineKeyboardButton("🏆 Set Title", callback_data="profile_set_title")],
        [InlineKeyboardButton("🎖 Set Badge", callback_data="profile_set_badge")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
    ])

    await _safe_edit(query, text, keyboard)


async def show_promote_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the promotion menu."""
    query = update.callback_query
    user_id = update.effective_user.id

    internal_id = await _get_internal_user_id(user_id)

    active_promos = await async_fetchall(
        "SELECT id, channel_link, duration_hours, status FROM promotions "
        "WHERE user_id = ? AND status IN ('active', 'pending') ORDER BY created_at DESC",
        (internal_id,),
    )

    text = (
        "📢 <b>Channel Promotion</b>\n\n"
        "Promote your Telegram channel to our community!\n\n"
        "🔹 Active promotions can appear to all users\n"
        "🔹 Maximum 3 promotions run simultaneously\n"
        "🔹 Queue system ensures fair visibility\n\n"
    )

    if active_promos:
        text += "📋 <b>Your Promotions:</b>\n"
        for promo in active_promos:
            status_emoji = "🟢" if promo["status"] == "active" else "🟡"
            text += (
                f"  {status_emoji} {promo['channel_link']} "
                f"({promo['duration_hours']}h) — {promo['status']}\n"
            )
        text += "\n"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Create Promotion", callback_data="promotion_create")],
        [InlineKeyboardButton("📊 Promotion Status", callback_data="promotion_status")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
    ])

    await _safe_edit(query, text, keyboard)


async def show_withdraw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the withdrawal menu."""
    query = update.callback_query
    user_id = update.effective_user.id

    internal_id = await _get_internal_user_id(user_id)

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (internal_id,))
    balance = wallet["balance"] if wallet else 0.0

    pending = await async_fetchall(
        "SELECT id, amount, method, status, created_at FROM withdrawals "
        "WHERE user_id = ? AND status = 'pending' ORDER BY created_at DESC",
        (internal_id,),
    )

    text = (
        f"💸 <b>Withdrawal</b>\n\n"
        f"💰 Your Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n"
        f"📋 Minimum Withdrawal: <b>{WITHDRAWAL_MIN_SAR} {CURRENCY_NAME}</b>\n"
        f"💳 Available Methods: {', '.join(WITHDRAWAL_METHODS)}\n\n"
    )

    if pending:
        text += "⏳ <b>Pending Withdrawals:</b>\n"
        for w in pending:
            text += f"  • {w['amount']:.2f} {CURRENCY_NAME} via {w['method']} — {w['status']}\n"
        text += "\n"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Start Withdrawal", callback_data="withdraw_start")],
        [InlineKeyboardButton("📊 Withdrawal Status", callback_data="withdraw_status")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
    ])

    await _safe_edit(query, text, keyboard)


async def show_builder_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the Game Builder menu."""
    query = update.callback_query
    user_id = update.effective_user.id

    internal_id = await _get_internal_user_id(user_id)

    # Check if user has Game Creation License
    license_record = await async_fetchone(
        "SELECT id FROM owned_features WHERE user_id = ? AND feature_type = 'game_creation_license'",
        (internal_id,),
    )
    has_license = license_record is not None

    # Count user's published games
    owned_games = await async_fetchall(
        "SELECT game_slug, creator_name FROM game_ownership WHERE owner_user_id = ?",
        (internal_id,),
    )

    # Count user's drafts
    drafts = await async_fetchall(
        "SELECT id, config_json FROM game_drafts WHERE user_id = ? AND status = 'active'",
        (internal_id,),
    )

    text = (
        "🧠 <b>Game Builder Studio</b>\n\n"
        "Create your own multiplayer games without writing code!\n\n"
    )

    if has_license:
        text += "✅ <b>License: Active</b>\n\n"
    else:
        text += "❌ <b>License: Not Purchased</b>\n"
        text += "You need a Game Creation License to build games.\n\n"

    if owned_games:
        text += f"🎮 <b>Your Games:</b> {len(owned_games)}\n"
        for g in owned_games[:3]:
            text += f"  • {g['game_slug']}\n"
        text += "\n"

    if drafts:
        text += f"📝 <b>Drafts:</b> {len(drafts)}\n\n"

    text += "What would you like to do?"

    keyboard_rows = []
    if has_license:
        keyboard_rows.append([
            InlineKeyboardButton("🚀 Start Building", callback_data="builder_start"),
        ])
        if drafts:
            keyboard_rows.append([
                InlineKeyboardButton("📂 Load Draft", callback_data="builder_drafts"),
            ])
    else:
        keyboard_rows.append([
            InlineKeyboardButton("🛒 Buy Creation License", callback_data="buy_game_creation_license"),
        ])

    keyboard_rows.append([
        InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)
    await _safe_edit(query, text, keyboard)
