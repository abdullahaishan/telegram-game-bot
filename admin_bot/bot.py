"""
Admin Bot - Main Application
Telegram multiplayer game platform administration bot.
Uses python-telegram-bot v20+ async API.
"""

import logging
import sys

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)

from config import ADMIN_IDS, ADMIN_BOT_TOKEN
from database import init_db
from admin_bot.handlers import dashboard, users, wallets, games, promotions, channels, withdrawals, store, logs

logger = logging.getLogger(__name__)


def create_admin_bot() -> Application:
    """Build and configure the Admin Bot Application with all handlers."""

    if not ADMIN_BOT_TOKEN:
        logger.error("ADMIN_BOT_TOKEN is not set in config!")
        sys.exit(1)

    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS is not set - no admin will have access!")

    # Initialize database schema
    init_db()

    application = Application.builder().token(ADMIN_BOT_TOKEN).build()

    # --- Command Handlers ---
    application.add_handler(CommandHandler("start", dashboard.cmd_start))

    # --- Callback Query Handlers ---
    # Dashboard
    application.add_handler(CallbackQueryHandler(dashboard.cb_dashboard, pattern=r"^admin_dashboard$"))
    application.add_handler(CallbackQueryHandler(dashboard.cb_refresh, pattern=r"^admin_refresh$"))

    # Users (non-conversation-entry callbacks only)
    application.add_handler(CallbackQueryHandler(users.cb_users, pattern=r"^admin_users$"))
    application.add_handler(CallbackQueryHandler(users.cb_user_detail, pattern=r"^admin_user_detail:\d+$"))
    application.add_handler(CallbackQueryHandler(users.cb_ban_user, pattern=r"^admin_ban_user:\d+$"))
    application.add_handler(CallbackQueryHandler(users.cb_unban_user, pattern=r"^admin_unban_user:\d+$"))
    application.add_handler(CallbackQueryHandler(users.cb_user_wallet, pattern=r"^admin_user_wallet:\d+$"))
    application.add_handler(CallbackQueryHandler(users.cb_user_sessions, pattern=r"^admin_user_sessions:\d+$"))
    application.add_handler(CallbackQueryHandler(users.cb_users_page, pattern=r"^admin_users_page:\d+$"))
    # NOTE: cb_user_edit_balance is the entry point for the balance_conv ConversationHandler below

    # Wallets (non-conversation-entry callbacks only)
    application.add_handler(CallbackQueryHandler(wallets.cb_wallets, pattern=r"^admin_wallets$"))
    application.add_handler(CallbackQueryHandler(wallets.cb_wallet_detail, pattern=r"^admin_wallet_detail:\d+$"))
    application.add_handler(CallbackQueryHandler(wallets.cb_wallet_add, pattern=r"^admin_wallet_add:\d+:[\d.]+$"))
    application.add_handler(CallbackQueryHandler(wallets.cb_wallet_subtract, pattern=r"^admin_wallet_subtract:\d+:[\d.]+$"))
    # NOTE: cb_wallet_adjust is the entry point for the wallet_conv ConversationHandler below

    # Games
    application.add_handler(CallbackQueryHandler(games.cb_games, pattern=r"^admin_games$"))
    application.add_handler(CallbackQueryHandler(games.cb_game_detail, pattern=r"^admin_game_detail:\d+$"))
    application.add_handler(CallbackQueryHandler(games.cb_approve_game, pattern=r"^admin_approve_game:\d+$"))
    application.add_handler(CallbackQueryHandler(games.cb_reject_game, pattern=r"^admin_reject_game:\d+$"))
    application.add_handler(CallbackQueryHandler(games.cb_disable_game, pattern=r"^admin_disable_game:\d+$"))
    application.add_handler(CallbackQueryHandler(games.cb_enable_game, pattern=r"^admin_enable_game:\d+$"))
    application.add_handler(CallbackQueryHandler(games.cb_active_sessions, pattern=r"^admin_active_sessions$"))
    application.add_handler(CallbackQueryHandler(games.cb_session_detail, pattern=r"^admin_session_detail:\d+$"))
    application.add_handler(CallbackQueryHandler(games.cb_end_session, pattern=r"^admin_end_session:\d+$"))
    application.add_handler(CallbackQueryHandler(games.cb_reload_games, pattern=r"^admin_reload_games$"))

    # Promotions
    application.add_handler(CallbackQueryHandler(promotions.cb_promotions, pattern=r"^admin_promotions$"))
    application.add_handler(CallbackQueryHandler(promotions.cb_promotion_detail, pattern=r"^admin_promotion_detail:\d+$"))
    application.add_handler(CallbackQueryHandler(promotions.cb_approve_promotion, pattern=r"^admin_approve_promotion:\d+$"))
    application.add_handler(CallbackQueryHandler(promotions.cb_reject_promotion, pattern=r"^admin_reject_promotion:\d+$"))
    application.add_handler(CallbackQueryHandler(promotions.cb_cancel_promotion, pattern=r"^admin_cancel_promotion:\d+$"))
    application.add_handler(CallbackQueryHandler(promotions.cb_promotion_queue, pattern=r"^admin_promotion_queue$"))
    application.add_handler(CallbackQueryHandler(promotions.cb_rotate_promotions, pattern=r"^admin_rotate_promotions$"))

    # Channels (non-conversation-entry callbacks only)
    application.add_handler(CallbackQueryHandler(channels.cb_channels, pattern=r"^admin_channels$"))
    application.add_handler(CallbackQueryHandler(channels.cb_remove_channel, pattern=r"^admin_remove_channel:\d+$"))
    application.add_handler(CallbackQueryHandler(channels.cb_toggle_channel, pattern=r"^admin_toggle_channel:\d+$"))
    application.add_handler(CallbackQueryHandler(channels.cb_channel_info, pattern=r"^admin_channel_info$"))
    # NOTE: cb_add_channel is the entry point for the channel_conv ConversationHandler below
    # NOTE: cb_reorder_channels is the entry point for the channel_reorder_conv ConversationHandler below

    # Withdrawals (non-conversation-entry callbacks only)
    application.add_handler(CallbackQueryHandler(withdrawals.cb_withdrawals, pattern=r"^admin_withdrawals$"))
    application.add_handler(CallbackQueryHandler(withdrawals.cb_withdrawal_detail, pattern=r"^admin_withdrawal_detail:\d+$"))
    application.add_handler(CallbackQueryHandler(withdrawals.cb_approve_withdrawal, pattern=r"^admin_approve_withdrawal:\d+$"))
    application.add_handler(CallbackQueryHandler(withdrawals.cb_withdrawals_history, pattern=r"^admin_withdrawals_history$"))
    # NOTE: cb_reject_withdrawal is the entry point for the withdrawal_reject_conv ConversationHandler below
    # NOTE: cb_withdrawal_note is the entry point for the withdrawal_note_conv ConversationHandler below

    # Store (non-conversation-entry callbacks only)
    application.add_handler(CallbackQueryHandler(store.cb_store, pattern=r"^admin_store$"))
    application.add_handler(CallbackQueryHandler(store.cb_toggle_store_item, pattern=r"^admin_toggle_store_item:\d+$"))
    application.add_handler(CallbackQueryHandler(store.cb_store_purchases, pattern=r"^admin_store_purchases$"))
    application.add_handler(CallbackQueryHandler(store.cb_store_item_detail, pattern=r"^admin_store_item_detail:\d+$"))
    # NOTE: cb_add_store_item is the entry point for the store_add_conv ConversationHandler below
    # NOTE: cb_edit_store_item is the entry point for the store_edit_conv ConversationHandler below

    # Logs (non-conversation-entry callbacks only)
    application.add_handler(CallbackQueryHandler(logs.cb_logs, pattern=r"^admin_logs$"))
    application.add_handler(CallbackQueryHandler(logs.cb_log_detail, pattern=r"^admin_log_detail:\d+$"))
    application.add_handler(CallbackQueryHandler(logs.cb_logs_page, pattern=r"^admin_logs_page:\d+$"))
    application.add_handler(CallbackQueryHandler(logs.cb_clear_logs, pattern=r"^admin_clear_logs$"))
    application.add_handler(CallbackQueryHandler(logs.cb_export_logs, pattern=r"^admin_export_logs$"))
    # NOTE: cb_logs_filter is the entry point for the logs_filter_conv ConversationHandler below

    # --- Conversation Handlers for text input ---

    # Balance edit conversation
    balance_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(users.cb_user_edit_balance, pattern=r"^admin_user_edit_balance:\d+$")],
        states={
            users.AWAITING_BALANCE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, users.handle_balance_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", users.cancel_conversation)],
        per_user=True,
        per_chat=True,
    )
    application.add_handler(balance_conv)

    # Wallet adjust conversation
    wallet_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(wallets.cb_wallet_adjust, pattern=r"^admin_wallet_adjust:\d+$")],
        states={
            wallets.AWAITING_WALLET_ADJUST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, wallets.handle_wallet_adjust_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", wallets.cancel_conversation)],
        per_user=True,
        per_chat=True,
    )
    application.add_handler(wallet_conv)

    # Add store item conversation
    store_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(store.cb_add_store_item, pattern=r"^admin_add_store_item$")],
        states={
            store.AWAITING_ITEM_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, store.handle_item_name),
            ],
            store.AWAITING_ITEM_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, store.handle_item_description),
            ],
            store.AWAITING_ITEM_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, store.handle_item_price),
            ],
            store.AWAITING_ITEM_IMAGE: [
                MessageHandler(filters.PHOTO | filters.TEXT, store.handle_item_image),
            ],
        },
        fallbacks=[CommandHandler("cancel", store.cancel_conversation)],
        per_user=True,
        per_chat=True,
    )
    application.add_handler(store_add_conv)

    # Edit store item conversation
    store_edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(store.cb_edit_store_item, pattern=r"^admin_edit_store_item:\d+$")],
        states={
            store.AWAITING_EDIT_FIELD: [
                CallbackQueryHandler(store.handle_edit_field, pattern=r"^store_edit_field:"),
            ],
            store.AWAITING_EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, store.handle_edit_value),
                MessageHandler(filters.PHOTO, store.handle_edit_value),
            ],
        },
        fallbacks=[CommandHandler("cancel", store.cancel_conversation)],
        per_user=True,
        per_chat=True,
    )
    application.add_handler(store_edit_conv)

    # Add channel conversation
    channel_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(channels.cb_add_channel, pattern=r"^admin_add_channel$")],
        states={
            channels.AWAITING_CHANNEL_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, channels.handle_channel_id_input),
            ],
            channels.AWAITING_CHANNEL_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, channels.handle_channel_title_input),
            ],
            channels.AWAITING_CHANNEL_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, channels.handle_channel_link_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", channels.cancel_conversation)],
        per_user=True,
        per_chat=True,
    )
    application.add_handler(channel_conv)

    # Channel reorder conversation
    channel_reorder_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(channels.cb_reorder_channels, pattern=r"^admin_reorder_channels$")],
        states={
            channels.AWAITING_CHANNEL_ORDER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, channels.handle_channel_order_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", channels.cancel_conversation)],
        per_user=True,
        per_chat=True,
    )
    application.add_handler(channel_reorder_conv)

    # Withdrawal rejection conversation
    withdrawal_reject_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(withdrawals.cb_reject_withdrawal, pattern=r"^admin_reject_withdrawal:\d+$")],
        states={
            withdrawals.AWAITING_REJECTION_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdrawals.handle_rejection_reason),
            ],
        },
        fallbacks=[CommandHandler("cancel", withdrawals.cancel_conversation)],
        per_user=True,
        per_chat=True,
    )
    application.add_handler(withdrawal_reject_conv)

    # Withdrawal note conversation
    withdrawal_note_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(withdrawals.cb_withdrawal_note, pattern=r"^admin_withdrawal_note$")],
        states={
            withdrawals.AWAITING_WITHDRAWAL_NOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdrawals.handle_withdrawal_note_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", withdrawals.cancel_conversation)],
        per_user=True,
        per_chat=True,
    )
    application.add_handler(withdrawal_note_conv)

    # Logs filter conversation
    logs_filter_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(logs.cb_logs_filter, pattern=r"^admin_logs_filter$")],
        states={
            logs.AWAITING_LOG_FILTER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, logs.handle_log_filter_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", logs.cancel_conversation)],
        per_user=True,
        per_chat=True,
    )
    application.add_handler(logs_filter_conv)

    return application
