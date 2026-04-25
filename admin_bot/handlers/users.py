"""
Admin Users Handler
User management: list, detail, ban/unban, wallet view, balance edit, sessions.
"""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import ADMIN_IDS, CURRENCY_NAME
from database import async_fetchone, async_fetchall, async_execute
from admin_bot.utils import admin_guard, log_admin_action

USERS_PER_PAGE = 10

# Conversation state for balance editing
AWAITING_BALANCE_INPUT = "awaiting_balance_input"

# Temp data key
TEMP_EDIT_BALANCE = "temp_edit_balance"


def _back_to_dashboard_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🔙 Dashboard", callback_data="admin_dashboard")


def _back_to_users_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🔙 Users", callback_data="admin_users")


async def cb_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List users (first page)."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    await _show_users_page(query, page=0, admin_id=update.effective_user.id)


async def cb_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle pagination for user list."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    page = int(query.data.split(":")[1])
    await _show_users_page(query, page=page, admin_id=update.effective_user.id)


async def _show_users_page(query, page: int, admin_id: int) -> None:
    """Render a page of users."""
    await log_admin_action(admin_id, "users_list", details=f"Viewed users page {page + 1}")

    offset = page * USERS_PER_PAGE

    total_row = await async_fetchone("SELECT COUNT(*) as cnt FROM users")
    total_users = total_row["cnt"] if total_row else 0
    total_pages = max(1, (total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE)

    rows = await async_fetchall(
        """
        SELECT u.id, u.telegram_id, u.username, u.first_name, u.is_banned, u.joined_at,
               COALESCE(w.balance, 0) as balance
        FROM users u
        LEFT JOIN wallets w ON u.id = w.user_id
        ORDER BY u.id DESC
        LIMIT ? OFFSET ?
        """,
        (USERS_PER_PAGE, offset),
    )

    if not rows:
        text = "👥 *Users*\n\nNo users found."
        keyboard = InlineKeyboardMarkup([[_back_to_dashboard_button()]])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    lines = [f"👥 *Users* (Page {page + 1}/{total_pages})\n"]
    for r in rows:
        status = "🚫" if r["is_banned"] else "✅"
        name = r["first_name"] or r["username"] or "Unknown"
        lines.append(
            f"{status} `{r['telegram_id']}` — {name} | {r['balance']:.2f} SAR"
        )

    text = "\n".join(lines)

    buttons = []
    # Per-user detail buttons (up to 5 to avoid too many buttons)
    for r in rows[:5]:
        name = r["first_name"] or r["username"] or str(r["telegram_id"])
        buttons.append(
            InlineKeyboardButton(f"👤 {name[:15]}", callback_data=f"admin_user_detail:{r['id']}")
        )

    # Arrange detail buttons in rows of 2
    detail_rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]

    # Navigation row
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_users_page:{page - 1}"))
    nav_buttons.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="admin_users"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"admin_users_page:{page + 1}"))

    keyboard_rows = detail_rows + [nav_buttons, [_back_to_dashboard_button()]]
    keyboard = InlineKeyboardMarkup(keyboard_rows)

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user details."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    user_id = int(query.data.split(":")[1])

    await log_admin_action(
        update.effective_user.id, "user_detail", target_type="user", target_id=str(user_id)
    )

    user = await async_fetchone(
        """
        SELECT u.*, COALESCE(w.balance, 0) as balance
        FROM users u
        LEFT JOIN wallets w ON u.id = w.user_id
        WHERE u.id = ?
        """,
        (user_id,),
    )

    if not user:
        await query.edit_message_text("❌ User not found.", reply_markup=InlineKeyboardMarkup([[_back_to_users_button()]]))
        return

    # Get stats - game_players has no is_winner column, so count all games
    games_row = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM game_players WHERE user_id = ?",
        (user_id,),
    )
    tx_row = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM transactions WHERE user_id = ?",
        (user_id,),
    )

    total_games = games_row["cnt"] if games_row else 0
    total_tx = tx_row["cnt"] if tx_row else 0

    status = "🚫 BANNED" if user["is_banned"] else "✅ Active"
    ban_text = f"\n🚫 Ban reason: {user.get('ban_reason', 'N/A')}" if user.get("is_banned") else ""

    text = (
        f"👤 *User Details*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"ID: `{user['id']}`\n"
        f"Telegram ID: `{user['telegram_id']}`\n"
        f"Username: @{user.get('username') or 'N/A'}\n"
        f"Name: {user.get('first_name', 'N/A')}\n"
        f"Status: {status}{ban_text}\n"
        f"Balance: `{user['balance']:.2f}` SAR\n"
        f"Games Played: `{total_games}`\n"
        f"Transactions: `{total_tx}`\n"
        f"Joined: {user.get('joined_at', 'N/A')}\n"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰 Wallet", callback_data=f"admin_user_wallet:{user_id}"),
            InlineKeyboardButton("📊 Edit Balance", callback_data=f"admin_user_edit_balance:{user_id}"),
        ],
        [
            InlineKeyboardButton("🎮 Sessions", callback_data=f"admin_user_sessions:{user_id}"),
        ],
        [
            InlineKeyboardButton(
                "✅ Unban" if user["is_banned"] else "🚫 Ban",
                callback_data=f"admin_unban_user:{user_id}" if user["is_banned"] else f"admin_ban_user:{user_id}",
            ),
        ],
        [_back_to_users_button(), _back_to_dashboard_button()],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ban a user."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    user_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    user = await async_fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        await query.answer("❌ User not found.", show_alert=True)
        return

    if user["is_banned"]:
        await query.answer("User is already banned.", show_alert=True)
        return

    await async_execute("UPDATE users SET is_banned = 1, ban_reason = 'Banned by admin' WHERE id = ?", (user_id,))

    await log_admin_action(
        admin_id, "ban_user", target_type="user", target_id=str(user_id),
        details=f"Banned user {user.get('username') or user['telegram_id']}"
    )

    await query.answer()

    await query.edit_message_text(
        f"✅ User `{user['telegram_id']}` has been banned.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 View User", callback_data=f"admin_user_detail:{user_id}")],
            [_back_to_users_button()],
        ]),
        parse_mode="Markdown",
    )


async def cb_unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unban a user."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    user_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    user = await async_fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        await query.answer("❌ User not found.", show_alert=True)
        return

    if not user["is_banned"]:
        await query.answer("User is not banned.", show_alert=True)
        return

    await async_execute("UPDATE users SET is_banned = 0, ban_reason = NULL WHERE id = ?", (user_id,))

    await log_admin_action(
        admin_id, "unban_user", target_type="user", target_id=str(user_id),
        details=f"Unbanned user {user.get('username') or user['telegram_id']}"
    )

    await query.answer()

    await query.edit_message_text(
        f"✅ User `{user['telegram_id']}` has been unbanned.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 View User", callback_data=f"admin_user_detail:{user_id}")],
            [_back_to_users_button()],
        ]),
        parse_mode="Markdown",
    )


async def cb_user_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View user's wallet."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    user_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    await log_admin_action(
        admin_id, "view_user_wallet", target_type="user", target_id=str(user_id)
    )

    wallet = await async_fetchone("SELECT * FROM wallets WHERE user_id = ?", (user_id,))
    user = await async_fetchone("SELECT * FROM users WHERE id = ?", (user_id,))

    if not user:
        await query.edit_message_text("❌ User not found.")
        return

    if not wallet:
        text = (
            f"💰 *Wallet for* {user.get('first_name', 'Unknown')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"No wallet found."
        )
    else:
        # Recent transactions
        recent_tx = await async_fetchall(
            "SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT 5",
            (user_id,),
        )
        tx_lines = []
        for tx in recent_tx:
            sign = "+" if tx["type"] in ("credit", "reward", "referral", "promotion", "admin_adjust") else "-"
            tx_lines.append(f"  {sign}{tx['amount']:.2f} — {tx['type']} ({tx.get('created_at', '')})")

        tx_text = "\n".join(tx_lines) if tx_lines else "  No recent transactions"

        text = (
            f"💰 *Wallet for* {user.get('first_name', 'Unknown')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Balance: `{wallet['balance']:.2f}` SAR\n"
            f"\n📝 Recent Transactions:\n{tx_text}"
        )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Adjust Balance", callback_data=f"admin_wallet_adjust:{user_id}"),
            InlineKeyboardButton("👤 User Detail", callback_data=f"admin_user_detail:{user_id}"),
        ],
        [_back_to_users_button(), _back_to_dashboard_button()],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_user_edit_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start balance edit conversation."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return ConversationHandler.END

    await query.answer()
    user_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    user = await async_fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        await query.edit_message_text("❌ User not found.")
        return ConversationHandler.END

    wallet = await async_fetchone("SELECT * FROM wallets WHERE user_id = ?", (user_id,))

    current_balance = wallet["balance"] if wallet else 0

    # Store target user_id in context
    context.user_data[TEMP_EDIT_BALANCE] = {
        "user_id": user_id,
        "admin_id": admin_id,
        "current_balance": current_balance,
    }

    text = (
        f"📊 *Edit Balance for* {user.get('first_name', 'Unknown')}\n"
        f"Current balance: `{current_balance:.2f}` SAR\n\n"
        f"Enter new balance or adjustment:\n"
        f"• `+100` to add 100 SAR\n"
        f"• `-50` to subtract 50 SAR\n"
        f"• `=200` to set balance to 200 SAR\n\n"
        f"Send /cancel to cancel."
    )

    await query.edit_message_text(text, parse_mode="Markdown")
    return AWAITING_BALANCE_INPUT


async def handle_balance_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the balance adjustment input."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    admin_id = update.effective_user.id
    temp = context.user_data.get(TEMP_EDIT_BALANCE)
    if not temp:
        await update.message.reply_text("❌ Session expired. Try again.")
        return ConversationHandler.END

    user_id = temp["user_id"]
    current_balance = temp["current_balance"]
    input_text = update.message.text.strip()

    try:
        if input_text.startswith("+"):
            amount = float(input_text[1:])
            new_balance = current_balance + amount
            change_desc = f"Added {amount:.2f} SAR"
        elif input_text.startswith("-"):
            amount = float(input_text[1:])
            new_balance = current_balance - amount
            change_desc = f"Subtracted {amount:.2f} SAR"
        elif input_text.startswith("="):
            new_balance = float(input_text[1:])
            change_desc = f"Set to {new_balance:.2f} SAR"
        else:
            # Default: add the amount
            amount = float(input_text)
            new_balance = current_balance + amount
            change_desc = f"Added {amount:.2f} SAR"

        if new_balance < 0:
            await update.message.reply_text("❌ Balance cannot be negative. Try again:")
            return AWAITING_BALANCE_INPUT

    except ValueError:
        await update.message.reply_text("❌ Invalid input. Use +100, -50, or =200 format:")
        return AWAITING_BALANCE_INPUT

    # Update wallet
    await async_execute("UPDATE wallets SET balance = ? WHERE user_id = ?", (new_balance, user_id))

    # Record transaction
    diff = new_balance - current_balance
    tx_type = "admin_adjust" if diff > 0 else "admin_adjust"
    await async_execute(
        """
        INSERT INTO transactions (user_id, type, amount, description)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, tx_type, abs(diff), f"Admin balance adjustment: {change_desc}"),
    )

    await log_admin_action(
        admin_id, "edit_balance", target_type="user", target_id=str(user_id),
        details=f"{change_desc} (was {current_balance:.2f}, now {new_balance:.2f})"
    )

    # Clean up
    context.user_data.pop(TEMP_EDIT_BALANCE, None)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 User Detail", callback_data=f"admin_user_detail:{user_id}")],
        [_back_to_users_button(), _back_to_dashboard_button()],
    ])

    await update.message.reply_text(
        f"✅ Balance updated!\n\n"
        f"Previous: `{current_balance:.2f}` SAR\n"
        f"Change: {change_desc}\n"
        f"New Balance: `{new_balance:.2f}` SAR",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

    return ConversationHandler.END


async def cb_user_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View user's active game sessions."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    user_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    await log_admin_action(
        admin_id, "view_user_sessions", target_type="user", target_id=str(user_id)
    )

    sessions = await async_fetchall(
        """
        SELECT gs.*, g.name as game_name
        FROM game_sessions gs
        JOIN game_players gp ON gs.id = gp.session_id
        JOIN games g ON gs.game_id = g.id
        WHERE gp.user_id = ? AND gs.status = 'active'
        ORDER BY gs.created_at DESC
        """,
        (user_id,),
    )

    user = await async_fetchone("SELECT * FROM users WHERE id = ?", (user_id,))

    if not sessions:
        text = f"🎮 *Active Sessions for* {user.get('first_name', 'Unknown') if user else 'User'}\n\nNo active sessions."
    else:
        lines = [f"🎮 *Active Sessions for* {user.get('first_name', 'Unknown') if user else 'User'}\n"]
        for s in sessions:
            lines.append(
                f"• Session #{s['id']} — {s['game_name']} (started {s.get('created_at', 'N/A')})"
            )
        text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 User Detail", callback_data=f"admin_user_detail:{user_id}")],
        [_back_to_users_button(), _back_to_dashboard_button()],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the current conversation."""
    context.user_data.pop(TEMP_EDIT_BALANCE, None)
    await update.message.reply_text(
        "❌ Operation cancelled.",
        reply_markup=InlineKeyboardMarkup([[_back_to_dashboard_button()]]),
    )
    return ConversationHandler.END
