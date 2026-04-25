"""
Withdrawal Handlers

Handles withdrawal flow including amount setting,
method selection, detail entry, confirmation,
status checking, and validation.
"""

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    CURRENCY_NAME,
    WITHDRAWAL_MIN_SAR,
    WITHDRAWAL_METHODS,
)
from database import async_execute, async_fetchone, async_fetchall, async_transaction

logger = logging.getLogger(__name__)


WITHDRAWAL_STATES = {
    "awaiting_amount": "withdraw_awaiting_amount",
    "awaiting_method": "withdraw_awaiting_method",
    "awaiting_details": "withdraw_awaiting_details",
    "awaiting_confirm": "withdraw_awaiting_confirm",
}


async def withdrawals_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all withdrawal-related callback queries."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = update.effective_user.id

    if data == "withdraw_start":
        await _start_withdrawal(query, context, user_id)
    elif data == "withdraw_set_amount":
        await _prompt_amount(query, context, user_id)
    elif data.startswith("withdraw_amount:"):
        amount_str = data.split(":", 1)[1]
        await _set_amount(query, context, user_id, amount_str)
    elif data == "withdraw_set_method":
        await _show_method_selection(query, context, user_id)
    elif data.startswith("withdraw_method:"):
        method = data.split(":", 1)[1]
        await _set_method(query, context, user_id, method)
    elif data == "withdraw_set_details":
        await _prompt_details(query, context, user_id)
    elif data == "withdraw_confirm":
        await _confirm_withdrawal(query, context, user_id)
    elif data == "withdraw_status":
        await _show_withdrawal_status(query, context, user_id)
    elif data == "withdraw_cancel":
        await _cancel_withdrawal_flow(query, context, user_id)
    else:
        await _start_withdrawal(query, context, user_id)


async def withdrawal_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle text messages during withdrawal flow.
    This acts as a simple conversation handler for amount and detail entry.
    """
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # Check for promotion flow messages first
    from game_bot.handlers.promotions import handle_promotion_message
    if await handle_promotion_message(update, context):
        return

    state = context.user_data.get("withdrawal_state")

    if state == "awaiting_amount":
        await _process_amount_input(update, context, user_id, text)
    elif state == "awaiting_details":
        await _process_details_input(update, context, user_id, text)
    # If no withdrawal state, ignore (message is not for us)


async def _start_withdrawal(query, context, user_id) -> None:
    """Start the withdrawal flow."""
    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if balance < WITHDRAWAL_MIN_SAR:
        text = (
            f"❌ <b>Insufficient Balance for Withdrawal</b>\n\n"
            f"💰 Your Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n"
            f"📋 Minimum Withdrawal: <b>{WITHDRAWAL_MIN_SAR} {CURRENCY_NAME}</b>\n\n"
            f"You need at least <b>{WITHDRAWAL_MIN_SAR:.2f} {CURRENCY_NAME}</b> to withdraw."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    pending = await async_fetchall(
        "SELECT id, amount, method, status, created_at FROM withdrawals "
        "WHERE user_id = ? AND status = 'pending'",
        (user_id,),
    )

    if pending:
        text = (
            f"⏳ <b>Pending Withdrawals</b>\n\n"
            f"You have {len(pending)} pending withdrawal(s):\n\n"
        )
        for w in pending:
            created = w["created_at"][:16] if w["created_at"] else "N/A"
            text += (
                f"  💸 {w['amount']:.2f} {CURRENCY_NAME} via {w['method']}\n"
                f"     Status: {w['status']} | Date: {created}\n"
            )
        text += (
            "\nPlease wait for your pending withdrawals to be processed "
            "before creating a new one."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Withdrawal Status", callback_data="withdraw_status")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    context.user_data["withdrawal_state"] = None
    context.user_data["withdrawal_amount"] = None
    context.user_data["withdrawal_method"] = None
    context.user_data["withdrawal_details"] = None

    text = (
        f"💸 <b>Withdraw Funds</b>\n\n"
        f"💰 Your Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n"
        f"📋 Minimum: <b>{WITHDRAWAL_MIN_SAR} {CURRENCY_NAME}</b>\n"
        f"💳 Methods: {', '.join(WITHDRAWAL_METHODS)}\n\n"
        f"Choose an amount to withdraw:"
    )

    quick_amounts = [WITHDRAWAL_MIN_SAR, 50, 100, 200, 500]
    quick_amounts = [a for a in quick_amounts if a <= balance]
    if not quick_amounts:
        quick_amounts = [WITHDRAWAL_MIN_SAR]

    keyboard_rows = []
    for i in range(0, len(quick_amounts), 2):
        row = []
        for j in range(2):
            if i + j < len(quick_amounts):
                amount = quick_amounts[i + j]
                row.append(InlineKeyboardButton(
                    f"{amount} {CURRENCY_NAME}",
                    callback_data=f"withdraw_amount:{amount}",
                ))
        keyboard_rows.append(row)

    keyboard_rows.append([
        InlineKeyboardButton("✏️ Custom Amount", callback_data="withdraw_set_amount"),
    ])
    keyboard_rows.append([
        InlineKeyboardButton("❌ Cancel", callback_data="back_to_main"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _prompt_amount(query, context, user_id) -> None:
    """Prompt user to type a custom amount."""
    context.user_data["withdrawal_state"] = "awaiting_amount"

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    text = (
        f"✏️ <b>Enter Withdrawal Amount</b>\n\n"
        f"💰 Your Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n"
        f"📋 Minimum: <b>{WITHDRAWAL_MIN_SAR} {CURRENCY_NAME}</b>\n\n"
        f"Please type the amount you want to withdraw:"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="withdraw_cancel")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _process_amount_input(update, context, user_id, text) -> None:
    """Process the custom amount entered by the user."""
    try:
        amount = float(text.replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. Please enter a valid number."
        )
        return

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if amount < WITHDRAWAL_MIN_SAR:
        await update.message.reply_text(
            f"❌ Minimum withdrawal is <b>{WITHDRAWAL_MIN_SAR} {CURRENCY_NAME}</b>.\n"
            f"Please enter a higher amount.",
            parse_mode="HTML",
        )
        return

    if amount > balance:
        await update.message.reply_text(
            f"❌ Insufficient balance.\n"
            f"Your balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n"
            f"Requested: <b>{amount:.2f} {CURRENCY_NAME}</b>",
            parse_mode="HTML",
        )
        return

    context.user_data["withdrawal_amount"] = amount
    context.user_data["withdrawal_state"] = None

    await _show_method_selection_message(update, context, user_id)


async def _set_amount(query, context, user_id, amount_str) -> None:
    """Set a quick-select withdrawal amount."""
    try:
        amount = float(amount_str)
    except ValueError:
        await query.answer("Invalid amount.", show_alert=True)
        return

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if amount < WITHDRAWAL_MIN_SAR:
        await query.answer(f"Minimum withdrawal is {WITHDRAWAL_MIN_SAR} {CURRENCY_NAME}.", show_alert=True)
        return

    if amount > balance:
        await query.answer(
            f"Insufficient balance. You have {balance:.2f} {CURRENCY_NAME}.",
            show_alert=True,
        )
        return

    context.user_data["withdrawal_amount"] = amount
    context.user_data["withdrawal_state"] = None

    await _show_method_selection_callback(query, context, user_id)


async def _show_method_selection(query, context, user_id) -> None:
    """Show method selection (from callback)."""
    if "withdrawal_amount" not in context.user_data or context.user_data["withdrawal_amount"] is None:
        await query.answer("Please set an amount first.", show_alert=True)
        return

    await _show_method_selection_callback(query, context, user_id)


async def _show_method_selection_callback(query, context, user_id) -> None:
    """Show method selection via callback query."""
    amount = context.user_data["withdrawal_amount"]

    text = (
        f"💳 <b>Select Withdrawal Method</b>\n\n"
        f"💸 Amount: <b>{amount:.2f} {CURRENCY_NAME}</b>\n\n"
        f"Choose your preferred method:"
    )

    keyboard_rows = []
    for method in WITHDRAWAL_METHODS:
        method_emoji = _get_method_emoji(method)
        keyboard_rows.append([
            InlineKeyboardButton(
                f"{method_emoji} {method}",
                callback_data=f"withdraw_method:{method}",
            )
        ])

    keyboard_rows.append([
        InlineKeyboardButton("❌ Cancel", callback_data="withdraw_cancel"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _show_method_selection_message(update, context, user_id) -> None:
    """Show method selection via message (from text input flow)."""
    amount = context.user_data["withdrawal_amount"]

    text = (
        f"✅ Amount set: <b>{amount:.2f} {CURRENCY_NAME}</b>\n\n"
        f"💳 <b>Select Withdrawal Method:</b>"
    )

    keyboard_rows = []
    for method in WITHDRAWAL_METHODS:
        method_emoji = _get_method_emoji(method)
        keyboard_rows.append([
            InlineKeyboardButton(
                f"{method_emoji} {method}",
                callback_data=f"withdraw_method:{method}",
            )
        ])

    keyboard_rows.append([
        InlineKeyboardButton("❌ Cancel", callback_data="withdraw_cancel"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _set_method(query, context, user_id, method) -> None:
    """Set the withdrawal method and prompt for details."""
    if method not in WITHDRAWAL_METHODS:
        await query.answer("Invalid method.", show_alert=True)
        return

    context.user_data["withdrawal_method"] = method
    context.user_data["withdrawal_state"] = "awaiting_details"

    method_instructions = _get_method_instructions(method)
    amount = context.user_data["withdrawal_amount"]

    text = (
        f"💳 <b>Method: {method}</b>\n"
        f"💸 Amount: <b>{amount:.2f} {CURRENCY_NAME}</b>\n\n"
        f"📝 <b>Enter your account details:</b>\n\n"
        f"{method_instructions}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="withdraw_cancel")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _prompt_details(query, context, user_id) -> None:
    """Re-prompt for account details."""
    method = context.user_data.get("withdrawal_method")
    amount = context.user_data.get("withdrawal_amount")

    if not method or not amount:
        await query.answer("Missing withdrawal info. Please start over.", show_alert=True)
        context.user_data["withdrawal_state"] = None
        return

    context.user_data["withdrawal_state"] = "awaiting_details"

    method_instructions = _get_method_instructions(method)

    text = (
        f"💳 <b>Method: {method}</b>\n"
        f"💸 Amount: <b>{amount:.2f} {CURRENCY_NAME}</b>\n\n"
        f"📝 <b>Enter your account details:</b>\n\n"
        f"{method_instructions}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="withdraw_cancel")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _process_details_input(update, context, user_id, text) -> None:
    """Process the account details entered by the user."""
    if len(text) < 3:
        await update.message.reply_text(
            "❌ Account details too short. Please enter valid account information."
        )
        return

    if len(text) > 500:
        await update.message.reply_text(
            "❌ Account details too long. Maximum 500 characters."
        )
        return

    context.user_data["withdrawal_details"] = text
    context.user_data["withdrawal_state"] = "awaiting_confirm"

    amount = context.user_data["withdrawal_amount"]
    method = context.user_data["withdrawal_method"]

    text_display = (
        f"📋 <b>Withdrawal Summary</b>\n\n"
        f"💸 Amount: <b>{amount:.2f} {CURRENCY_NAME}</b>\n"
        f"💳 Method: <b>{method}</b>\n"
        f"📝 Details: <code>{text}</code>\n\n"
        f"⚠️ Please verify all details are correct.\n"
        f"Once confirmed, the amount will be deducted from your wallet.\n\n"
        f"Do you want to proceed?"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="withdraw_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="withdraw_cancel"),
        ],
    ])

    await update.message.reply_text(text_display, parse_mode="HTML", reply_markup=keyboard)


async def _confirm_withdrawal(query, context, user_id) -> None:
    """Confirm and submit the withdrawal request."""
    amount = context.user_data.get("withdrawal_amount")
    method = context.user_data.get("withdrawal_method")
    details = context.user_data.get("withdrawal_details")

    if not amount or not method or not details:
        await query.answer("Missing withdrawal information. Please start over.", show_alert=True)
        context.user_data["withdrawal_state"] = None
        return

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if balance < amount:
        text = (
            f"❌ <b>Insufficient Balance</b>\n\n"
            f"Your balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n"
            f"Requested: <b>{amount:.2f} {CURRENCY_NAME}</b>\n\n"
            f"The withdrawal could not be processed."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        _clear_withdrawal_state(context)
        return

    if amount < WITHDRAWAL_MIN_SAR:
        await query.answer(
            f"Minimum withdrawal is {WITHDRAWAL_MIN_SAR} {CURRENCY_NAME}.",
            show_alert=True,
        )
        return

    now = datetime.utcnow().isoformat()

    async with async_transaction():
        await async_execute(
            "UPDATE wallets SET balance = balance - ?, updated_at = ? WHERE user_id = ?",
            (amount, now, user_id),
        )
        await async_execute(
            "INSERT INTO transactions (user_id, type, amount, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, "withdrawal", -amount,
             f"Withdrawal via {method}", now),
        )
        cursor = await async_execute(
            "INSERT INTO withdrawals (user_id, amount, method, account_details, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (user_id, amount, method, details, now),
        )
        withdrawal_id = cursor.lastrowid

    updated_wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    updated_balance = updated_wallet["balance"] if updated_wallet else 0.0

    text = (
        f"✅ <b>Withdrawal Request Submitted!</b>\n\n"
        f"🆔 Request ID: <code>W-{withdrawal_id}</code>\n"
        f"💸 Amount: <b>{amount:.2f} {CURRENCY_NAME}</b>\n"
        f"💳 Method: <b>{method}</b>\n"
        f"📝 Details: <code>{details}</code>\n"
        f"📊 Status: <b>Pending</b>\n\n"
        f"💰 Remaining Balance: <b>{updated_balance:.2f} {CURRENCY_NAME}</b>\n\n"
        f"⏳ Your withdrawal will be processed by an admin. "
        f"You'll be notified once it's completed."
    )

    _clear_withdrawal_state(context)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Withdrawal Status", callback_data="withdraw_status")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _show_withdrawal_status(query, context, user_id) -> None:
    """Show all withdrawal requests and their statuses."""
    withdrawals = await async_fetchall(
        "SELECT id, amount, method, status, admin_note, created_at, reviewed_at "
        "FROM withdrawals WHERE user_id = ? "
        "ORDER BY created_at DESC LIMIT 10",
        (user_id,),
    )

    text = "📊 <b>Withdrawal History</b>\n\n"

    if not withdrawals:
        text += "No withdrawal requests found."
    else:
        status_emojis = {
            "pending": "⏳",
            "approved": "✅",
            "completed": "✅",
            "rejected": "❌",
            "cancelled": "🚫",
        }
        for w in withdrawals:
            emoji = status_emojis.get(w["status"], "❓")
            created = w["created_at"][:16] if w["created_at"] else "N/A"
            processed = w["reviewed_at"][:16] if w["reviewed_at"] else "N/A"

            text += (
                f"{emoji} <b>W-{w['id']}</b>\n"
                f"  💸 {w['amount']:.2f} {CURRENCY_NAME} via {w['method']}\n"
                f"  📊 Status: <b>{w['status'].title()}</b>\n"
                f"  📅 Created: {created}\n"
            )
            if w["status"] in ("completed", "approved", "rejected"):
                text += f"  📅 Processed: {processed}\n"
            if w["admin_note"]:
                text += f"  📝 Note: {w['admin_note']}\n"
            text += "\n"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 New Withdrawal", callback_data="withdraw_start")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _cancel_withdrawal_flow(query, context, user_id) -> None:
    """Cancel the current withdrawal flow."""
    _clear_withdrawal_state(context)

    text = "❌ <b>Withdrawal Cancelled</b>\n\nNo funds have been deducted."

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Try Again", callback_data="withdraw_start")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


def _clear_withdrawal_state(context) -> None:
    """Clear all withdrawal-related user data."""
    context.user_data.pop("withdrawal_state", None)
    context.user_data.pop("withdrawal_amount", None)
    context.user_data.pop("withdrawal_method", None)
    context.user_data.pop("withdrawal_details", None)


def _get_method_emoji(method: str) -> str:
    """Return an emoji for a withdrawal method."""
    method_emojis = {
        "Western Union": "🏦",
        "PayPal": "🅿️",
        "Crypto": "₿",
        "Bank Transfer": "🏧",
        "USDT": "💲",
        "BTC": "₿",
        "ETH": "⟠",
    }
    return method_emojis.get(method, "💳")


def _get_method_instructions(method: str) -> str:
    """Return instructions for entering account details for each method."""
    instructions = {
        "Western Union": (
            "Please provide:\n"
            "• Full name (as on ID)\n"
            "• Country\n"
            "• City\n\n"
            "Example: John Doe, Saudi Arabia, Riyadh"
        ),
        "PayPal": (
            "Please provide:\n"
            "• PayPal email address\n\n"
            "Example: john@example.com"
        ),
        "Crypto": (
            "Please provide:\n"
            "• Cryptocurrency (USDT/BTC/ETH)\n"
            "• Wallet address\n"
            "• Network (TRC20/ERC20/BEP20)\n\n"
            "Example: USDT, TXrkRn...k3Q, TRC20"
        ),
        "Bank Transfer": (
            "Please provide:\n"
            "• Bank name\n"
            "• Account number\n"
            "• Account holder name\n"
            "• IBAN/Swift code\n\n"
            "Example: Al Rajhi Bank, 1234567890, John Doe, SA0380000000608010167519"
        ),
    }

    for key in instructions:
        if key.lower() in method.lower():
            return instructions[key]

    return (
        "Please provide your account details for the selected method.\n"
        "Include all necessary information for processing."
    )
