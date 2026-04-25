"""
Main Game Bot Application

Initializes the Telegram bot, registers all handlers including the Game Builder,
sets up engine components, and provides a factory function for the main runner.
"""

import asyncio
import logging
import sys

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import GAME_BOT_TOKEN, ADMIN_IDS
from database import init_db
from game_bot.engine.renderer import GameRenderer
from game_bot.engine.plugin_loader import PluginLoader
from game_bot.engine.session import SessionManager

from game_bot.handlers.start import start_handler, referral_start_handler
from game_bot.handlers.menu import menu_callback_handler
from game_bot.handlers.games import (
    browse_games_handler,
    select_game_handler,
    create_room_handler,
    create_room_confirm_handler,
    join_room_handler,
    leave_room_handler,
    start_game_handler,
    game_action_handler,
)
from game_bot.handlers.wallet import (
    wallet_view_handler,
    wallet_history_handler,
    wallet_deposit_handler,
    wallet_claim_share_handler,
)
from game_bot.handlers.marketplace import (
    marketplace_view_handler,
    buy_item_handler,
    marketplace_callback_handler,
)
from game_bot.handlers.profile import (
    profile_view_handler,
    profile_callback_handler,
)
from game_bot.handlers.promotions import (
    promotions_callback_handler,
    handle_promotion_duration_callback,
)
from game_bot.handlers.withdrawals import (
    withdrawals_callback_handler,
    withdrawal_message_handler,
)
from game_bot.handlers.channels import (
    channels_callback_handler,
    verify_joined_handler,
)
from game_bot.handlers.builder import (
    builder_entry_handler,
    builder_callback_handler,
    builder_text_handler,
    builder_drafts_handler,
)

logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and notify admins."""
    logger.error("Exception while handling an update:", exc_info=context.error)

    if update and isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ An unexpected error occurred. The admins have been notified. "
                "Please try again later."
            )
        except Exception:
            pass

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"🚨 Game Bot Error\n\n{context.error}",
            )
        except Exception:
            logger.error("Failed to notify admin %s about error.", admin_id)


def create_game_bot() -> Application:
    """Build and configure the Game Bot Application with all handlers."""

    application = (
        Application.builder()
        .token(GAME_BOT_TOKEN)
        .build()
    )

    # Initialize engine components
    plugin_loader = PluginLoader()
    plugin_loader.discover_all()
    logger.info("Loaded %d game plugins", len(plugin_loader.list_games()))

    renderer = GameRenderer()

    # Store engine components in bot_data for access by handlers
    application.bot_data["plugin_loader"] = plugin_loader
    application.bot_data["renderer"] = renderer
    application.bot_data["session_manager"] = None  # Will be set in run.py

    # --- Command Handlers ---
    application.add_handler(CommandHandler("start", referral_start_handler))

    # --- Callback Query Handlers (ordered by pattern specificity) ---

    # Game Builder callbacks (high priority - specific patterns)
    application.add_handler(CallbackQueryHandler(builder_entry_handler, pattern=r"^builder_start$"))
    application.add_handler(CallbackQueryHandler(builder_callback_handler, pattern=r"^builder:"))
    application.add_handler(CallbackQueryHandler(builder_callback_handler, pattern=r"^builder_edit_field:"))
    application.add_handler(CallbackQueryHandler(builder_callback_handler, pattern=r"^builder_confirm_field$"))
    application.add_handler(CallbackQueryHandler(builder_callback_handler, pattern=r"^builder_cancel_edit$"))
    application.add_handler(CallbackQueryHandler(builder_drafts_handler, pattern=r"^builder_drafts$"))
    application.add_handler(CallbackQueryHandler(builder_callback_handler, pattern=r"^builder_load_draft:"))
    application.add_handler(CallbackQueryHandler(builder_callback_handler, pattern=r"^builder_delete_draft:"))

    # Game-related callbacks
    application.add_handler(CallbackQueryHandler(browse_games_handler, pattern=r"^browse_games$"))
    application.add_handler(CallbackQueryHandler(select_game_handler, pattern=r"^select_game:.+$"))
    application.add_handler(CallbackQueryHandler(create_room_handler, pattern=r"^create_room:.+$"))
    application.add_handler(CallbackQueryHandler(create_room_confirm_handler, pattern=r"^create_room_confirm:.+:.+$"))
    application.add_handler(CallbackQueryHandler(join_room_handler, pattern=r"^join_room:.+$"))
    application.add_handler(CallbackQueryHandler(leave_room_handler, pattern=r"^leave_room:.+$"))
    application.add_handler(CallbackQueryHandler(start_game_handler, pattern=r"^start_game:.+$"))
    application.add_handler(CallbackQueryHandler(game_action_handler, pattern=r"^game_action"))

    # Wallet callbacks
    application.add_handler(CallbackQueryHandler(wallet_view_handler, pattern=r"^wallet_view$"))
    application.add_handler(CallbackQueryHandler(wallet_history_handler, pattern=r"^wallet_history"))
    application.add_handler(CallbackQueryHandler(wallet_deposit_handler, pattern=r"^wallet_deposit_info$"))
    application.add_handler(CallbackQueryHandler(wallet_claim_share_handler, pattern=r"^wallet_claim_share$"))

    # Marketplace callbacks
    application.add_handler(CallbackQueryHandler(marketplace_view_handler, pattern=r"^marketplace_view$"))
    application.add_handler(CallbackQueryHandler(buy_item_handler, pattern=r"^buy_item:.+$"))
    application.add_handler(CallbackQueryHandler(marketplace_callback_handler, pattern=r"^buy_promotion$|^buy_profile_pack$|^buy_game_ownership$|^buy_private_room$|^buy_featured_slot$|^buy_game_creation_license$|^promotion_set_channel$|^promotion_confirm$"))

    # Profile callbacks
    application.add_handler(CallbackQueryHandler(profile_view_handler, pattern=r"^profile_view$"))
    application.add_handler(CallbackQueryHandler(profile_callback_handler, pattern=r"^profile_"))

    # Promotion callbacks
    application.add_handler(CallbackQueryHandler(promotions_callback_handler, pattern=r"^promotion_status$|^promotion_create$|^promotion_cancel:.+$"))
    application.add_handler(CallbackQueryHandler(handle_promotion_duration_callback, pattern=r"^promo_duration:.+$"))

    # Withdrawal callbacks
    application.add_handler(CallbackQueryHandler(withdrawals_callback_handler, pattern=r"^withdraw_"))

    # Channel callbacks
    application.add_handler(CallbackQueryHandler(channels_callback_handler, pattern=r"^check_membership$|^show_required_channels$"))
    application.add_handler(CallbackQueryHandler(verify_joined_handler, pattern=r"^verify_joined$"))

    # Menu callbacks (catch-all for navigation)
    application.add_handler(CallbackQueryHandler(menu_callback_handler, pattern=r"^(main_menu|games_menu|wallet_menu|marketplace_menu|profile_menu|promote_menu|withdraw_menu|builder_menu|back_to_main)$"))

    # --- Message Handlers ---
    # Handle text messages - builder text input + withdrawal detail entry
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        builder_text_handler,
    ))

    # --- Error Handler ---
    application.add_error_handler(error_handler)

    return application
