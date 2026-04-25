"""
Start Command Handler & User Registration

Handles /start command, new user registration,
referral code processing, channel membership checks,
and main menu display.
"""

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    CURRENCY_NAME,
    REFERRAL_BONUS,
    REQUIRED_CHANNELS_ENABLED,
)
from database import async_execute, async_fetchone, async_fetchall, async_transaction
from game_bot.handlers.menu import build_main_menu_keyboard

logger = logging.getLogger(__name__)


async def _get_internal_user_id(telegram_id: int):
    """Resolve a telegram_id to the internal users.id. Returns None if not found."""
    row = await async_fetchone("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    return row["id"] if row else None


async def check_required_channels(user_id: int, bot) -> list:
    """
    Check if a user is a member of all required channels.
    Returns a list of channels the user has NOT joined.
    """
    if not REQUIRED_CHANNELS_ENABLED:
        return []

    channels = await async_fetchall(
        "SELECT channel_id, channel_username, channel_name FROM required_channels WHERE is_enabled = 1"
    )
    if not channels:
        return []

    not_joined = []
    for channel in channels:
        try:
            member = await bot.get_chat_member(
                chat_id=channel["channel_id"],
                user_id=user_id,
            )
            if member.status in ("left", "kicked"):
                not_joined.append(channel)
        except Exception as e:
            logger.warning(
                "Could not check membership for channel %s, user %s: %s",
                channel["channel_id"], user_id, e,
            )
            not_joined.append(channel)

    return not_joined


async def register_user(user_id: int, username: str, first_name: str, last_name: str) -> bool:
    """
    Register a new user with wallet and profile.
    Returns True if user was newly created, False if already exists.
    """
    existing = await async_fetchone("SELECT telegram_id FROM users WHERE telegram_id = ?", (user_id,))
    if existing:
        return False

    async with async_transaction():
        cursor = await async_execute(
            "INSERT INTO users (telegram_id, username, first_name, joined_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, username or "", first_name or "", datetime.utcnow().isoformat()),
        )
        internal_id = cursor.lastrowid
        await async_execute(
            "INSERT INTO wallets (user_id, balance, updated_at) VALUES (?, 0.0, ?)",
            (internal_id, datetime.utcnow().isoformat()),
        )
        await async_execute(
            "INSERT INTO profiles (user_id, title, badge, created_at) "
            "VALUES (?, ?, ?, ?)",
            (internal_id, "New Player", "🟢", datetime.utcnow().isoformat()),
        )

    return True


async def process_referral(referrer_code: str, new_user_id: int, bot) -> None:
    """
    Process a referral code: find the referrer, grant bonuses to both.
    The referral code format is the referrer's user_id encoded.
    """
    try:
        referrer_telegram_id = int(referrer_code)
    except ValueError:
        logger.warning("Invalid referral code: %s", referrer_code)
        return

    if referrer_telegram_id == new_user_id:
        logger.warning("User %s tried to use own referral code.", new_user_id)
        return

    referrer = await async_fetchone("SELECT id FROM users WHERE telegram_id = ?", (referrer_telegram_id,))
    if not referrer:
        logger.warning("Referrer %s not found.", referrer_telegram_id)
        return

    referrer_internal_id = referrer["id"]

    new_user = await async_fetchone("SELECT id FROM users WHERE telegram_id = ?", (new_user_id,))
    if not new_user:
        logger.warning("New user %s not found for referral.", new_user_id)
        return

    new_user_internal_id = new_user["id"]

    existing_referral = await async_fetchone(
        "SELECT id FROM referrals WHERE referrer_id = ? AND referred_id = ?",
        (referrer_internal_id, new_user_internal_id),
    )
    if existing_referral:
        return

    async with async_transaction():
        await async_execute(
            "INSERT INTO referrals (referrer_id, referred_id, reward_given, created_at) "
            "VALUES (?, ?, ?, ?)",
            (referrer_internal_id, new_user_internal_id, 1, datetime.utcnow().isoformat()),
        )
        await async_execute(
            "UPDATE wallets SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
            (REFERRAL_BONUS, datetime.utcnow().isoformat(), referrer_internal_id),
        )
        await async_execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (referrer_internal_id, "referral_bonus", REFERRAL_BONUS,
             f"Referral bonus for inviting user {new_user_id}", datetime.utcnow().isoformat()),
        )
        await async_execute(
            "UPDATE wallets SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
            (REFERRAL_BONUS, datetime.utcnow().isoformat(), new_user_internal_id),
        )
        await async_execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (new_user_internal_id, "referral_bonus", REFERRAL_BONUS,
             "Bonus for using referral code", datetime.utcnow().isoformat()),
        )

    try:
        await bot.send_message(
            chat_id=referrer_telegram_id,
            text=f"🎉 <b>Referral Bonus!</b>\n\n"
                 f"You earned <b>{REFERRAL_BONUS} {CURRENCY_NAME}</b> for inviting a new player!",
            parse_mode="HTML",
        )
    except Exception:
        logger.warning("Could not notify referrer %s.", referrer_telegram_id)


# build_main_menu_keyboard is imported from game_bot.handlers.menu to avoid duplication


def build_channels_keyboard(channels: list) -> InlineKeyboardMarkup:
    """Build keyboard for channels the user must join."""
    buttons = []
    for channel in channels:
        channel_link = f"https://t.me/{channel['channel_username'].lstrip('@')}"
        buttons.append([
            InlineKeyboardButton(
                f"Join {channel['channel_name']}",
                url=channel_link,
            )
        ])
    buttons.append([
        InlineKeyboardButton("✅ I've Joined All Channels", callback_data="verify_joined"),
    ])
    return InlineKeyboardMarkup(buttons)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command without referral code."""
    await _handle_start(update, context, referral_code=None)


async def referral_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command with optional referral code."""
    referral_code = None
    if context.args and len(context.args) > 0:
        referral_code = context.args[0]
        if referral_code.startswith("ref_"):
            referral_code = referral_code[4:]

    await _handle_start(update, context, referral_code)


async def _handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE, referral_code: str = None) -> None:
    """Core start logic: register user, check channels, show menu."""
    user = update.effective_user
    user_id = user.id
    username = user.username or ""
    first_name = user.first_name or ""
    last_name = user.last_name or ""

    is_new = await register_user(user_id, username, first_name, last_name)

    if is_new and referral_code:
        await process_referral(referral_code, user_id, context.bot)

    not_joined = await check_required_channels(user_id, context.bot)
    if not_joined:
        channels_text = "⚠️ <b>Join Required Channels First</b>\n\n"
        channels_text += "You must join the following channels before you can use the game platform:\n\n"
        for ch in not_joined:
            channels_text += f"🔹 {ch['channel_name']}\n"

        channels_text += "\nJoin all channels, then click the button below to verify."

        reply_markup = build_channels_keyboard(not_joined)

        if update.message:
            await update.message.reply_text(
                channels_text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        return

    internal_id = await _get_internal_user_id(user_id)
    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (internal_id,))
    balance = wallet["balance"] if wallet else 0.0

    welcome_text = (
        f"🎮 <b>Welcome to the Game Platform!</b>\n\n"
        f"Hello, <b>{first_name}</b>!\n\n"
        f"💰 Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n"
        f"🔗 Your referral code: <code>ref_{user_id}</code>\n\n"
        f"Choose an option below to get started:"
    )

    if is_new:
        welcome_text = (
            f"🎉 <b>Welcome aboard, {first_name}!</b>\n\n"
            f"Your account has been created successfully!\n\n"
            f"💰 Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n"
            f"🔗 Your referral code: <code>ref_{user_id}</code>\n"
            f"   Share it to earn <b>{REFERRAL_BONUS} {CURRENCY_NAME}</b> per referral!\n\n"
            f"Choose an option below to get started:"
        )

    reply_markup = build_main_menu_keyboard()

    if update.message:
        await update.message.reply_text(
            welcome_text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
