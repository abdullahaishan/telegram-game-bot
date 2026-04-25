"""
Admin Store Handler
Store management: list items, add, edit, toggle, view purchases, item details.
"""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import ADMIN_IDS, CURRENCY_NAME
from database import async_fetchone, async_fetchall, async_execute
from admin_bot.utils import admin_guard, log_admin_action

# Conversation states for adding item
AWAITING_ITEM_NAME = "awaiting_item_name"
AWAITING_ITEM_DESCRIPTION = "awaiting_item_description"
AWAITING_ITEM_PRICE = "awaiting_item_price"
AWAITING_ITEM_IMAGE = "awaiting_item_image"

# Conversation states for editing item
AWAITING_EDIT_FIELD = "awaiting_edit_field"
AWAITING_EDIT_VALUE = "awaiting_edit_value"

# Temp data keys
TEMP_ADD_ITEM = "temp_add_item"
TEMP_EDIT_ITEM = "temp_edit_item"

# Whitelist of allowed fields for editing (SQL injection prevention)
ALLOWED_EDIT_FIELDS = {"name", "description", "price_sar"}


def _back_to_dashboard_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🔙 Dashboard", callback_data="admin_dashboard")


def _back_to_store_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🛒 Store", callback_data="admin_store")


async def cb_store(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all store items."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "store_list", details="Viewed store items")

    items = await async_fetchall(
        """
        SELECT si.*,
               (SELECT COUNT(*) FROM purchases WHERE item_id = si.id) as purchase_count
        FROM store_items si
        ORDER BY si.is_active DESC, si.id ASC
        """
    )

    if not items:
        text = "🛒 *Store Items*\n\nNo items in store."
    else:
        lines = ["🛒 *Store Items*\n"]
        for item in items:
            status = "✅" if item["is_active"] else "🔴"
            purchases = item.get("purchase_count", 0)
            lines.append(
                f"{status} #{item['id']} — {item['name']}\n"
                f"  Price: {item['price_sar']:.2f} SAR | Purchases: {purchases}"
            )
        text = "\n".join(lines)

    # Build buttons for items
    item_buttons = []
    for item in items[:10]:
        status = "✅" if item["is_active"] else "🔴"
        item_buttons.append(
            InlineKeyboardButton(
                f"{status} {item['name'][:15]}",
                callback_data=f"admin_store_item_detail:{item['id']}",
            )
        )

    item_rows = [item_buttons[i:i + 2] for i in range(0, len(item_buttons), 2)]

    keyboard = InlineKeyboardMarkup(
        item_rows + [
            [
                InlineKeyboardButton("➕ Add Item", callback_data="admin_add_store_item"),
                InlineKeyboardButton("📜 Purchases", callback_data="admin_store_purchases"),
            ],
            [_back_to_dashboard_button()],
        ]
    )

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_add_store_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start adding a new store item - ask for name."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return ConversationHandler.END

    await query.answer()

    context.user_data[TEMP_ADD_ITEM] = {}

    text = (
        "➕ *Add New Store Item*\n\n"
        "Step 1/4: Enter the item name\n\n"
        "Send /cancel to cancel."
    )

    await query.edit_message_text(text, parse_mode="Markdown")
    return AWAITING_ITEM_NAME


async def handle_item_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle item name input."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    name = update.message.text.strip()
    if len(name) > 100:
        await update.message.reply_text("❌ Name too long (max 100 chars). Try again:")
        return AWAITING_ITEM_NAME

    context.user_data[TEMP_ADD_ITEM]["name"] = name

    text = (
        f"✅ Name: {name}\n\n"
        "Step 2/4: Enter the item description\n\n"
        "Send /cancel to cancel."
    )

    await update.message.reply_text(text)
    return AWAITING_ITEM_DESCRIPTION


async def handle_item_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle item description input."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    description = update.message.text.strip()

    context.user_data[TEMP_ADD_ITEM]["description"] = description

    text = (
        "✅ Description recorded.\n\n"
        "Step 3/4: Enter the item price in SAR\n"
        "Example: `25.00`\n\n"
        "Send /cancel to cancel."
    )

    await update.message.reply_text(text, parse_mode="Markdown")
    return AWAITING_ITEM_PRICE


async def handle_item_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle item price input."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    try:
        price = float(update.message.text.strip())
        if price <= 0:
            raise ValueError("Price must be positive")
    except ValueError:
        await update.message.reply_text("❌ Invalid price. Enter a positive number (e.g., `25.00`):")
        return AWAITING_ITEM_PRICE

    context.user_data[TEMP_ADD_ITEM]["price_sar"] = price

    text = (
        f"✅ Price: {price:.2f} SAR\n\n"
        "Step 4/4: Enter the item type\n"
        "Valid types: title, badge, theme, powerup, skin, feature\n"
        "Example: `badge`\n\n"
        "Send /cancel to cancel."
    )

    await update.message.reply_text(text)
    return AWAITING_ITEM_IMAGE


async def handle_item_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle item type input (was image, now item_type) and save the item."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    admin_id = update.effective_user.id
    temp = context.user_data.get(TEMP_ADD_ITEM, {})

    # Get item type from text input
    item_type = update.message.text.strip().lower()
    valid_types = {"title", "badge", "theme", "powerup", "skin", "feature"}
    if item_type not in valid_types:
        await update.message.reply_text(
            f"❌ Invalid type '{item_type}'. Valid types: {', '.join(valid_types)}. Try again:"
        )
        return AWAITING_ITEM_IMAGE

    name = temp.get("name")
    description = temp.get("description", "")
    price = temp.get("price_sar", 0)

    if not name:
        context.user_data.pop(TEMP_ADD_ITEM, None)
        await update.message.reply_text("❌ Session data lost. Please start over.")
        return ConversationHandler.END

    # Insert into database using correct schema column names
    await async_execute(
        """
        INSERT INTO store_items (item_type, name, description, price_sar, is_active)
        VALUES (?, ?, ?, ?, 1)
        """,
        (item_type, name, description, price),
    )

    item = await async_fetchone("SELECT last_insert_rowid() as id")

    await log_admin_action(
        admin_id, "add_store_item", target_type="store_item",
        target_id=str(item["id"]) if item else "unknown",
        details=f"Added store item: {name} ({price:.2f} SAR)"
    )

    context.user_data.pop(TEMP_ADD_ITEM, None)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Store", callback_data="admin_store")],
        [_back_to_dashboard_button()],
    ])

    await update.message.reply_text(
        f"✅ Store item added!\n\n"
        f"Name: {name}\n"
        f"Price: {price:.2f} SAR\n"
        f"Type: {item_type}\n"
        f"Description: {description[:100]}",
        reply_markup=keyboard,
    )

    return ConversationHandler.END


async def cb_edit_store_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start editing a store item - show field selection."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return ConversationHandler.END

    await query.answer()
    item_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    item = await async_fetchone("SELECT * FROM store_items WHERE id = ?", (item_id,))
    if not item:
        await query.edit_message_text("❌ Item not found.")
        return ConversationHandler.END

    context.user_data[TEMP_EDIT_ITEM] = {"item_id": item_id}

    text = (
        f"✏️ *Edit Store Item: {item['name']}*\n\n"
        f"Current values:\n"
        f"• Name: {item['name']}\n"
        f"• Description: {item.get('description', 'N/A')[:50]}\n"
        f"• Price: {item['price_sar']:.2f} SAR\n\n"
        f"Select field to edit:"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Name", callback_data="store_edit_field:name")],
        [InlineKeyboardButton("📝 Description", callback_data="store_edit_field:description")],
        [InlineKeyboardButton("📝 Price", callback_data="store_edit_field:price_sar")],
        [InlineKeyboardButton("❌ Cancel", callback_data="admin_store_item_detail:" + str(item_id))],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return AWAITING_EDIT_FIELD


async def handle_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle which field to edit."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return ConversationHandler.END

    field = query.data.split(":")[1]

    # SQL injection prevention: validate field against whitelist
    if field not in ALLOWED_EDIT_FIELDS:
        await query.answer("Invalid field.", show_alert=True)
        return ConversationHandler.END

    temp = context.user_data.get(TEMP_EDIT_ITEM, {})
    temp["field"] = field
    context.user_data[TEMP_EDIT_ITEM] = temp

    field_names = {
        "name": "name",
        "description": "description",
        "price_sar": "price (in SAR)",
    }

    text = (
        f"✏️ *Edit {field_names.get(field, field)}*\n\n"
        f"Enter the new value:\n\n"
        f"Send /cancel to cancel."
    )

    await query.answer()
    await query.edit_message_text(text, parse_mode="Markdown")
    return AWAITING_EDIT_VALUE


async def handle_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the new value for the edited field."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    admin_id = update.effective_user.id
    temp = context.user_data.get(TEMP_EDIT_ITEM, {})

    item_id = temp.get("item_id")
    field = temp.get("field")

    if not item_id or not field:
        context.user_data.pop(TEMP_EDIT_ITEM, None)
        await update.message.reply_text("❌ Session data lost.")
        return ConversationHandler.END

    item = await async_fetchone("SELECT * FROM store_items WHERE id = ?", (item_id,))
    if not item:
        context.user_data.pop(TEMP_EDIT_ITEM, None)
        await update.message.reply_text("❌ Item not found.")
        return ConversationHandler.END

    # Parse the value
    value = None
    if field == "price_sar":
        try:
            value = float(update.message.text.strip())
            if value <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Invalid price. Enter a positive number:")
            return AWAITING_EDIT_VALUE
    elif field == "name":
        value = update.message.text.strip()
        if len(value) > 100:
            await update.message.reply_text("❌ Name too long (max 100 chars):")
            return AWAITING_EDIT_VALUE
    else:
        value = update.message.text.strip()

    old_value = item.get(field, "N/A")

    # Update the field using parameterized query (field is validated against ALLOWED_EDIT_FIELDS)
    await async_execute(f"UPDATE store_items SET {field} = ? WHERE id = ?", (value, item_id))

    await log_admin_action(
        admin_id, "edit_store_item", target_type="store_item", target_id=str(item_id),
        details=f"Edited item #{item_id} field '{field}': '{old_value}' → '{value}'"
    )

    context.user_data.pop(TEMP_EDIT_ITEM, None)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Item Detail", callback_data=f"admin_store_item_detail:{item_id}")],
        [_back_to_store_button(), _back_to_dashboard_button()],
    ])

    field_display = {"name": "Name", "description": "Description", "price_sar": "Price"}

    await update.message.reply_text(
        f"✅ Item updated!\n\n"
        f"Field: {field_display.get(field, field)}\n"
        f"Old: {old_value}\n"
        f"New: {value}",
        reply_markup=keyboard,
    )

    return ConversationHandler.END


async def cb_toggle_store_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle store item active/inactive."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    item_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    item = await async_fetchone("SELECT * FROM store_items WHERE id = ?", (item_id,))
    if not item:
        await query.answer("❌ Item not found.", show_alert=True)
        return

    new_status = 0 if item["is_active"] else 1
    status_text = "disabled" if item["is_active"] else "enabled"

    await async_execute("UPDATE store_items SET is_active = ? WHERE id = ?", (new_status, item_id))

    await log_admin_action(
        admin_id, "toggle_store_item", target_type="store_item", target_id=str(item_id),
        details=f"{status_text.title()} store item: {item['name']}"
    )

    await query.answer(f"✅ Item {status_text}!")

    # Refresh item detail
    await _show_item_detail(query, item_id, admin_id)


async def cb_store_purchases(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View recent purchases."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "store_purchases", details="Viewed recent purchases")

    purchases = await async_fetchall(
        """
        SELECT p.*, si.name as item_name, u.username, u.first_name, u.telegram_id
        FROM purchases p
        JOIN store_items si ON p.item_id = si.id
        JOIN users u ON p.user_id = u.id
        ORDER BY p.created_at DESC
        LIMIT 20
        """
    )

    if not purchases:
        text = "📜 *Recent Purchases*\n\nNo purchases yet."
    else:
        lines = ["📜 *Recent Purchases*\n"]
        for p in purchases:
            name = p.get("first_name") or p.get("username") or str(p["telegram_id"])
            lines.append(
                f"📋 #{p['id']} — {p['item_name']} by {name} "
                f"({p['price_paid']:.2f} SAR) {p.get('created_at', '')}"
            )
        text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [_back_to_store_button(), _back_to_dashboard_button()],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_store_item_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show store item details."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    item_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    await _show_item_detail(query, item_id, admin_id)


async def _show_item_detail(query, item_id: int, admin_id: int) -> None:
    """Render store item detail view."""
    await log_admin_action(
        admin_id, "store_item_detail", target_type="store_item", target_id=str(item_id)
    )

    item = await async_fetchone("SELECT * FROM store_items WHERE id = ?", (item_id,))
    if not item:
        await query.edit_message_text(
            "❌ Item not found.",
            reply_markup=InlineKeyboardMarkup([[_back_to_store_button()]]),
        )
        return

    # Purchase stats
    purchase_count = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM purchases WHERE item_id = ?", (item_id,)
    )
    revenue = await async_fetchone(
        "SELECT COALESCE(SUM(price_paid), 0) as total FROM purchases WHERE item_id = ?",
        (item_id,),
    )

    cnt = purchase_count["cnt"] if purchase_count else 0
    rev = revenue["total"] if revenue else 0

    status = "✅ Active" if item["is_active"] else "🔴 Disabled"

    text = (
        f"🛒 *Store Item Details*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"ID: `{item['id']}`\n"
        f"Name: {item['name']}\n"
        f"Type: {item.get('item_type', 'N/A')}\n"
        f"Status: {status}\n"
        f"Price: {item['price_sar']:.2f} SAR\n"
        f"Description: {item.get('description', 'N/A')}\n"
        f"Purchases: {cnt}\n"
        f"Revenue: {rev:.2f} SAR\n"
        f"Created: {item.get('created_at', 'N/A')}\n"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Edit", callback_data=f"admin_edit_store_item:{item_id}"),
            InlineKeyboardButton(
                "🔴 Disable" if item["is_active"] else "✅ Enable",
                callback_data=f"admin_toggle_store_item:{item_id}",
            ),
        ],
        [_back_to_store_button(), _back_to_dashboard_button()],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the current conversation."""
    context.user_data.pop(TEMP_ADD_ITEM, None)
    context.user_data.pop(TEMP_EDIT_ITEM, None)
    await update.message.reply_text(
        "❌ Operation cancelled.",
        reply_markup=InlineKeyboardMarkup([[_back_to_dashboard_button()]]),
    )
    return ConversationHandler.END
