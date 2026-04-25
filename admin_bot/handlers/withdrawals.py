"""
Admin Withdrawals Handler
Withdrawal management: list pending, detail, approve, reject (with reason), add note, history.
When approving, sends "We will contact you" message to user via Game Bot.
"""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import ADMIN_IDS, GAME_BOT_TOKEN, WITHDRAWAL_MIN_SAR, WITHDRAWAL_METHODS, CURRENCY_NAME
from database import async_fetchone, async_fetchall, async_execute
from admin_bot.utils import admin_guard, log_admin_action

# Conversation states
AWAITING_WITHDRAWAL_NOTE = "awaiting_withdrawal_note"
AWAITING_REJECTION_REASON = "awaiting_rejection_reason"

# Temp data keys
TEMP_WITHDRAWAL_NOTE = "temp_withdrawal_note"
TEMP_REJECTION = "temp_rejection"


def _back_to_dashboard_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🔙 Dashboard", callback_data="admin_dashboard")


def _back_to_withdrawals_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("💸 Withdrawals", callback_data="admin_withdrawals")


async def _send_game_bot_message(user_telegram_id: int, text: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Send a message to a user via the Game Bot."""
    try:
        from telegram import Bot
        bot = Bot(token=GAME_BOT_TOKEN)
        await bot.send_message(chat_id=user_telegram_id, text=text)
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to send Game Bot message: {e}")
        return False


async def cb_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List pending withdrawals."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "withdrawals_list", details="Viewed pending withdrawals")

    withdrawals = await async_fetchall(
        """
        SELECT w.*, u.username, u.first_name, u.telegram_id
        FROM withdrawals w
        JOIN users u ON w.user_id = u.id
        WHERE w.status = 'pending'
        ORDER BY w.created_at ASC
        """
    )

    # Summary stats
    total_pending = await async_fetchone(
        "SELECT COALESCE(SUM(amount), 0) as total FROM withdrawals WHERE status = 'pending'"
    )
    pending_total = total_pending["total"] if total_pending else 0

    lines = [
        f"💸 *Pending Withdrawals*\n",
        f"━━━━━━━━━━━━━━━━━━",
        f"Count: {len(withdrawals)}",
        f"Total Amount: `{pending_total:.2f}` SAR\n",
    ]

    if not withdrawals:
        lines.append("No pending withdrawals.")
    else:
        for wd in withdrawals:
            name = wd.get("first_name") or wd.get("username") or str(wd["telegram_id"])
            method = wd.get("method", "unknown")
            lines.append(
                f"📋 #{wd['id']} — {name}\n"
                f"  Amount: {wd['amount']:.2f} SAR | Method: {method}\n"
                f"  Account: {wd.get('account_details', 'N/A')}\n"
                f"  Requested: {wd.get('created_at', 'N/A')}"
            )

    text = "\n".join(lines)

    # Build buttons for each withdrawal
    wd_buttons = []
    for wd in withdrawals[:10]:
        name = (wd.get("first_name") or wd.get("username") or str(wd["telegram_id"]))[:15]
        wd_buttons.append(
            InlineKeyboardButton(
                f"💰 #{wd['id']} {name} ({wd['amount']:.0f})",
                callback_data=f"admin_withdrawal_detail:{wd['id']}",
            )
        )

    wd_rows = [wd_buttons[i:i + 1] for i in range(0, len(wd_buttons), 1)]

    keyboard = InlineKeyboardMarkup(
        wd_rows + [
            [
                InlineKeyboardButton("📜 History", callback_data="admin_withdrawals_history"),
                InlineKeyboardButton("📝 Add Note", callback_data="admin_withdrawal_note"),
            ],
            [_back_to_dashboard_button()],
        ]
    )

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_withdrawal_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show withdrawal details."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    withdrawal_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    await log_admin_action(
        admin_id, "withdrawal_detail", target_type="withdrawal", target_id=str(withdrawal_id)
    )

    wd = await async_fetchone(
        """
        SELECT w.*, u.username, u.first_name, u.telegram_id, u.is_banned
        FROM withdrawals w
        JOIN users u ON w.user_id = u.id
        WHERE w.id = ?
        """,
        (withdrawal_id,),
    )

    if not wd:
        await query.edit_message_text(
            "❌ Withdrawal not found.",
            reply_markup=InlineKeyboardMarkup([[_back_to_withdrawals_button()]]),
        )
        return

    # Check user balance
    wallet = await async_fetchone("SELECT * FROM wallets WHERE user_id = ?", (wd["user_id"],))
    user_balance = wallet["balance"] if wallet else 0

    # Previous withdrawals
    prev_count = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM withdrawals WHERE user_id = ? AND status = 'approved'",
        (wd["user_id"],),
    )
    prev_total = await async_fetchone(
        "SELECT COALESCE(SUM(amount), 0) as total FROM withdrawals WHERE user_id = ? AND status = 'approved'",
        (wd["user_id"],),
    )

    # BUG FIX: was `w["telegram_id"]` but variable is `wd`, not `w`
    name = wd.get("first_name") or wd.get("username") or str(wd["telegram_id"])

    status_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌", "processed": "🔄"}.get(
        wd["status"], "❓"
    )

    text = (
        f"💸 *Withdrawal Details*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"ID: `{wd['id']}`\n"
        f"Status: {status_emoji} {wd['status'].title()}\n"
        f"User: {name} (`{wd['telegram_id']}`)\n"
        f"User Banned: {'🚫 Yes' if wd.get('is_banned') else '✅ No'}\n"
        f"Amount: `{wd['amount']:.2f}` SAR\n"
        f"Method: {wd.get('method', 'N/A')}\n"
        f"Account Details: `{wd.get('account_details', 'N/A')}`\n"
        f"User Balance: `{user_balance:.2f}` SAR\n"
        f"Previous Withdrawals: {prev_count['cnt'] if prev_count else 0} "
        f"(Total: {prev_total['total'] if prev_total else 0:.2f} SAR)\n"
        f"Admin Note: {wd.get('admin_note', 'None')}\n"
        f"Requested: {wd.get('created_at', 'N/A')}\n"
        f"Reviewed: {wd.get('reviewed_at', 'N/A')}\n"
    )

    # Action buttons
    action_rows = []
    if wd["status"] == "pending":
        action_rows.append([
            InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_withdrawal:{withdrawal_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_withdrawal:{withdrawal_id}"),
        ])

    keyboard = InlineKeyboardMarkup(
        action_rows + [
            [_back_to_withdrawals_button(), _back_to_dashboard_button()],
        ]
    )

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_approve_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Approve a pending withdrawal."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    withdrawal_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    wd = await async_fetchone(
        """
        SELECT w.*, u.telegram_id, u.username, u.first_name
        FROM withdrawals w
        JOIN users u ON w.user_id = u.id
        WHERE w.id = ?
        """,
        (withdrawal_id,),
    )

    if not wd:
        await query.answer("❌ Withdrawal not found.", show_alert=True)
        return

    if wd["status"] != "pending":
        await query.answer("Withdrawal is not pending.", show_alert=True)
        return

    # Deduct from user wallet
    wallet = await async_fetchone("SELECT * FROM wallets WHERE user_id = ?", (wd["user_id"],))
    if wallet and wallet["balance"] >= wd["amount"]:
        await async_execute(
            "UPDATE wallets SET balance = balance - ? WHERE user_id = ?",
            (wd["amount"], wd["user_id"]),
        )
        # Record transaction
        await async_execute(
            """
            INSERT INTO transactions (user_id, type, amount, description)
            VALUES (?, ?, ?, ?)
            """,
            (
                wd["user_id"],
                "withdrawal",
                wd["amount"],
                f"Withdrawal #{withdrawal_id} approved - {wd.get('method', 'N/A')}",
            ),
        )
    elif wallet:
        # Insufficient balance - still approve but flag it
        await async_execute(
            "UPDATE wallets SET balance = 0 WHERE user_id = ?",
            (wd["user_id"],),
        )
        await async_execute(
            """
            INSERT INTO transactions (user_id, type, amount, description)
            VALUES (?, ?, ?, ?)
            """,
            (
                wd["user_id"],
                "withdrawal",
                wallet["balance"],
                f"Withdrawal #{withdrawal_id} approved (partial - insufficient balance)",
            ),
        )

    # Update withdrawal status
    await async_execute(
        """
        UPDATE withdrawals
        SET status = 'approved', reviewed_at = ?
        WHERE id = ?
        """,
        (datetime.utcnow().isoformat(), withdrawal_id),
    )

    await log_admin_action(
        admin_id, "approve_withdrawal", target_type="withdrawal", target_id=str(withdrawal_id),
        details=f"Approved withdrawal #{withdrawal_id} for {wd['amount']:.2f} SAR "
                f"for user {wd.get('first_name') or wd.get('username') or wd['telegram_id']}"
    )

    # Send message to user via Game Bot
    user_telegram_id = wd["telegram_id"]
    message_sent = await _send_game_bot_message(
        user_telegram_id,
        f"✅ Your withdrawal request of {wd['amount']:.2f} SAR has been approved!\n\n"
        f"We will contact you shortly with further details regarding your payment.",
        context,
    )

    notify_status = "\n📩 User notified via Game Bot." if message_sent else "\n⚠️ Failed to notify user via Game Bot."

    await query.answer()

    await query.edit_message_text(
        f"✅ Withdrawal #{withdrawal_id} approved!\n\n"
        f"Amount: {wd['amount']:.2f} SAR\n"
        f"User: {wd.get('first_name') or wd.get('username') or wd['telegram_id']}"
        f"{notify_status}",
        reply_markup=InlineKeyboardMarkup([
            [_back_to_withdrawals_button(), _back_to_dashboard_button()],
        ]),
        parse_mode="Markdown",
    )


async def cb_reject_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start rejection conversation - ask for reason."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return ConversationHandler.END

    withdrawal_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    wd = await async_fetchone("SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,))
    if not wd:
        await query.edit_message_text("❌ Withdrawal not found.")
        return ConversationHandler.END

    if wd["status"] != "pending":
        await query.answer("Withdrawal is not pending.", show_alert=True)
        return ConversationHandler.END

    # Store withdrawal ID for the conversation
    context.user_data[TEMP_REJECTION] = {
        "withdrawal_id": withdrawal_id,
        "admin_id": admin_id,
    }

    text = (
        f"❌ *Reject Withdrawal #{withdrawal_id}*\n\n"
        f"Enter the reason for rejection (or type 'skip' for no reason):\n\n"
        f"Send /cancel to cancel."
    )

    await query.answer()
    await query.edit_message_text(text, parse_mode="Markdown")
    return AWAITING_REJECTION_REASON


async def handle_rejection_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the rejection reason input."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    admin_id = update.effective_user.id
    temp = context.user_data.get(TEMP_REJECTION)
    if not temp:
        await update.message.reply_text("❌ Session expired. Try again.")
        return ConversationHandler.END

    withdrawal_id = temp["withdrawal_id"]
    reason = update.message.text.strip()
    if reason.lower() == "skip":
        reason = None

    wd = await async_fetchone(
        """
        SELECT w.*, u.telegram_id, u.username, u.first_name
        FROM withdrawals w
        JOIN users u ON w.user_id = u.id
        WHERE w.id = ?
        """,
        (withdrawal_id,),
    )

    if not wd:
        context.user_data.pop(TEMP_REJECTION, None)
        await update.message.reply_text("❌ Withdrawal not found.")
        return ConversationHandler.END

    # Update withdrawal status
    reject_note = f"Rejected: {reason}" if reason else "Rejected by admin"
    await async_execute(
        """
        UPDATE withdrawals
        SET status = 'rejected', reviewed_at = ?, admin_note = ?
        WHERE id = ?
        """,
        (datetime.utcnow().isoformat(), reject_note, withdrawal_id),
    )

    # No balance deduction for rejection - money stays in wallet

    await log_admin_action(
        admin_id, "reject_withdrawal", target_type="withdrawal", target_id=str(withdrawal_id),
        details=f"Rejected withdrawal #{withdrawal_id} for {wd['amount']:.2f} SAR. "
                f"Reason: {reason or 'No reason provided'}"
    )

    # Notify user
    notify_msg = ""
    if reason:
        user_msg = (
            f"❌ Your withdrawal request of {wd['amount']:.2f} SAR has been rejected.\n\n"
            f"Reason: {reason}\n\n"
            f"The amount remains in your wallet."
        )
    else:
        user_msg = (
            f"❌ Your withdrawal request of {wd['amount']:.2f} SAR has been rejected.\n\n"
            f"The amount remains in your wallet."
        )

    message_sent = await _send_game_bot_message(wd["telegram_id"], user_msg, context)
    notify_msg = "\n📩 User notified." if message_sent else "\n⚠️ Failed to notify user."

    context.user_data.pop(TEMP_REJECTION, None)

    keyboard = InlineKeyboardMarkup([
        [_back_to_withdrawals_button(), _back_to_dashboard_button()],
    ])

    await update.message.reply_text(
        f"❌ Withdrawal #{withdrawal_id} rejected.\n\n"
        f"Reason: {reason or 'No reason'}"
        f"{notify_msg}",
        reply_markup=keyboard,
    )

    return ConversationHandler.END


async def cb_withdrawal_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start adding a note to a withdrawal - ask for withdrawal ID."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return ConversationHandler.END

    await query.answer()

    text = (
        "📝 *Add Note to Withdrawal*\n\n"
        "Enter in format: `withdrawal_id note_text`\n"
        "Example: `5 User needs to verify identity first`\n\n"
        "Send /cancel to cancel."
    )

    await query.edit_message_text(text, parse_mode="Markdown")
    return AWAITING_WITHDRAWAL_NOTE


async def handle_withdrawal_note_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the withdrawal note input."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    admin_id = update.effective_user.id
    input_text = update.message.text.strip()

    parts = input_text.split(None, 1)
    if len(parts) < 2:
        await update.message.reply_text("❌ Use format: `withdrawal_id note_text`")
        return AWAITING_WITHDRAWAL_NOTE

    try:
        withdrawal_id = int(parts[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid withdrawal ID. Use format: `withdrawal_id note_text`")
        return AWAITING_WITHDRAWAL_NOTE

    note = parts[1]

    wd = await async_fetchone("SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,))
    if not wd:
        await update.message.reply_text("❌ Withdrawal not found.")
        return AWAITING_WITHDRAWAL_NOTE

    # Append note to existing admin_note
    existing_note = wd.get("admin_note", "") or ""
    if existing_note:
        new_note = f"{existing_note}\n[{datetime.utcnow().strftime('%Y-%m-%d %H:%M')}] {note}"
    else:
        new_note = f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M')}] {note}"

    await async_execute("UPDATE withdrawals SET admin_note = ? WHERE id = ?", (new_note, withdrawal_id))

    await log_admin_action(
        admin_id, "withdrawal_note", target_type="withdrawal", target_id=str(withdrawal_id),
        details=f"Added note to withdrawal #{withdrawal_id}: {note}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Detail", callback_data=f"admin_withdrawal_detail:{withdrawal_id}")],
        [_back_to_withdrawals_button(), _back_to_dashboard_button()],
    ])

    await update.message.reply_text(
        f"✅ Note added to withdrawal #{withdrawal_id}.",
        reply_markup=keyboard,
    )

    return ConversationHandler.END


async def cb_withdrawals_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View processed (approved/rejected) withdrawals history."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "withdrawals_history", details="Viewed withdrawal history")

    processed = await async_fetchall(
        """
        SELECT w.*, u.username, u.first_name, u.telegram_id
        FROM withdrawals w
        JOIN users u ON w.user_id = u.id
        WHERE w.status IN ('approved', 'rejected')
        ORDER BY w.reviewed_at DESC
        LIMIT 20
        """
    )

    if not processed:
        text = "📜 *Withdrawal History*\n\nNo processed withdrawals."
    else:
        lines = ["📜 *Withdrawal History*\n"]
        for wd in processed:
            status = "✅" if wd["status"] == "approved" else "❌"
            name = wd.get("first_name") or wd.get("username") or str(wd["telegram_id"])
            lines.append(
                f"{status} #{wd['id']} — {name}: {wd['amount']:.2f} SAR "
                f"({wd.get('reviewed_at', 'N/A')})"
            )
        text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Pending", callback_data="admin_withdrawals")],
        [_back_to_dashboard_button()],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the current conversation."""
    context.user_data.pop(TEMP_WITHDRAWAL_NOTE, None)
    context.user_data.pop(TEMP_REJECTION, None)
    await update.message.reply_text(
        "❌ Operation cancelled.",
        reply_markup=InlineKeyboardMarkup([[_back_to_dashboard_button()]]),
    )
    return ConversationHandler.END
