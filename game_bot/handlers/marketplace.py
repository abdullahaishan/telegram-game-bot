"""
Marketplace Handlers

Handles marketplace browsing, item purchasing,
promotion flow, and various marketplace items
(Channel Promotion, Profile Pack, Game Ownership,
Private Rooms, Featured Slot).
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


async def marketplace_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available store items."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    items = await async_fetchall(
        "SELECT id, name, description, price_sar, item_type, is_active FROM store_items "
        "WHERE is_active = 1 ORDER BY category, price ASC"
    )

    text = (
        f"🛒 <b>Marketplace</b>\n\n"
        f"💰 Your Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n\n"
    )

    if not items:
        text += "No items available at the moment. Check back later!"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    current_category = None
    for item in items:
        if item["category"] != current_category:
            current_category = item["category"]
            category_emoji = _get_category_emoji(current_category)
            text += f"\n{category_emoji} <b>{current_category}</b>\n"
            text += "─" * 20 + "\n"

        text += (
            f"  📦 <b>{item['name']}</b>\n"
            f"     {item['description']}\n"
            f"     💰 {item['price']:.2f} {CURRENCY_NAME}\n\n"
        )

    text += "\nSelect an item to purchase:"

    keyboard_rows = []
    for item in items:
        keyboard_rows.append([
            InlineKeyboardButton(
                f"{item['name']} ({item['price']:.2f})",
                callback_data=f"buy_item:{item['id']}",
            )
        ])

    keyboard_rows.append([
        InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def buy_item_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Purchase an item from the marketplace."""
    query = update.callback_query
    await query.answer()

    item_id = query.data.split(":", 1)[1]
    user_id = update.effective_user.id

    item = await async_fetchone(
        "SELECT id, name, description, price_sar, item_type, is_active FROM store_items "
        "WHERE id = ? AND is_active = 1",
        (item_id,),
    )

    if not item:
        await query.answer("Item not found or no longer available.", show_alert=True)
        return

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if balance < item["price_sar"]:
        text = (
            f"❌ <b>Insufficient Balance</b>\n\n"
            f"📦 Item: {item['name']}\n"
            f"💰 Price: {item['price']:.2f} {CURRENCY_NAME}\n"
            f"💳 Your Balance: {balance:.2f} {CURRENCY_NAME}\n\n"
            f"You need <b>{item['price'] - balance:.2f} {CURRENCY_NAME}</b> more."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu")],
            [InlineKeyboardButton("🛒 Marketplace", callback_data="marketplace_view")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    category = item["category"]
    if category == "Channel Promotion":
        context.user_data["pending_promotion_item_id"] = item_id
        await _start_promotion_flow(query, context, item)
        return
    elif category == "Profile Pack":
        await _purchase_profile_pack(query, context, user_id, item)
        return
    elif category == "Game Ownership":
        await _purchase_game_ownership(query, context, user_id, item)
        return
    elif category == "Private Rooms":
        await _purchase_private_room(query, context, user_id, item)
        return
    elif category == "Featured Slot":
        await _purchase_featured_slot(query, context, user_id, item)
        return
    else:
        await _purchase_generic_item(query, context, user_id, item)


async def marketplace_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle marketplace-related callback queries."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = update.effective_user.id

    if data == "buy_promotion":
        await _handle_buy_promotion(query, context, user_id)
    elif data == "buy_profile_pack":
        await _handle_buy_profile_pack(query, context, user_id)
    elif data == "buy_game_ownership":
        await _handle_buy_game_ownership(query, context, user_id)
    elif data == "buy_private_room":
        await _handle_buy_private_room(query, context, user_id)
    elif data == "buy_featured_slot":
        await _handle_buy_featured_slot(query, context, user_id)
    elif data == "promotion_set_channel":
        await _handle_promotion_set_channel(query, context, user_id)
    elif data == "promotion_confirm":
        await _handle_promotion_confirm(query, context, user_id)
    else:
        await marketplace_view_handler(update, context)


# ──────────────────────────────────────────────
# Promotion Flow
# ──────────────────────────────────────────────

async def _start_promotion_flow(query, context, item) -> None:
    """Start the promotion purchase flow — ask for channel link."""
    user_id = query.from_user.id

    active_count = await _get_active_promotion_count()
    queue_count = await _get_queued_promotion_count(user_id)

    text = (
        "📢 <b>Channel Promotion</b>\n\n"
        f"💰 Price: <b>{item['price']:.2f} {CURRENCY_NAME}</b>\n"
        f"📋 Min. Cost: {PROMOTION_MIN_SAR} {CURRENCY_NAME}\n"
        f"🟢 Active Promotions: {active_count}/{PROMOTION_MAX_ACTIVE}\n"
    )

    if queue_count > 0:
        text += f"🟡 Your Queued Promotions: {queue_count}\n"

    text += (
        "\n<b>How it works:</b>\n"
        "1. Enter your channel link (e.g., @channel or https://t.me/channel)\n"
        "2. Choose duration\n"
        "3. Pay and your promotion enters the queue\n"
        "4. When a slot opens, your promotion goes live!\n\n"
        "Please enter your channel link:"
    )

    context.user_data["promotion_state"] = "awaiting_channel"
    context.user_data["promotion_item_id"] = item["id"]
    context.user_data["promotion_price"] = item["price_sar"]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="marketplace_view")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _handle_buy_promotion(query, context, user_id) -> None:
    """Handle the buy_promotion direct callback."""
    items = await async_fetchall(
        "SELECT id, name, price, description FROM store_items "
        "WHERE category = 'Channel Promotion' AND is_active = 1 LIMIT 1"
    )
    if not items:
        await query.answer("No promotion packages available.", show_alert=True)
        return

    await _start_promotion_flow(query, context, items[0])


async def _handle_promotion_set_channel(query, context, user_id) -> None:
    """Handle setting the channel for promotion (re-entry point)."""
    if "promotion_item_id" not in context.user_data:
        await query.answer("No active promotion flow. Start from marketplace.", show_alert=True)
        return

    item = await async_fetchone(
        "SELECT id, name, price FROM store_items WHERE id = ?",
        (context.user_data["promotion_item_id"],),
    )
    if not item:
        await query.answer("Item no longer available.", show_alert=True)
        context.user_data.pop("promotion_state", None)
        return

    await _start_promotion_flow(query, context, item)


async def _handle_promotion_confirm(query, context, user_id) -> None:
    """Confirm and pay for a promotion."""
    promo_state = context.user_data.get("promotion_state")
    if promo_state != "awaiting_confirm":
        await query.answer("No promotion to confirm. Start from marketplace.", show_alert=True)
        return

    channel_link = context.user_data.get("promotion_channel")
    duration_hours = context.user_data.get("promotion_duration", 24)
    price = context.user_data.get("promotion_price", 0.0)
    item_id = context.user_data.get("promotion_item_id")

    if not channel_link:
        await query.answer("Channel link not set. Please start again.", show_alert=True)
        context.user_data.pop("promotion_state", None)
        return

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if balance < price:
        text = (
            f"❌ <b>Insufficient Balance</b>\n\n"
            f"💰 Price: {price:.2f} {CURRENCY_NAME}\n"
            f"💳 Balance: {balance:.2f} {CURRENCY_NAME}\n\n"
            f"You need <b>{price - balance:.2f} {CURRENCY_NAME}</b> more."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu")],
            [InlineKeyboardButton("🛒 Marketplace", callback_data="marketplace_view")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        context.user_data.pop("promotion_state", None)
        return

    active_count = await _get_active_promotion_count()
    status = "active" if active_count < PROMOTION_MAX_ACTIVE else "queued"
    now = datetime.utcnow().isoformat()

    async with async_transaction():
        await async_execute(
            "UPDATE wallets SET balance = balance - ?, updated_at = ? WHERE user_id = ?",
            (price, now, user_id),
        )
        await async_execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, "promotion", -price,
             f"Channel promotion for {channel_link}", now),
        )

        cursor = await async_execute(
            "INSERT INTO promotions (user_id, channel_link, duration_hours, price, status, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, channel_link, duration_hours, price, status, now),
        )
        promo_id = cursor.lastrowid

        if item_id:
            await async_execute(
                "INSERT INTO purchases (user_id, item_id, price_paid, status, created_at) "
                "VALUES (?, ?, ?, 'completed', ?)",
                (user_id, item_id, price, now),
            )

        if status == "active":
            await async_execute(
                "UPDATE promotions SET started_at = ? WHERE id = ?",
                (now, promo_id),
            )

    queue_position = 0
    if status == "queued":
        queued = await async_fetchall(
            "SELECT id FROM promotions WHERE status = 'pending' ORDER BY created_at ASC"
        )
        for i, q in enumerate(queued, 1):
            if q["id"] == promo_id:
                queue_position = i
                break

    status_text = "🟢 Active" if status == "active" else f"🟡 Queued (Position: {queue_position})"

    text = (
        f"✅ <b>Promotion Purchased!</b>\n\n"
        f"📢 Channel: {channel_link}\n"
        f"⏱ Duration: {duration_hours} hours\n"
        f"💰 Cost: {price:.2f} {CURRENCY_NAME}\n"
        f"📊 Status: {status_text}\n\n"
    )

    if status == "queued":
        text += (
            f"Your promotion is in the queue. There are {active_count} active promotions "
            f"(max {PROMOTION_MAX_ACTIVE}). You'll be notified when it goes live!\n"
        )
    else:
        text += "Your promotion is now live! 🎉\n"

    context.user_data.pop("promotion_state", None)
    context.user_data.pop("promotion_channel", None)
    context.user_data.pop("promotion_duration", None)
    context.user_data.pop("promotion_price", None)
    context.user_data.pop("promotion_item_id", None)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Promotion Status", callback_data="promotion_status")],
        [InlineKeyboardButton("🛒 Marketplace", callback_data="marketplace_view")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ──────────────────────────────────────────────
# Profile Pack
# ──────────────────────────────────────────────

async def _purchase_profile_pack(query, context, user_id, item) -> None:
    """Purchase a premium profile pack."""
    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if balance < item["price_sar"]:
        await _show_insufficient_balance(query, item, balance)
        return

    already_owned = await async_fetchone(
        "SELECT id FROM owned_features WHERE user_id = ? AND feature = 'premium_profile'",
        (user_id,),
    )
    if already_owned:
        await query.answer("You already own the Premium Profile Pack!", show_alert=True)
        return

    now = datetime.utcnow().isoformat()
    async with async_transaction():
        await async_execute(
            "UPDATE wallets SET balance = balance - ?, updated_at = ? WHERE user_id = ?",
            (item["price_sar"], now, user_id),
        )
        await async_execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, "purchase", -item["price_sar"], f"Purchased {item['name']}", now),
        )
        await async_execute(
            "INSERT INTO purchases (user_id, item_id, price_paid, status, created_at) "
            "VALUES (?, ?, ?, 'completed', ?)",
            (user_id, item["id"], item["price_sar"], now),
        )
        await async_execute(
            "INSERT INTO owned_features (user_id, feature, purchased_at) VALUES (?, ?, ?)",
            (user_id, "premium_profile", now),
        )
        await async_execute(
            "UPDATE profiles SET badge = '⭐' WHERE user_id = ?",
            (user_id,),
        )

    text = (
        f"✅ <b>Purchase Complete!</b>\n\n"
        f"📦 Item: {item['name']}\n"
        f"💰 Cost: {item['price']:.2f} {CURRENCY_NAME}\n\n"
        f"🎉 You now have a <b>Premium Profile</b>!\n"
        f"   • Custom titles unlocked\n"
        f"   • Premium badge (⭐)\n"
        f"   • Priority in game lobbies"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 View Profile", callback_data="profile_view")],
        [InlineKeyboardButton("🛒 Marketplace", callback_data="marketplace_view")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _handle_buy_profile_pack(query, context, user_id) -> None:
    """Handle the buy_profile_pack direct callback."""
    items = await async_fetchall(
        "SELECT id, name, price, description FROM store_items "
        "WHERE category = 'Profile Pack' AND is_active = 1 LIMIT 1"
    )
    if not items:
        await query.answer("No profile packs available.", show_alert=True)
        return
    await _purchase_profile_pack(query, context, user_id, items[0])


# ──────────────────────────────────────────────
# Game Ownership
# ──────────────────────────────────────────────

async def _purchase_game_ownership(query, context, user_id, item) -> None:
    """Purchase game creation rights."""
    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if balance < item["price_sar"]:
        await _show_insufficient_balance(query, item, balance)
        return

    already_owned = await async_fetchone(
        "SELECT id FROM owned_features WHERE user_id = ? AND feature = 'game_ownership'",
        (user_id,),
    )
    if already_owned:
        await query.answer("You already have game creation rights!", show_alert=True)
        return

    now = datetime.utcnow().isoformat()
    async with async_transaction():
        await async_execute(
            "UPDATE wallets SET balance = balance - ?, updated_at = ? WHERE user_id = ?",
            (item["price_sar"], now, user_id),
        )
        await async_execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, "purchase", -item["price_sar"], f"Purchased {item['name']}", now),
        )
        await async_execute(
            "INSERT INTO purchases (user_id, item_id, price_paid, status, created_at) "
            "VALUES (?, ?, ?, 'completed', ?)",
            (user_id, item["id"], item["price_sar"], now),
        )
        await async_execute(
            "INSERT INTO owned_features (user_id, feature, purchased_at) VALUES (?, ?, ?)",
            (user_id, "game_ownership", now),
        )
        await async_execute(
            "INSERT INTO game_ownership (user_id, can_create, granted_at) VALUES (?, 1, ?)",
            (user_id, now),
        )

    text = (
        f"✅ <b>Purchase Complete!</b>\n\n"
        f"📦 Item: {item['name']}\n"
        f"💰 Cost: {item['price']:.2f} {CURRENCY_NAME}\n\n"
        f"🎉 You now have <b>Game Creation Rights</b>!\n"
        f"   • Create and publish your own games\n"
        f"   • Set entry fees and rewards\n"
        f"   • Manage your game community"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 Games", callback_data="games_menu")],
        [InlineKeyboardButton("🛒 Marketplace", callback_data="marketplace_view")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _handle_buy_game_ownership(query, context, user_id) -> None:
    """Handle the buy_game_ownership direct callback."""
    items = await async_fetchall(
        "SELECT id, name, price, description FROM store_items "
        "WHERE category = 'Game Ownership' AND is_active = 1 LIMIT 1"
    )
    if not items:
        await query.answer("No game ownership packages available.", show_alert=True)
        return
    await _purchase_game_ownership(query, context, user_id, items[0])


# ──────────────────────────────────────────────
# Private Rooms
# ──────────────────────────────────────────────

async def _purchase_private_room(query, context, user_id, item) -> None:
    """Purchase private room access."""
    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if balance < item["price_sar"]:
        await _show_insufficient_balance(query, item, balance)
        return

    already_owned = await async_fetchone(
        "SELECT id FROM owned_features WHERE user_id = ? AND feature = 'private_rooms'",
        (user_id,),
    )
    if already_owned:
        await query.answer("You already have private room access!", show_alert=True)
        return

    now = datetime.utcnow().isoformat()
    async with async_transaction():
        await async_execute(
            "UPDATE wallets SET balance = balance - ?, updated_at = ? WHERE user_id = ?",
            (item["price_sar"], now, user_id),
        )
        await async_execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, "purchase", -item["price_sar"], f"Purchased {item['name']}", now),
        )
        await async_execute(
            "INSERT INTO purchases (user_id, item_id, price_paid, status, created_at) "
            "VALUES (?, ?, ?, 'completed', ?)",
            (user_id, item["id"], item["price_sar"], now),
        )
        await async_execute(
            "INSERT INTO owned_features (user_id, feature, purchased_at) VALUES (?, ?, ?)",
            (user_id, "private_rooms", now),
        )

    text = (
        f"✅ <b>Purchase Complete!</b>\n\n"
        f"📦 Item: {item['name']}\n"
        f"💰 Cost: {item['price']:.2f} {CURRENCY_NAME}\n\n"
        f"🎉 You now have <b>Private Room Access</b>!\n"
        f"   • Create invite-only game rooms\n"
        f"   • Password-protect your lobbies\n"
        f"   • Full control over who joins"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 Games", callback_data="games_menu")],
        [InlineKeyboardButton("🛒 Marketplace", callback_data="marketplace_view")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _handle_buy_private_room(query, context, user_id) -> None:
    """Handle the buy_private_room direct callback."""
    items = await async_fetchall(
        "SELECT id, name, price, description FROM store_items "
        "WHERE category = 'Private Rooms' AND is_active = 1 LIMIT 1"
    )
    if not items:
        await query.answer("No private room packages available.", show_alert=True)
        return
    await _purchase_private_room(query, context, user_id, items[0])


# ──────────────────────────────────────────────
# Featured Slot
# ──────────────────────────────────────────────

async def _purchase_featured_slot(query, context, user_id, item) -> None:
    """Purchase a featured slot for a game."""
    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if balance < item["price_sar"]:
        await _show_insufficient_balance(query, item, balance)
        return

    now = datetime.utcnow().isoformat()
    async with async_transaction():
        await async_execute(
            "UPDATE wallets SET balance = balance - ?, updated_at = ? WHERE user_id = ?",
            (item["price_sar"], now, user_id),
        )
        await async_execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, "purchase", -item["price_sar"], f"Purchased {item['name']}", now),
        )
        await async_execute(
            "INSERT INTO purchases (user_id, item_id, price_paid, status, created_at) "
            "VALUES (?, ?, ?, 'completed', ?)",
            (user_id, item["id"], item["price_sar"], now),
        )
        await async_execute(
            "INSERT INTO owned_features (user_id, feature, purchased_at) VALUES (?, ?, ?)",
            (user_id, "featured_slot", now),
        )

    text = (
        f"✅ <b>Purchase Complete!</b>\n\n"
        f"📦 Item: {item['name']}\n"
        f"💰 Cost: {item['price']:.2f} {CURRENCY_NAME}\n\n"
        f"🎉 You now have a <b>Featured Slot</b>!\n"
        f"   • Your game appears at the top of the browse list\n"
        f"   • Highlighted with a ⭐ badge\n"
        f"   • Greater visibility for 7 days"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 Games", callback_data="games_menu")],
        [InlineKeyboardButton("🛒 Marketplace", callback_data="marketplace_view")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _handle_buy_featured_slot(query, context, user_id) -> None:
    """Handle the buy_featured_slot direct callback."""
    items = await async_fetchall(
        "SELECT id, name, price, description FROM store_items "
        "WHERE category = 'Featured Slot' AND is_active = 1 LIMIT 1"
    )
    if not items:
        await query.answer("No featured slot packages available.", show_alert=True)
        return
    await _purchase_featured_slot(query, context, user_id, items[0])


# ──────────────────────────────────────────────
# Generic Item Purchase
# ──────────────────────────────────────────────

async def _purchase_generic_item(query, context, user_id, item) -> None:
    """Purchase a generic item from the marketplace."""
    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if balance < item["price_sar"]:
        await _show_insufficient_balance(query, item, balance)
        return

    now = datetime.utcnow().isoformat()
    async with async_transaction():
        await async_execute(
            "UPDATE wallets SET balance = balance - ?, updated_at = ? WHERE user_id = ?",
            (item["price_sar"], now, user_id),
        )
        await async_execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, "purchase", -item["price_sar"], f"Purchased {item['name']}", now),
        )
        await async_execute(
            "INSERT INTO purchases (user_id, item_id, price_paid, status, created_at) "
            "VALUES (?, ?, ?, 'completed', ?)",
            (user_id, item["id"], item["price_sar"], now),
        )

    text = (
        f"✅ <b>Purchase Complete!</b>\n\n"
        f"📦 Item: {item['name']}\n"
        f"📝 {item['description']}\n"
        f"💰 Cost: {item['price']:.2f} {CURRENCY_NAME}\n\n"
        f"Thank you for your purchase! 🎉"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Marketplace", callback_data="marketplace_view")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
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


async def _get_queued_promotion_count(user_id: int) -> int:
    """Get the number of queued promotions for a user."""
    result = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM promotions WHERE user_id = ? AND status = 'pending'",
        (user_id,),
    )
    return result["cnt"] if result else 0


async def _show_insufficient_balance(query, item, balance) -> None:
    """Show insufficient balance message."""
    text = (
        f"❌ <b>Insufficient Balance</b>\n\n"
        f"📦 Item: {item['name']}\n"
        f"💰 Price: {item['price']:.2f} {CURRENCY_NAME}\n"
        f"💳 Your Balance: {balance:.2f} {CURRENCY_NAME}\n\n"
        f"You need <b>{item['price'] - balance:.2f} {CURRENCY_NAME}</b> more."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu")],
        [InlineKeyboardButton("🛒 Marketplace", callback_data="marketplace_view")],
    ])
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


def _get_category_emoji(category: str) -> str:
    """Return an emoji for a store category."""
    category_emojis = {
        "Channel Promotion": "📢",
        "Profile Pack": "👤",
        "Game Ownership": "🎮",
        "Private Rooms": "🔒",
        "Featured Slot": "⭐",
    }
    return category_emojis.get(category, "📦")
