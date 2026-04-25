"""
Admin Channels Handler
Channel management: list, add, remove, toggle, reorder, membership stats.
"""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import ADMIN_IDS
from database import async_fetchone, async_fetchall, async_execute
from admin_bot.utils import admin_guard, log_admin_action

# Conversation states
AWAITING_CHANNEL_ID = "awaiting_channel_id"
AWAITING_CHANNEL_TITLE = "awaiting_channel_title"
AWAITING_CHANNEL_LINK = "awaiting_channel_link"
AWAITING_CHANNEL_ORDER = "awaiting_channel_order"

# Temp data keys
TEMP_CHANNEL_ADD = "temp_channel_add"


def _back_to_dashboard_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🔙 Dashboard", callback_data="admin_dashboard")


def _back_to_channels_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("📺 Channels", callback_data="admin_channels")


async def cb_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List required channels."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "channels_list", details="Viewed channels list")

    channels = await async_fetchall(
        """
        SELECT * FROM required_channels
        ORDER BY position ASC, id ASC
        """
    )

    if not channels:
        text = "📺 *Required Channels*\n\nNo channels configured."
    else:
        lines = ["📺 *Required Channels*\n"]
        for ch in channels:
            status = "✅" if ch["is_enabled"] else "🔴"
            lines.append(
                f"{status} #{ch['id']} — {ch['channel_name']} "
                f"(@{ch.get('channel_username', 'N/A')}) [Order: {ch.get('position', 0)}]"
            )
        text = "\n".join(lines)

    # Build buttons
    channel_buttons = []
    for ch in channels:
        status = "✅" if ch["is_enabled"] else "🔴"
        title = ch.get("channel_name", str(ch["channel_id"]))[:15]
        channel_buttons.append(
            InlineKeyboardButton(
                f"{status} {title}",
                callback_data=f"admin_toggle_channel:{ch['id']}",
            )
        )

    channel_rows = [channel_buttons[i:i + 2] for i in range(0, len(channel_buttons), 2)]

    keyboard = InlineKeyboardMarkup(
        channel_rows + [
            [
                InlineKeyboardButton("➕ Add Channel", callback_data="admin_add_channel"),
                InlineKeyboardButton("📊 Info", callback_data="admin_channel_info"),
            ],
            [InlineKeyboardButton("🔄 Reorder", callback_data="admin_reorder_channels")],
            [_back_to_dashboard_button()],
        ]
    )

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start adding a new channel - ask for channel ID."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return ConversationHandler.END

    await query.answer()

    context.user_data[TEMP_CHANNEL_ADD] = {}

    text = (
        "➕ *Add New Channel*\n\n"
        "Step 1/3: Enter the channel ID (e.g., `-1001234567890`)\n\n"
        "Send /cancel to cancel."
    )

    await query.edit_message_text(text, parse_mode="Markdown")
    return AWAITING_CHANNEL_ID


async def handle_channel_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle channel ID input."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    input_text = update.message.text.strip()

    # Try to parse as integer (channel IDs are negative numbers)
    try:
        channel_id = int(input_text)
        if channel_id > 0:
            # Could be a username without @, but let's accept it
            pass
    except ValueError:
        await update.message.reply_text("❌ Invalid channel ID. Must be a number (e.g., `-1001234567890`). Try again:")
        return AWAITING_CHANNEL_ID

    context.user_data[TEMP_CHANNEL_ADD]["channel_id"] = str(channel_id)

    text = (
        "✅ Channel ID recorded.\n\n"
        "Step 2/3: Enter the channel title (e.g., `My Game Channel`)\n\n"
        "Send /cancel to cancel."
    )

    await update.message.reply_text(text, parse_mode="Markdown")
    return AWAITING_CHANNEL_TITLE


async def handle_channel_title_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle channel title input."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    title = update.message.text.strip()

    if len(title) > 100:
        await update.message.reply_text("❌ Title too long (max 100 chars). Try again:")
        return AWAITING_CHANNEL_TITLE

    context.user_data[TEMP_CHANNEL_ADD]["title"] = title

    text = (
        "✅ Title recorded.\n\n"
        "Step 3/3: Enter the channel invite link (e.g., `https://t.me/mychannel`)\n\n"
        "Send /cancel to cancel."
    )

    await update.message.reply_text(text, parse_mode="Markdown")
    return AWAITING_CHANNEL_LINK


async def handle_channel_link_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle channel link input and save the channel."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    admin_id = update.effective_user.id
    link = update.message.text.strip()
    temp = context.user_data.get(TEMP_CHANNEL_ADD, {})

    channel_id = temp.get("channel_id")
    title = temp.get("title")

    if not channel_id or not title:
        await update.message.reply_text("❌ Session data lost. Please start over.")
        context.user_data.pop(TEMP_CHANNEL_ADD, None)
        return ConversationHandler.END

    # Get next position
    max_order = await async_fetchone("SELECT COALESCE(MAX(position), 0) as max_ord FROM required_channels")
    next_order = (max_order["max_ord"] if max_order else 0) + 1

    # Try to get channel info from Telegram
    username = None
    try:
        chat = await context.bot.get_chat(channel_id)
        username = chat.username if chat.username else None
        if chat.title and not title:
            title = chat.title
    except Exception:
        pass  # Bot might not be in the channel, that's OK

    # Insert into database using schema column names
    await async_execute(
        """
        INSERT INTO required_channels (channel_id, channel_username, channel_name, is_enabled, position)
        VALUES (?, ?, ?, 1, ?)
        """,
        (int(channel_id), username, title, next_order),
    )

    await log_admin_action(
        admin_id, "add_channel", target_type="channel", target_id=channel_id,
        details=f"Added channel: {title} ({channel_id})"
    )

    context.user_data.pop(TEMP_CHANNEL_ADD, None)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📺 Channels", callback_data="admin_channels")],
        [_back_to_dashboard_button()],
    ])

    await update.message.reply_text(
        f"✅ Channel added!\n\n"
        f"Title: {title}\n"
        f"ID: `{channel_id}`\n"
        f"Link: {link}",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

    return ConversationHandler.END


async def cb_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a required channel."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    channel_pk = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    channel = await async_fetchone("SELECT * FROM required_channels WHERE id = ?", (channel_pk,))
    if not channel:
        await query.answer("❌ Channel not found.", show_alert=True)
        return

    await async_execute("DELETE FROM required_channels WHERE id = ?", (channel_pk,))

    await log_admin_action(
        admin_id, "remove_channel", target_type="channel", target_id=str(channel_pk),
        details=f"Removed channel: {channel.get('channel_name', channel['channel_id'])}"
    )

    await query.answer()

    await query.edit_message_text(
        f"✅ Channel *{channel.get('channel_name', 'Unknown')}* has been removed.",
        reply_markup=InlineKeyboardMarkup([
            [_back_to_channels_button()],
        ]),
        parse_mode="Markdown",
    )


async def cb_toggle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle channel active/inactive status."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    channel_pk = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    channel = await async_fetchone("SELECT * FROM required_channels WHERE id = ?", (channel_pk,))
    if not channel:
        await query.answer("❌ Channel not found.", show_alert=True)
        return

    new_status = 0 if channel["is_enabled"] else 1
    status_text = "disabled" if channel["is_enabled"] else "enabled"

    await async_execute("UPDATE required_channels SET is_enabled = ? WHERE id = ?", (new_status, channel_pk))

    await log_admin_action(
        admin_id, "toggle_channel", target_type="channel", target_id=str(channel_pk),
        details=f"{status_text.title()} channel: {channel.get('channel_name', channel['channel_id'])}"
    )

    await query.answer(f"✅ Channel {status_text}!")

    # Refresh the channels list
    channels = await async_fetchall(
        "SELECT * FROM required_channels ORDER BY position ASC, id ASC"
    )

    lines = ["📺 *Required Channels*\n"]
    channel_buttons = []
    for ch in channels:
        status = "✅" if ch["is_enabled"] else "🔴"
        lines.append(
            f"{status} #{ch['id']} — {ch.get('channel_name', ch['channel_id'])} "
            f"(@{ch.get('channel_username', 'N/A')}) [Order: {ch.get('position', 0)}]"
        )
        title = ch.get("channel_name", str(ch["channel_id"]))[:15]
        channel_buttons.append(
            InlineKeyboardButton(
                f"{'✅' if ch['is_enabled'] else '🔴'} {title}",
                callback_data=f"admin_toggle_channel:{ch['id']}",
            )
        )

    channel_rows = [channel_buttons[i:i + 2] for i in range(0, len(channel_buttons), 2)]
    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup(
        channel_rows + [
            [
                InlineKeyboardButton("➕ Add Channel", callback_data="admin_add_channel"),
                InlineKeyboardButton("📊 Info", callback_data="admin_channel_info"),
            ],
            [InlineKeyboardButton("🔄 Reorder", callback_data="admin_reorder_channels")],
            [_back_to_dashboard_button()],
        ]
    )

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_reorder_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start channel reorder conversation."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return ConversationHandler.END

    await query.answer()

    channels = await async_fetchall(
        "SELECT * FROM required_channels ORDER BY position ASC, id ASC"
    )

    if not channels:
        await query.edit_message_text(
            "📺 No channels to reorder.",
            reply_markup=InlineKeyboardMarkup([[_back_to_channels_button()]]),
        )
        return ConversationHandler.END

    lines = ["🔄 *Reorder Channels*\n\nCurrent order:"]
    for i, ch in enumerate(channels, 1):
        lines.append(f"  {i}. #{ch['id']} — {ch.get('channel_name', ch['channel_id'])}")

    lines.append(
        "\nEnter new order as comma-separated IDs.\n"
        "Example: `3,1,2` means channel #3 first, then #1, then #2\n\n"
        "Send /cancel to cancel."
    )

    text = "\n".join(lines)
    await query.edit_message_text(text, parse_mode="Markdown")
    return AWAITING_CHANNEL_ORDER


async def handle_channel_order_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the channel reorder input."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    admin_id = update.effective_user.id
    input_text = update.message.text.strip()

    try:
        order_ids = [int(x.strip()) for x in input_text.split(",")]
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Use comma-separated IDs (e.g., `3,1,2`):")
        return AWAITING_CHANNEL_ORDER

    # Verify all IDs exist
    for cid in order_ids:
        exists = await async_fetchone("SELECT id FROM required_channels WHERE id = ?", (cid,))
        if not exists:
            await update.message.reply_text(f"❌ Channel #{cid} not found. Try again:")
            return AWAITING_CHANNEL_ORDER

    # Update display orders
    for i, cid in enumerate(order_ids, 1):
        await async_execute("UPDATE required_channels SET position = ? WHERE id = ?", (i, cid))

    # Set remaining channels to end
    all_channels = await async_fetchall("SELECT id FROM required_channels")
    order_set = set(order_ids)
    remaining = [ch["id"] for ch in all_channels if ch["id"] not in order_set]
    for i, cid in enumerate(remaining, len(order_ids) + 1):
        await async_execute("UPDATE required_channels SET position = ? WHERE id = ?", (i, cid))

    await log_admin_action(
        admin_id, "reorder_channels", details=f"Reordered channels: {input_text}"
    )

    keyboard = InlineKeyboardMarkup([
        [_back_to_channels_button(), _back_to_dashboard_button()],
    ])

    await update.message.reply_text(
        "✅ Channel order updated!",
        reply_markup=keyboard,
    )

    return ConversationHandler.END


async def cb_channel_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show channel membership stats."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "channel_info", details="Viewed channel membership stats")

    channels = await async_fetchall(
        "SELECT * FROM required_channels WHERE is_enabled = 1 ORDER BY position ASC"
    )

    if not channels:
        await query.edit_message_text(
            "📺 No active channels.",
            reply_markup=InlineKeyboardMarkup([[_back_to_channels_button()]]),
        )
        return

    lines = ["📊 *Channel Membership Stats*\n"]

    for ch in channels:
        member_count = "N/A"
        try:
            chat = await context.bot.get_chat(ch["channel_id"])
            member_count = str(chat.member_count) if hasattr(chat, "member_count") else "N/A"
        except Exception as e:
            member_count = f"Error: {str(e)[:30]}"

        status = "✅" if ch["is_enabled"] else "🔴"
        lines.append(
            f"{status} {ch.get('channel_name', ch['channel_id'])}\n"
            f"  Members: {member_count}\n"
            f"  Username: @{ch.get('channel_username', 'N/A')}\n"
        )

    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [_back_to_channels_button(), _back_to_dashboard_button()],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the current conversation."""
    context.user_data.pop(TEMP_CHANNEL_ADD, None)
    await update.message.reply_text(
        "❌ Operation cancelled.",
        reply_markup=InlineKeyboardMarkup([[_back_to_dashboard_button()]]),
    )
    return ConversationHandler.END
