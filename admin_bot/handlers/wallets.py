"""
Admin Wallets Handler
Wallet management: overview, detail, adjust balance, add/subtract SAR.
All adjustments recorded as transactions and logged to admin_logs.
"""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import ADMIN_IDS, CURRENCY_NAME
from database import async_fetchone, async_fetchall, async_execute, async_transaction
from admin_bot.utils import admin_guard, log_admin_action

# Conversation state
AWAITING_WALLET_ADJUST = "awaiting_wallet_adjust"

# Temp data keys
TEMP_WALLET_ADJUST = "temp_wallet_adjust"


def _back_to_dashboard_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🔙 Dashboard", callback_data="admin_dashboard")


def _back_to_wallets_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🔙 Wallets", callback_data="admin_wallets")


async def cb_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show wallet overview: total SAR, top balances."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "wallets_overview", details="Viewed wallet overview")

    # Total SAR in system
    total_row = await async_fetchone("SELECT COALESCE(SUM(balance), 0) as total FROM wallets")
    total_sar = total_row["total"] if total_row else 0

    # Number of wallets
    count_row = await async_fetchone("SELECT COUNT(*) as cnt FROM wallets")
    wallet_count = count_row["cnt"] if count_row else 0

    # Average balance
    avg_row = await async_fetchone("SELECT COALESCE(AVG(balance), 0) as avg_bal FROM wallets")
    avg_balance = avg_row["avg_bal"] if avg_row else 0

    # Top 10 balances
    top_wallets = await async_fetchall(
        """
        SELECT w.user_id, w.balance, u.username, u.first_name, u.telegram_id
        FROM wallets w
        JOIN users u ON w.user_id = u.id
        ORDER BY w.balance DESC
        LIMIT 10
        """
    )

    lines = [
        f"💰 *Wallet Overview*\n",
        f"━━━━━━━━━━━━━━━━━━",
        f"Total {CURRENCY_NAME}: `{total_sar:.2f}` SAR",
        f"Total Wallets: `{wallet_count}`",
        f"Average Balance: `{avg_balance:.2f}` SAR",
        f"\n🏆 *Top Balances:*",
    ]

    for i, w in enumerate(top_wallets, 1):
        name = w.get("first_name") or w.get("username") or str(w["telegram_id"])
        lines.append(f"  {i}. {name}: `{w['balance']:.2f}` SAR")

    text = "\n".join(lines)

    # Build buttons for top wallets
    detail_buttons = []
    for w in top_wallets[:5]:
        name = (w.get("first_name") or w.get("username") or str(w["telegram_id"]))[:15]
        detail_buttons.append(
            InlineKeyboardButton(f"💰 {name}", callback_data=f"admin_wallet_detail:{w['user_id']}")
        )

    detail_rows = [detail_buttons[i:i + 2] for i in range(0, len(detail_buttons), 2)]

    keyboard = InlineKeyboardMarkup(
        detail_rows + [[_back_to_dashboard_button()]]
    )

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_wallet_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show specific wallet details."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    user_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    await log_admin_action(
        admin_id, "wallet_detail", target_type="wallet", target_id=str(user_id)
    )

    wallet = await async_fetchone("SELECT * FROM wallets WHERE user_id = ?", (user_id,))
    user = await async_fetchone("SELECT * FROM users WHERE id = ?", (user_id,))

    if not user:
        await query.edit_message_text(
            "❌ User not found.",
            reply_markup=InlineKeyboardMarkup([[_back_to_wallets_button()]]),
        )
        return

    if not wallet:
        text = f"💰 *Wallet for* {user.get('first_name', 'Unknown')}\n\nNo wallet found."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Adjust Balance", callback_data=f"admin_wallet_adjust:{user_id}")],
            [_back_to_wallets_button(), _back_to_dashboard_button()],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    # Recent transactions
    recent_tx = await async_fetchall(
        """
        SELECT * FROM transactions
        WHERE user_id = ?
        ORDER BY created_at DESC LIMIT 10
        """,
        (user_id,),
    )

    tx_lines = []
    for tx in recent_tx:
        sign = "+" if tx["type"] in ("credit", "reward", "referral", "promotion", "admin_adjust") else "-"
        desc = tx.get("description", "") or ""
        tx_lines.append(f"  {sign}{tx['amount']:.2f} SAR — {tx['type']} {desc}")

    tx_text = "\n".join(tx_lines) if tx_lines else "  No recent transactions"

    text = (
        f"💰 *Wallet Details*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"User: {user.get('first_name', 'Unknown')} (@{user.get('username') or 'N/A'})\n"
        f"Telegram ID: `{user['telegram_id']}`\n"
        f"Balance: `{wallet['balance']:.2f}` SAR\n"
        f"Updated: {wallet.get('updated_at', 'N/A')}\n"
        f"\n📝 *Recent Transactions:*\n{tx_text}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Adjust", callback_data=f"admin_wallet_adjust:{user_id}"),
            InlineKeyboardButton("➕ Add 10", callback_data=f"admin_wallet_add:{user_id}:10"),
        ],
        [
            InlineKeyboardButton("➕ Add 50", callback_data=f"admin_wallet_add:{user_id}:50"),
            InlineKeyboardButton("➕ Add 100", callback_data=f"admin_wallet_add:{user_id}:100"),
        ],
        [
            InlineKeyboardButton("➖ Sub 10", callback_data=f"admin_wallet_subtract:{user_id}:10"),
            InlineKeyboardButton("➖ Sub 50", callback_data=f"admin_wallet_subtract:{user_id}:50"),
        ],
        [_back_to_wallets_button(), _back_to_dashboard_button()],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_wallet_adjust(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start wallet adjust conversation - ask for amount and reason."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return ConversationHandler.END

    await query.answer()
    user_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    wallet = await async_fetchone("SELECT * FROM wallets WHERE user_id = ?", (user_id,))
    user = await async_fetchone("SELECT * FROM users WHERE id = ?", (user_id,))

    if not user:
        await query.edit_message_text("❌ User not found.")
        return ConversationHandler.END

    current_balance = wallet["balance"] if wallet else 0

    context.user_data[TEMP_WALLET_ADJUST] = {
        "user_id": user_id,
        "admin_id": admin_id,
        "current_balance": current_balance,
    }

    text = (
        f"📊 *Adjust Wallet for* {user.get('first_name', 'Unknown')}\n"
        f"Current balance: `{current_balance:.2f}` SAR\n\n"
        f"Enter adjustment in format: `amount reason`\n"
        f"Examples:\n"
        f"• `+100 Bonus reward`\n"
        f"• `-25 Penalty for cheating`\n"
        f"• `=50 Reset balance`\n\n"
        f"Send /cancel to cancel."
    )

    await query.edit_message_text(text, parse_mode="Markdown")
    return AWAITING_WALLET_ADJUST


async def handle_wallet_adjust_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the wallet adjustment input with reason."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    admin_id = update.effective_user.id
    temp = context.user_data.get(TEMP_WALLET_ADJUST)
    if not temp:
        await update.message.reply_text("❌ Session expired. Try again.")
        return ConversationHandler.END

    user_id = temp["user_id"]
    current_balance = temp["current_balance"]

    input_text = update.message.text.strip()

    # Parse: first token is amount, rest is reason
    parts = input_text.split(None, 1)
    amount_str = parts[0]
    reason = parts[1] if len(parts) > 1 else "Admin adjustment"

    try:
        if amount_str.startswith("+"):
            amount = float(amount_str[1:])
            new_balance = current_balance + amount
            change_desc = f"Added {amount:.2f} SAR"
        elif amount_str.startswith("-"):
            amount = float(amount_str[1:])
            new_balance = current_balance - amount
            change_desc = f"Subtracted {amount:.2f} SAR"
        elif amount_str.startswith("="):
            new_balance = float(amount_str[1:])
            change_desc = f"Set to {new_balance:.2f} SAR"
        else:
            amount = float(amount_str)
            new_balance = current_balance + amount
            change_desc = f"Added {amount:.2f} SAR"

        if new_balance < 0:
            await update.message.reply_text("❌ Balance cannot be negative. Try again:")
            return AWAITING_WALLET_ADJUST

    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Use format: `amount reason`\nExample: `+100 Bonus`")
        return AWAITING_WALLET_ADJUST

    # Update wallet
    await async_execute("UPDATE wallets SET balance = ? WHERE user_id = ?", (new_balance, user_id))

    # Record transaction with reason
    diff = new_balance - current_balance
    tx_type = "admin_adjust" if diff > 0 else "admin_adjust"
    await async_execute(
        """
        INSERT INTO transactions (user_id, type, amount, description)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, tx_type, abs(diff), f"{reason} ({change_desc})"),
    )

    await log_admin_action(
        admin_id, "wallet_adjust", target_type="wallet", target_id=str(user_id),
        details=f"{change_desc} — Reason: {reason} (was {current_balance:.2f}, now {new_balance:.2f})"
    )

    context.user_data.pop(TEMP_WALLET_ADJUST, None)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Wallet Detail", callback_data=f"admin_wallet_detail:{user_id}")],
        [_back_to_wallets_button(), _back_to_dashboard_button()],
    ])

    await update.message.reply_text(
        f"✅ Wallet adjusted!\n\n"
        f"Previous: `{current_balance:.2f}` SAR\n"
        f"Change: {change_desc}\n"
        f"Reason: {reason}\n"
        f"New Balance: `{new_balance:.2f}` SAR",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

    return ConversationHandler.END


async def cb_wallet_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick-add SAR to a wallet."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    parts = query.data.split(":")
    user_id = int(parts[1])
    amount = float(parts[2])
    admin_id = update.effective_user.id

    wallet = await async_fetchone("SELECT * FROM wallets WHERE user_id = ?", (user_id,))
    user = await async_fetchone("SELECT * FROM users WHERE id = ?", (user_id,))

    if not user:
        await query.answer("❌ User not found.", show_alert=True)
        return

    if not wallet:
        await query.answer("❌ Wallet not found.", show_alert=True)
        return

    old_balance = wallet["balance"]
    new_balance = old_balance + amount

    # Update wallet
    await async_execute("UPDATE wallets SET balance = ? WHERE user_id = ?", (new_balance, user_id))

    # Record transaction
    await async_execute(
        """
        INSERT INTO transactions (user_id, type, amount, description)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, "admin_adjust", amount, f"Admin quick-add {amount:.2f} SAR"),
    )

    await log_admin_action(
        admin_id, "wallet_add", target_type="wallet", target_id=str(user_id),
        details=f"Added {amount:.2f} SAR (was {old_balance:.2f}, now {new_balance:.2f})"
    )

    await query.answer(f"✅ Added {amount:.2f} SAR", show_alert=True)

    # Refresh wallet detail view
    text = (
        f"✅ *SAR Added!*\n\n"
        f"User: {user.get('first_name', 'Unknown')}\n"
        f"Added: `+{amount:.2f}` SAR\n"
        f"New Balance: `{new_balance:.2f}` SAR"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Wallet Detail", callback_data=f"admin_wallet_detail:{user_id}")],
        [_back_to_wallets_button(), _back_to_dashboard_button()],
    ])

    try:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        pass


async def cb_wallet_subtract(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick-subtract SAR from a wallet."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    parts = query.data.split(":")
    user_id = int(parts[1])
    amount = float(parts[2])
    admin_id = update.effective_user.id

    wallet = await async_fetchone("SELECT * FROM wallets WHERE user_id = ?", (user_id,))
    user = await async_fetchone("SELECT * FROM users WHERE id = ?", (user_id,))

    if not user:
        await query.answer("❌ User not found.", show_alert=True)
        return

    if not wallet:
        await query.answer("❌ Wallet not found.", show_alert=True)
        return

    old_balance = wallet["balance"]
    new_balance = old_balance - amount

    if new_balance < 0:
        await query.answer("❌ Insufficient balance for this subtraction.", show_alert=True)
        return

    # Update wallet
    await async_execute("UPDATE wallets SET balance = ? WHERE user_id = ?", (new_balance, user_id))

    # Record transaction
    await async_execute(
        """
        INSERT INTO transactions (user_id, type, amount, description)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, "admin_adjust", amount, f"Admin quick-subtract {amount:.2f} SAR"),
    )

    await log_admin_action(
        admin_id, "wallet_subtract", target_type="wallet", target_id=str(user_id),
        details=f"Subtracted {amount:.2f} SAR (was {old_balance:.2f}, now {new_balance:.2f})"
    )

    await query.answer(f"✅ Subtracted {amount:.2f} SAR", show_alert=True)

    text = (
        f"✅ *SAR Subtracted!*\n\n"
        f"User: {user.get('first_name', 'Unknown')}\n"
        f"Subtracted: `-{amount:.2f}` SAR\n"
        f"New Balance: `{new_balance:.2f}` SAR"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Wallet Detail", callback_data=f"admin_wallet_detail:{user_id}")],
        [_back_to_wallets_button(), _back_to_dashboard_button()],
    ])

    try:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        pass


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the current conversation."""
    context.user_data.pop(TEMP_WALLET_ADJUST, None)
    await update.message.reply_text(
        "❌ Operation cancelled.",
        reply_markup=InlineKeyboardMarkup([[_back_to_dashboard_button()]]),
    )
    return ConversationHandler.END
