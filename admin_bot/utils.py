"""
Admin Bot Shared Utilities

Contains admin_guard, log_admin_action, and is_admin
to avoid circular imports between bot.py and handlers.
"""

import logging
import json

from config import ADMIN_IDS
from database import async_execute

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    """Check if the user is an authorized admin."""
    return user_id in ADMIN_IDS


async def admin_guard(update, context) -> bool:
    """
    Guard that checks if the user is an admin.
    Returns True if admin, False otherwise.
    Sends 'Access denied' message to non-admins.
    """
    user_id = None
    if update.effective_user:
        user_id = update.effective_user.id

    if not is_admin(user_id):
        if update.message:
            await update.message.reply_text("⛔ Access denied")
        elif update.callback_query:
            await update.callback_query.answer("⛔ Access denied", show_alert=True)
        return False
    return True


async def log_admin_action(
    admin_id: int,
    action: str,
    target_type: str = None,
    target_id: str = None,
    details: str = "",
    metadata: dict = None,
) -> None:
    """Log an admin action to the admin_logs table."""
    try:
        metadata_str = json.dumps(metadata) if metadata else None
        await async_execute(
            "INSERT INTO admin_logs (admin_id, action, target_type, target_id, details, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            (admin_id, action, target_type, target_id, details, metadata_str)
        )
    except Exception as e:
        logger.error(f"Failed to log admin action: {e}")
