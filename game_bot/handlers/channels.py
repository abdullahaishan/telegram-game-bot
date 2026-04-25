"""
Required Channels Enforcement

Handles checking, displaying, and verifying
user membership in required Telegram channels.
Blocks gameplay and actions for non-members.
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import REQUIRED_CHANNELS_ENABLED
from database import async_fetchall, async_fetchone

logger = logging.getLogger(__name__)


async def check_membership(user_id: int, bot) -> list:
    """
    Verify user is a member of all required channels.
    Returns a list of channels the user has NOT joined.
    If empty list, user has joined all channels.
    """
    if not REQUIRED_CHANNELS_ENABLED:
        return []

    channels = await async_fetchall(
        "SELECT channel_id, channel_username, channel_name FROM required_channels "
        "WHERE is_enabled = 1"
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
                "Could not verify membership for user %s in channel %s: %s",
                user_id, channel["channel_id"], e,
            )
            not_joined.append(channel)

    return not_joined


async def is_member_of_all_channels(user_id: int, bot) -> bool:
    """
    Quick check if user is a member of all required channels.
    Returns True if user has joined all channels (or if channels are disabled).
    """
    not_joined = await check_membership(user_id, bot)
    return len(not_joined) == 0


async def channels_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all channel-related callback queries."""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "check_membership":
        await _check_membership_callback(query, context)
    elif data == "show_required_channels":
        await _show_required_channels(query, context)
    else:
        await _show_required_channels(query, context)


async def verify_joined_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Re-check membership after user claims they joined all channels.
    If all joined, show main menu. Otherwise, re-display unjoined channels.
    """
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    not_joined = await check_membership(user_id, context.bot)

    if not not_joined:
        from game_bot.handlers.start import build_main_menu_keyboard
        from config import CURRENCY_NAME
        from database import async_fetchone

        wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
        balance = wallet["balance"] if wallet else 0.0

        first_name = update.effective_user.first_name or "Player"

        text = (
            f"✅ <b>Membership Verified!</b>\n\n"
            f"Welcome, <b>{first_name}</b>! 🎉\n\n"
            f"You now have full access to the game platform.\n"
            f"💰 Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n\n"
            f"Choose an option below to get started:"
        )

        reply_markup = build_main_menu_keyboard()

        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        text = (
            "⚠️ <b>Still Missing Channels</b>\n\n"
            "You haven't joined all required channels yet. "
            "Please join the following channels:\n\n"
        )
        for ch in not_joined:
            text += f"🔹 {ch['channel_name']}\n"

        text += "\nJoin all channels, then click the button below to verify again."

        keyboard = _build_channels_keyboard(not_joined)

        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _check_membership_callback(query, context) -> None:
    """Handle check_membership callback query."""
    user_id = query.from_user.id

    not_joined = await check_membership(user_id, context.bot)

    if not not_joined:
        from game_bot.handlers.start import build_main_menu_keyboard
        from config import CURRENCY_NAME
        from database import async_fetchone

        wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
        balance = wallet["balance"] if wallet else 0.0
        first_name = query.from_user.first_name or "Player"

        text = (
            f"✅ <b>All Channels Joined!</b>\n\n"
            f"Welcome, <b>{first_name}</b>! 🎉\n\n"
            f"💰 Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n\n"
            f"Choose an option below:"
        )

        reply_markup = build_main_menu_keyboard()

        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        text = (
            "⚠️ <b>Membership Check</b>\n\n"
            "You still need to join the following channels:\n\n"
        )
        for ch in not_joined:
            text += f"🔹 {ch['channel_name']}\n"

        text += "\nJoin all channels, then click verify."

        keyboard = _build_channels_keyboard(not_joined)

        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _show_required_channels(query, context) -> None:
    """Display the list of channels the user must join."""
    user_id = query.from_user.id

    channels = await async_fetchall(
        "SELECT channel_id, channel_username, channel_name, channel_description "
        "FROM required_channels WHERE is_enabled = 1"
    )

    if not channels:
        text = (
            "✅ <b>No Required Channels</b>\n\n"
            "There are currently no required channels. You have full access!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
        ])
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    not_joined = await check_membership(user_id, context.bot)

    if not not_joined:
        text = (
            "✅ <b>All Channels Joined</b>\n\n"
            "You are a member of all required channels. You have full access!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
        ])
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    text = (
        "📢 <b>Required Channels</b>\n\n"
        "You must join the following channels to use the game platform:\n\n"
    )

    for i, ch in enumerate(channels, 1):
        status = "❌" if any(nj["channel_id"] == ch["channel_id"] for nj in not_joined) else "✅"
        text += f"{status} {i}. <b>{ch['channel_name']}</b>\n"
        if ch.get("channel_description"):
            text += f"   📝 {ch['channel_description']}\n"
        text += f"   🔗 @{ch['channel_username'].lstrip('@')}\n\n"

    text += (
        f"\n⚠️ You have <b>{len(not_joined)}</b> channel(s) left to join.\n"
        "Join all channels, then click the verify button."
    )

    keyboard = _build_channels_keyboard(not_joined)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def enforce_channel_membership(user_id: int, bot) -> bool:
    """
    Check if a user meets channel membership requirements.
    Returns True if the user is allowed to proceed (all channels joined or feature disabled).
    Returns False if the user is blocked (hasn't joined all required channels).

    This function should be called before allowing gameplay or key actions.
    """
    return await is_member_of_all_channels(user_id, bot)


async def get_blocked_message(user_id: int, bot) -> tuple:
    """
    Get the block message and keyboard for a user who hasn't joined required channels.
    Returns (text, reply_markup) or (None, None) if user is allowed.
    """
    not_joined = await check_membership(user_id, bot)

    if not not_joined:
        return None, None

    text = (
        "⚠️ <b>Channel Membership Required</b>\n\n"
        "You must join all required channels before you can play games "
        "or perform this action.\n\n"
        "Missing channels:\n"
    )
    for ch in not_joined:
        text += f"🔹 {ch['channel_name']}\n"

    text += "\nJoin all channels, then verify your membership."

    keyboard = _build_channels_keyboard(not_joined)

    return text, keyboard


def _build_channels_keyboard(channels: list) -> InlineKeyboardMarkup:
    """Build inline keyboard with channel join buttons and verify button."""
    buttons = []
    for channel in channels:
        channel_username = channel["channel_username"].lstrip("@")
        channel_link = f"https://t.me/{channel_username}"
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
