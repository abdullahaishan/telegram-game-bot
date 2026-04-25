"""
Admin Logs Handler
System logs: view, detail, filter, paginate, clear old, export.
"""

import csv
import io
import json
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import ADMIN_IDS
from database import async_fetchone, async_fetchall, async_execute
from admin_bot.utils import admin_guard, log_admin_action

LOGS_PER_PAGE = 10

# Conversation state
AWAITING_LOG_FILTER = "awaiting_log_filter"

# Temp data keys
TEMP_LOG_FILTER = "temp_log_filter"


def _back_to_dashboard_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🔙 Dashboard", callback_data="admin_dashboard")


def _back_to_logs_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("📋 Logs", callback_data="admin_logs")


async def cb_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View recent admin logs (first page)."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "view_logs", details="Viewed admin logs")

    await _show_logs_page(query, page=0, admin_id=admin_id)


async def cb_logs_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle pagination for logs."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    page = int(query.data.split(":")[1])
    admin_id = update.effective_user.id
    await _show_logs_page(query, page=page, admin_id=admin_id)


async def _show_logs_page(query, page: int, admin_id: int, filter_type: str = None, filter_admin: int = None) -> None:
    """Render a page of admin logs."""
    offset = page * LOGS_PER_PAGE

    # Build query based on filters
    # NOTE: Using schema column name 'action' not 'action_type'
    where_clauses = []
    params = []

    if filter_type:
        where_clauses.append("action = ?")
        params.append(filter_type)

    if filter_admin:
        where_clauses.append("admin_id = ?")
        params.append(filter_admin)

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    total_row = await async_fetchone(f"SELECT COUNT(*) as cnt FROM admin_logs {where_sql}", tuple(params))
    total_logs = total_row["cnt"] if total_row else 0
    total_pages = max(1, (total_logs + LOGS_PER_PAGE - 1) // LOGS_PER_PAGE)

    rows = await async_fetchall(
        f"""
        SELECT al.*, u.username as admin_name, u.first_name as admin_first_name
        FROM admin_logs al
        LEFT JOIN users u ON al.admin_id = u.telegram_id
        {where_sql}
        ORDER BY al.created_at DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params + [LOGS_PER_PAGE, offset]),
    )

    filter_desc = ""
    if filter_type or filter_admin:
        parts = []
        if filter_type:
            parts.append(f"Type: {filter_type}")
        if filter_admin:
            parts.append(f"Admin: {filter_admin}")
        filter_desc = f" (Filter: {', '.join(parts)})"

    if not rows:
        text = f"📋 *Admin Logs*{filter_desc}\n\nNo logs found."
        keyboard = InlineKeyboardMarkup([[_back_to_dashboard_button()]])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    lines = [f"📋 *Admin Logs*{filter_desc} (Page {page + 1}/{total_pages})\n"]

    for log in rows:
        admin_name = log.get("admin_first_name") or log.get("admin_name") or str(log["admin_id"])
        lines.append(
            f"📝 #{log['id']} — {log['action']}\n"
            f"  Admin: {admin_name} | {log.get('details', '')[:50]}\n"
            f"  {log.get('created_at', '')}"
        )

    text = "\n".join(lines)

    # Detail buttons for each log
    detail_buttons = []
    for log in rows[:5]:
        detail_buttons.append(
            InlineKeyboardButton(f"📝 #{log['id']}", callback_data=f"admin_log_detail:{log['id']}")
        )

    detail_rows = [detail_buttons[i:i + 3] for i in range(0, len(detail_buttons), 3)]

    # Navigation
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_logs_page:{page - 1}"))
    nav_buttons.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="admin_logs"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"admin_logs_page:{page + 1}"))

    keyboard = InlineKeyboardMarkup(
        detail_rows + [
            nav_buttons,
            [
                InlineKeyboardButton("🔍 Filter", callback_data="admin_logs_filter"),
                InlineKeyboardButton("🗑 Clear Old", callback_data="admin_clear_logs"),
            ],
            [
                InlineKeyboardButton("📤 Export", callback_data="admin_export_logs"),
            ],
            [_back_to_dashboard_button()],
        ]
    )

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_log_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View log details."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    log_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    log_entry = await async_fetchone(
        """
        SELECT al.*, u.username as admin_name, u.first_name as admin_first_name
        FROM admin_logs al
        LEFT JOIN users u ON al.admin_id = u.telegram_id
        WHERE al.id = ?
        """,
        (log_id,),
    )

    if not log_entry:
        await query.edit_message_text(
            "❌ Log entry not found.",
            reply_markup=InlineKeyboardMarkup([[_back_to_logs_button()]]),
        )
        return

    admin_name = log_entry.get("admin_first_name") or log_entry.get("admin_name") or str(log_entry["admin_id"])

    # Parse metadata
    metadata_text = "None"
    if log_entry.get("metadata"):
        try:
            metadata_obj = json.loads(log_entry["metadata"]) if isinstance(log_entry["metadata"], str) else log_entry["metadata"]
            metadata_text = json.dumps(metadata_obj, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            metadata_text = str(log_entry["metadata"])

    text = (
        f"📝 *Log Entry #{log_entry['id']}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Action: `{log_entry['action']}`\n"
        f"Admin: {admin_name} (`{log_entry['admin_id']}`)\n"
        f"Target Type: {log_entry.get('target_type') or 'N/A'}\n"
        f"Target ID: `{log_entry.get('target_id') or 'N/A'}`\n"
        f"Details: {log_entry.get('details') or 'N/A'}\n"
        f"Metadata: `{metadata_text}`\n"
        f"Timestamp: {log_entry.get('created_at', 'N/A')}\n"
    )

    keyboard = InlineKeyboardMarkup([
        [_back_to_logs_button(), _back_to_dashboard_button()],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_logs_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start log filter conversation."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return ConversationHandler.END

    await query.answer()

    # Get available action types for reference
    types = await async_fetchall(
        "SELECT DISTINCT action FROM admin_logs ORDER BY action LIMIT 20"
    )
    type_list = ", ".join(t["action"] for t in types) if types else "None available"

    text = (
        "🔍 *Filter Logs*\n\n"
        "Enter filter criteria in format:\n"
        "`type:action` — filter by action\n"
        "`admin:admin_id` — filter by admin\n"
        "`date:YYYY-MM-DD` — filter by date\n"
        "`type:action admin:123 date:2024-01-01` — combined\n\n"
        f"*Available actions:* {type_list}\n\n"
        "Or type 'all' to clear filters.\n\n"
        "Send /cancel to cancel."
    )

    await query.edit_message_text(text, parse_mode="Markdown")
    return AWAITING_LOG_FILTER


async def handle_log_filter_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle log filter input."""
    if not await admin_guard(update, context):
        return ConversationHandler.END

    input_text = update.message.text.strip()

    if input_text.lower() == "all":
        # Clear filters - show all logs
        context.user_data.pop(TEMP_LOG_FILTER, None)
        # Redirect to logs view
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 View Logs", callback_data="admin_logs")],
            [_back_to_dashboard_button()],
        ])
        await update.message.reply_text("✅ Filters cleared.", reply_markup=keyboard)
        return ConversationHandler.END

    # Parse filter criteria
    filter_type = None
    filter_admin = None
    filter_date = None

    parts = input_text.split()
    for part in parts:
        if part.startswith("type:"):
            filter_type = part[5:]
        elif part.startswith("admin:"):
            try:
                filter_admin = int(part[6:])
            except ValueError:
                await update.message.reply_text("❌ Invalid admin ID. Must be a number.")
                return AWAITING_LOG_FILTER
        elif part.startswith("date:"):
            filter_date = part[5:]
            try:
                datetime.strptime(filter_date, "%Y-%m-%d")
            except ValueError:
                await update.message.reply_text("❌ Invalid date format. Use YYYY-MM-DD.")
                return AWAITING_LOG_FILTER

    # Store filters in context for pagination
    context.user_data[TEMP_LOG_FILTER] = {
        "filter_type": filter_type,
        "filter_admin": filter_admin,
        "filter_date": filter_date,
    }

    # Build the filtered query
    # NOTE: Using schema column name 'action' not 'action_type'
    where_clauses = []
    params = []

    if filter_date:
        start = f"{filter_date}T00:00:00"
        end = f"{filter_date}T23:59:59"
        where_clauses.append("created_at >= ? AND created_at <= ?")
        params.extend([start, end])

    if filter_type:
        where_clauses.append("action = ?")
        params.append(filter_type)
    if filter_admin:
        where_clauses.append("admin_id = ?")
        params.append(filter_admin)

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    rows = await async_fetchall(
        f"""
        SELECT al.*, u.username as admin_name, u.first_name as admin_first_name
        FROM admin_logs al
        LEFT JOIN users u ON al.admin_id = u.telegram_id
        {where_sql}
        ORDER BY al.created_at DESC
        LIMIT 10
        """,
        tuple(params),
    )

    total_row = await async_fetchone(f"SELECT COUNT(*) as cnt FROM admin_logs {where_sql}", tuple(params))
    total = total_row["cnt"] if total_row else 0

    filter_parts = []
    if filter_type:
        filter_parts.append(f"Type: {filter_type}")
    if filter_admin:
        filter_parts.append(f"Admin: {filter_admin}")
    if filter_date:
        filter_parts.append(f"Date: {filter_date}")
    filter_desc = ", ".join(filter_parts)

    if not rows:
        text = f"🔍 *Filtered Logs* ({filter_desc})\n\nNo matching logs found."
    else:
        lines = [f"🔍 *Filtered Logs* ({filter_desc})\nFound: {total} entries\n"]
        for log in rows:
            admin_name = log.get("admin_first_name") or log.get("admin_name") or str(log["admin_id"])
            lines.append(
                f"📝 #{log['id']} — {log['action']}\n"
                f"  Admin: {admin_name} | {log.get('details', '')[:50]}"
            )
        text = "\n".join(lines)

    # Detail buttons
    detail_buttons = []
    for log in rows[:5]:
        detail_buttons.append(
            InlineKeyboardButton(f"📝 #{log['id']}", callback_data=f"admin_log_detail:{log['id']}")
        )
    detail_rows = [detail_buttons[i:i + 3] for i in range(0, len(detail_buttons), 3)]

    keyboard = InlineKeyboardMarkup(
        detail_rows + [
            [InlineKeyboardButton("📋 All Logs", callback_data="admin_logs")],
            [_back_to_dashboard_button()],
        ]
    )

    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return ConversationHandler.END


async def cb_clear_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear old logs (older than 30 days)."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    admin_id = update.effective_user.id

    # Count logs older than 30 days
    cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()

    old_count = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM admin_logs WHERE created_at < ?",
        (cutoff,),
    )
    count = old_count["cnt"] if old_count else 0

    if count == 0:
        await query.answer("No logs older than 30 days to clear.", show_alert=True)
        return

    # Delete old logs
    await async_execute("DELETE FROM admin_logs WHERE created_at < ?", (cutoff,))

    await log_admin_action(
        admin_id, "clear_logs",
        details=f"Cleared {count} log entries older than 30 days"
    )

    await query.answer()

    await query.edit_message_text(
        f"🗑 *Logs Cleared*\n\n"
        f"Deleted {count} log entries older than 30 days.\n"
        f"Cutoff date: {cutoff[:10]}",
        reply_markup=InlineKeyboardMarkup([
            [_back_to_logs_button(), _back_to_dashboard_button()],
        ]),
        parse_mode="Markdown",
    )


async def cb_export_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Export logs as CSV file."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer("Exporting logs...")
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "export_logs", details="Exported admin logs")

    # Fetch recent logs (last 1000)
    logs = await async_fetchall(
        """
        SELECT al.*, u.username as admin_name, u.first_name as admin_first_name
        FROM admin_logs al
        LEFT JOIN users u ON al.admin_id = u.telegram_id
        ORDER BY al.created_at DESC
        LIMIT 1000
        """
    )

    if not logs:
        await query.edit_message_text(
            "📋 No logs to export.",
            reply_markup=InlineKeyboardMarkup([[_back_to_logs_button()]]),
        )
        return

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "ID", "Admin ID", "Admin Name", "Action",
        "Target Type", "Target ID", "Details", "Metadata", "Created At"
    ])

    # Data rows
    for log in logs:
        admin_name = log.get("admin_first_name") or log.get("admin_name") or ""
        metadata = log.get("metadata", "") or ""
        writer.writerow([
            log["id"],
            log["admin_id"],
            admin_name,
            log.get("action", ""),
            log.get("target_type", ""),
            log.get("target_id", ""),
            log.get("details", ""),
            metadata,
            log.get("created_at", ""),
        ])

    # Send as document
    output.seek(0)
    csv_bytes = output.getvalue().encode("utf-8")

    from telegram import InputFile
    import tempfile

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False) as f:
        f.write(csv_bytes)
        temp_path = f.name

    try:
        with open(temp_path, "rb") as f:
            await context.bot.send_document(
                chat_id=admin_id,
                document=f,
                filename=f"admin_logs_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv",
                caption=f"📋 Admin Logs Export ({len(logs)} entries)",
            )
    except Exception as e:
        await query.edit_message_text(
            f"❌ Failed to export logs: {str(e)[:100]}",
            reply_markup=InlineKeyboardMarkup([[_back_to_logs_button()]]),
        )
    finally:
        import os
        os.unlink(temp_path)

    # Update the query message
    await query.edit_message_text(
        f"📤 *Logs Exported*\n\n{len(logs)} entries exported to CSV and sent as document.",
        reply_markup=InlineKeyboardMarkup([
            [_back_to_logs_button(), _back_to_dashboard_button()],
        ]),
        parse_mode="Markdown",
    )


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the current conversation."""
    context.user_data.pop(TEMP_LOG_FILTER, None)
    await update.message.reply_text(
        "❌ Operation cancelled.",
        reply_markup=InlineKeyboardMarkup([[_back_to_dashboard_button()]]),
    )
    return ConversationHandler.END
