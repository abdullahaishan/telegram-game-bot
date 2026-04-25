"""
Wallet and Transaction Handlers

Handles wallet viewing, transaction history,
deposit info, and reward distribution for wins/shares.
"""

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    CURRENCY_NAME,
    WIN_REWARD,
    SHARE_REWARD,
    REWARD_CLAIM_COOLDOWN,
)
from database import async_execute, async_fetchone, async_fetchall, async_transaction

logger = logging.getLogger(__name__)

TRANSACTIONS_PER_PAGE = 5


async def award_game_win(user_id: int, game_name: str, bot=None) -> None:
    """
    Award WIN_REWARD SAR to a user who won a game.
    Updates wallet and records the transaction.
    """
    now = datetime.utcnow().isoformat()

    async with async_transaction():
        await async_execute(
            "UPDATE wallets SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
            (WIN_REWARD, now, user_id),
        )
        await async_execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, "game_win", WIN_REWARD,
             f"Won {game_name}", now),
        )

    if bot:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"🏆 <b>Victory!</b>\n\n"
                    f"You won <b>{WIN_REWARD} {CURRENCY_NAME}</b> for winning {game_name}!\n"
                    f"Keep up the great work! 🎉"
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.warning("Could not notify user %s about win.", user_id)


async def award_game_loss(user_id: int, game_name: str) -> None:
    """Record a game loss for statistics. No profile columns to update (wins/losses/games_played removed)."""
    pass


async def award_share_reward(user_id: int, bot=None) -> bool:
    """
    Award SHARE_REWARD SAR for verified sharing.
    Returns True if reward was given, False if on cooldown.
    """
    now = datetime.utcnow().isoformat()

    last_claim = await async_fetchone(
        "SELECT created_at FROM reward_claims "
        "WHERE user_id = ? AND claim_type = 'share' "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    )

    if last_claim:
        last_time = datetime.fromisoformat(last_claim["created_at"])
        cooldown_seconds = REWARD_CLAIM_COOLDOWN
        elapsed = (datetime.utcnow() - last_time).total_seconds()
        if elapsed < cooldown_seconds:
            remaining = int(cooldown_seconds - elapsed)
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            if bot:
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"⏳ <b>Share Reward Cooldown</b>\n\n"
                            f"You can claim the share reward again in "
                            f"<b>{hours}h {minutes}m</b>."
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            return False

    async with async_transaction():
        await async_execute(
            "UPDATE wallets SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
            (SHARE_REWARD, now, user_id),
        )
        await async_execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, "share_reward", SHARE_REWARD,
             "Reward for sharing the platform", now),
        )
        await async_execute(
            "INSERT INTO reward_claims (user_id, claim_type, amount, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, "share", SHARE_REWARD, now),
        )

    if bot:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"🎁 <b>Share Reward!</b>\n\n"
                    f"You earned <b>{SHARE_REWARD} {CURRENCY_NAME}</b> for sharing! 🎉"
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.warning("Could not notify user %s about share reward.", user_id)

    return True


async def wallet_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current SAR balance and recent transactions."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    if not wallet:
        await query.answer("Wallet not found. Please use /start to register.", show_alert=True)
        return

    balance = wallet["balance"]

    recent_tx = await async_fetchall(
        "SELECT type, amount, description, created_at FROM transactions "
        "WHERE user_id = ? ORDER BY created_at DESC LIMIT 5",
        (user_id,),
    )

    total_in = await async_fetchone(
        "SELECT COALESCE(SUM(amount), 0) as total FROM transactions "
        "WHERE user_id = ? AND amount > 0",
        (user_id,),
    )
    total_out = await async_fetchone(
        "SELECT COALESCE(SUM(ABS(amount)), 0) as total FROM transactions "
        "WHERE user_id = ? AND amount < 0",
        (user_id,),
    )

    text = (
        f"💰 <b>Wallet</b>\n\n"
        f"┌─────────────────────┐\n"
        f"│ Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n"
        f"│ Total In: <b>+{total_in['total']:.2f}</b>\n"
        f"│ Total Out: <b>-{total_out['total']:.2f}</b>\n"
        f"└─────────────────────┘\n\n"
    )

    if recent_tx:
        text += "📊 <b>Recent Transactions:</b>\n"
        for tx in recent_tx:
            sign = "+" if tx["amount"] > 0 else ""
            tx_type_emoji = _get_type_emoji(tx["type"])
            created = tx["created_at"][:16] if tx["created_at"] else "N/A"
            text += (
                f"  {tx_type_emoji} {sign}{tx['amount']:.2f} — "
                f"{tx['description'][:25]}\n"
                f"    📅 {created}\n"
            )
    else:
        text += "No transactions yet.\n"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📜 Full History", callback_data="wallet_history:1"),
            InlineKeyboardButton("📥 Deposit Info", callback_data="wallet_deposit_info"),
        ],
        [
            InlineKeyboardButton("🎁 Claim Share Reward", callback_data="wallet_claim_share"),
        ],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def wallet_history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show full transaction history (paginated)."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    try:
        page = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        page = 1

    offset = (page - 1) * TRANSACTIONS_PER_PAGE

    total_count = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM transactions WHERE user_id = ?",
        (user_id,),
    )
    count = total_count["cnt"] if total_count else 0

    transactions = await async_fetchall(
        "SELECT type, amount, description, created_at FROM transactions "
        "WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (user_id, TRANSACTIONS_PER_PAGE, offset),
    )

    total_pages = max(1, (count + TRANSACTIONS_PER_PAGE - 1) // TRANSACTIONS_PER_PAGE)

    text = (
        f"📜 <b>Transaction History</b> (Page {page}/{total_pages})\n\n"
    )

    if transactions:
        for tx in transactions:
            sign = "+" if tx["amount"] > 0 else ""
            tx_type_emoji = _get_type_emoji(tx["type"])
            created = tx["created_at"][:16] if tx["created_at"] else "N/A"
            text += (
                f"{tx_type_emoji} {sign}{tx['amount']:.2f} {CURRENCY_NAME}\n"
                f"  {tx['description'][:35]}\n"
                f"  📅 {created}\n\n"
            )
    else:
        text += "No transactions found.\n"

    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton("⬅ Previous", callback_data=f"wallet_history:{page - 1}")
        )
    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton("➡ Next", callback_data=f"wallet_history:{page + 1}")
        )

    keyboard_rows = []
    if nav_buttons:
        keyboard_rows.append(nav_buttons)
    keyboard_rows.append([
        InlineKeyboardButton("💰 Wallet", callback_data="wallet_view"),
        InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def wallet_deposit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show deposit instructions."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    text = (
        "📥 <b>Deposit Instructions</b>\n\n"
        "To add funds to your wallet, please follow these steps:\n\n"
        "1️⃣ Contact the admin via the support channel\n"
        "2️⃣ Specify the amount you want to deposit\n"
        "3️⃣ Complete the payment via the provided method\n"
        "4️⃣ Your balance will be updated after confirmation\n\n"
        f"💱 Currency: <b>{CURRENCY_NAME}</b>\n\n"
        "⚠️ <i>Deposits are manually processed. Please allow "
        "some time for confirmation.</i>\n\n"
        f"💡 You can also earn {CURRENCY_NAME} by:\n"
        f"  🏆 Winning games (+{WIN_REWARD} {CURRENCY_NAME})\n"
        f"  📢 Sharing the platform (+{SHARE_REWARD} {CURRENCY_NAME})\n"
        f"  👥 Referring friends (bonus per referral)"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Wallet", callback_data="wallet_view")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def wallet_claim_share_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle share reward claim."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    success = await award_share_reward(user_id, context.bot)

    if success:
        wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
        balance = wallet["balance"] if wallet else 0.0

        text = (
            f"✅ <b>Share Reward Claimed!</b>\n\n"
            f"You received <b>{SHARE_REWARD} {CURRENCY_NAME}</b>!\n"
            f"💰 New Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n\n"
            f"Share again after the cooldown to earn more!"
        )
    else:
        text = (
            "⏳ <b>Share Reward on Cooldown</b>\n\n"
            "You've recently claimed the share reward. "
            "Please wait for the cooldown period to end."
        )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Wallet", callback_data="wallet_view")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


def _get_type_emoji(tx_type: str) -> str:
    """Return an emoji for a transaction type."""
    type_emojis = {
        "game_win": "🏆",
        "game_entry": "🎮",
        "refund": "↩️",
        "share_reward": "📢",
        "referral_bonus": "👥",
        "purchase": "🛒",
        "withdrawal": "💸",
        "deposit": "📥",
        "promotion": "📢",
        "admin_adjust": "⚙️",
    }
    return type_emojis.get(tx_type, "💰")
