"""
Game Browsing, Creation, and Joining Handlers

Handles game browsing, game detail viewing, room creation,
room joining/leaving, game start, and routing game actions
to the SessionManager.
"""

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    CURRENCY_NAME,
    WIN_REWARD,
    REQUIRED_CHANNELS_ENABLED,
)
from database import async_execute, async_fetchone, async_fetchall, async_transaction
from game_bot.handlers.start import check_required_channels

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Browse Games
# ──────────────────────────────────────────────

async def browse_games_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all available/approved games with inline buttons."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    not_joined = await check_required_channels(user_id, context.bot)
    if not_joined:
        await _show_channel_warning(query, not_joined)
        return

    games = await async_fetchall(
        "SELECT slug, name, description, min_players, max_players, entry_fee_sar, reward_sar "
        "FROM games WHERE is_approved = 1 AND is_active = 1 ORDER BY name ASC"
    )

    if not games:
        text = (
            "🎮 <b>Games</b>\n\n"
            "No games are currently available. Check back soon!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    text = "🎮 <b>Available Games</b>\n\nSelect a game to view details:\n\n"

    keyboard_rows = []
    for game in games:
        fee_text = f"{game['entry_fee_sar']:.2f} {CURRENCY_NAME}" if game["entry_fee_sar"] > 0 else "Free"
        text += (
            f"🔹 <b>{game['name']}</b>\n"
            f"   Players: {game['min_players']}-{game['max_players']} | "
            f"Fee: {fee_text} | Reward: {game['reward_sar']:.2f} {CURRENCY_NAME}\n\n"
        )
        keyboard_rows.append([
            InlineKeyboardButton(
                f"▶ {game['name']} ({fee_text})",
                callback_data=f"select_game:{game['slug']}",
            )
        ])

    keyboard_rows.append([
        InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ──────────────────────────────────────────────
# Select Game (View Details)
# ──────────────────────────────────────────────

async def select_game_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show game details for a selected game."""
    query = update.callback_query
    await query.answer()

    slug = query.data.split(":", 1)[1]

    game = await async_fetchone(
        "SELECT slug, name, description, min_players, max_players, entry_fee_sar, reward_sar, "
        "is_active FROM games WHERE slug = ? AND is_approved = 1",
        (slug,),
    )

    if not game:
        text = "❌ Game not found or no longer available."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Browse Games", callback_data="browse_games")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    active_sessions = await async_fetchall(
        "SELECT gs.id, gs.mode, g.max_players, "
        "(SELECT COUNT(*) FROM game_players gp WHERE gp.session_id = gs.id AND gp.is_alive = 1) as current_players "
        "FROM game_sessions gs "
        "JOIN games g ON gs.game_id = g.id "
        "WHERE gs.game_id = (SELECT id FROM games WHERE slug = ?) AND gs.status = 'waiting' AND gs.mode = 'public' "
        "ORDER BY gs.created_at ASC LIMIT 5",
        (slug,),
    )

    fee_text = f"{game['entry_fee_sar']:.2f} {CURRENCY_NAME}" if game["entry_fee_sar"] > 0 else "Free"
    reward_text = f"{game['reward_sar']:.2f} {CURRENCY_NAME}"

    text = (
        f"🎮 <b>{game['name']}</b>\n\n"
        f"📝 {game['description']}\n\n"
        f"👥 Players: <b>{game['min_players']} - {game['max_players']}</b>\n"
        f"💰 Entry Fee: <b>{fee_text}</b>\n"
        f"🏆 Reward: <b>{reward_text}</b>\n"
    )

    if active_sessions:
        text += "\n🟢 <b>Open Rooms:</b>\n"
        for session in active_sessions:
            text += (
                f"  • Room {str(session['id'])[:8]}... "
                f"({session['current_players']}/{session['max_players']} players)\n"
            )

    keyboard_rows = [
        [InlineKeyboardButton("🏠 Create New Room", callback_data=f"create_room:{slug}")],
    ]

    for session in active_sessions:
        keyboard_rows.append([
            InlineKeyboardButton(
                f"Join Room {str(session['id'])[:8]}... ({session['current_players']}/{session['max_players']})",
                callback_data=f"join_room:{session['id']}",
            )
        ])

    keyboard_rows.append([
        InlineKeyboardButton("🔙 Browse Games", callback_data="browse_games"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ──────────────────────────────────────────────
# Create Room
# ──────────────────────────────────────────────

async def create_room_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show room creation options (public/private)."""
    query = update.callback_query
    await query.answer()

    slug = query.data.split(":", 1)[1]

    game = await async_fetchone(
        "SELECT slug, name, entry_fee_sar, min_players, max_players FROM games "
        "WHERE slug = ? AND is_approved = 1 AND is_active = 1",
        (slug,),
    )

    if not game:
        text = "❌ Game not found."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Browse Games", callback_data="browse_games")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    user_id = update.effective_user.id
    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    fee_text = f"{game['entry_fee_sar']:.2f} {CURRENCY_NAME}" if game["entry_fee_sar"] > 0 else "Free"

    text = (
        f"🏠 <b>Create Room — {game['name']}</b>\n\n"
        f"💰 Entry Fee: <b>{fee_text}</b>\n"
        f"💰 Your Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n"
        f"👥 Players: {game['min_players']}-{game['max_players']}\n\n"
        f"Choose room visibility:"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🌐 Public Room",
                callback_data=f"create_room_confirm:{slug}:public",
            ),
            InlineKeyboardButton(
                "🔒 Private Room",
                callback_data=f"create_room_confirm:{slug}:private",
            ),
        ],
        [
            InlineKeyboardButton("🔙 Back to Game", callback_data=f"select_game:{slug}"),
        ],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def create_room_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm room creation and create session via SessionManager."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Invalid request.", show_alert=True)
        return

    _, slug, mode = parts

    game = await async_fetchone(
        "SELECT id, slug, name, entry_fee_sar, min_players, max_players FROM games "
        "WHERE slug = ? AND is_approved = 1 AND is_active = 1",
        (slug,),
    )

    if not game:
        await query.answer("Game not found.", show_alert=True)
        return

    user_id = update.effective_user.id

    not_joined = await check_required_channels(user_id, context.bot)
    if not_joined:
        await _show_channel_warning(query, not_joined)
        return

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if game["entry_fee_sar"] > 0 and balance < game["entry_fee_sar"]:
        text = (
            f"❌ <b>Insufficient Balance</b>\n\n"
            f"Entry fee: <b>{game['entry_fee_sar']:.2f} {CURRENCY_NAME}</b>\n"
            f"Your balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n\n"
            f"You need at least <b>{game['entry_fee_sar'] - balance:.2f} {CURRENCY_NAME}</b> more."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu")],
            [InlineKeyboardButton("🔙 Back to Game", callback_data=f"select_game:{slug}")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    session_manager = context.bot_data.get("session_manager")
    if not session_manager:
        await query.answer("Session manager not available.", show_alert=True)
        return

    try:
        session = session_manager.create_session(
            game_slug=slug,
            chat_id=query.message.chat_id,
            creator_id=user_id,
            mode=mode,
            visibility="open",
        )
        session_id = session.session_id
    except Exception as e:
        logger.error("Failed to create session: %s", e, exc_info=True)
        await query.answer("Failed to create room. Please try again.", show_alert=True)
        return

    if game["entry_fee_sar"] > 0:
        async with async_transaction():
            await async_execute(
                "UPDATE wallets SET balance = balance - ?, updated_at = ? WHERE user_id = ?",
                (game["entry_fee_sar"], datetime.utcnow().isoformat(), user_id),
            )
            await async_execute(
                "INSERT INTO transactions (user_id, type, amount, description, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, "game_entry", -game["entry_fee_sar"],
                 f"Entry fee for {game['name']} room", datetime.utcnow().isoformat()),
            )

    async with async_transaction():
        await async_execute(
            "INSERT INTO game_players (session_id, user_id, is_alive, joined_at) "
            "VALUES (?, ?, 1, ?)",
            (session_id, user_id, datetime.utcnow().isoformat()),
        )

    mode_emoji = "🌐" if mode == "public" else "🔒"
    fee_display = f"{game['entry_fee_sar']:.2f} {CURRENCY_NAME}" if game["entry_fee_sar"] > 0 else "Free"

    text = (
        f"✅ <b>Room Created!</b>\n\n"
        f"{mode_emoji} <b>{game['name']}</b> — {mode.title()} Room\n"
        f"🆔 Room ID: <code>{str(session_id)[:8]}</code>\n"
        f"💰 Entry Fee: {fee_display}\n"
        f"👥 Players: 1/{game['max_players']}\n\n"
        f"Waiting for players to join...\n\n"
        f"Share this room ID with friends to invite them!"
    )

    keyboard_rows = [
        [InlineKeyboardButton("▶ Start Game", callback_data=f"start_game:{session_id}")],
    ]

    if mode == "public":
        keyboard_rows.append([
            InlineKeyboardButton("🔄 Refresh Room", callback_data=f"join_room:{session_id}"),
        ])

    keyboard_rows.append([
        InlineKeyboardButton("🚪 Leave Room", callback_data=f"leave_room:{session_id}"),
    ])
    keyboard_rows.append([
        InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ──────────────────────────────────────────────
# Join Room
# ──────────────────────────────────────────────

async def join_room_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Join a game room / view waiting room."""
    query = update.callback_query
    await query.answer()

    session_id = query.data.split(":", 1)[1]
    user_id = update.effective_user.id

    not_joined = await check_required_channels(user_id, context.bot)
    if not_joined:
        await _show_channel_warning(query, not_joined)
        return

    session = await async_fetchone(
        "SELECT gs.id, gs.game_id, gs.mode, gs.status, "
        "gs.entry_fee, g.name as game_name, g.max_players, g.min_players, "
        "(SELECT COUNT(*) FROM game_players gp WHERE gp.session_id = gs.id AND gp.is_alive = 1) as current_players "
        "FROM game_sessions gs "
        "JOIN games g ON gs.game_id = g.id "
        "WHERE gs.id = ?",
        (session_id,),
    )

    if not session:
        await query.answer("Room not found.", show_alert=True)
        return

    existing_player = await async_fetchone(
        "SELECT user_id FROM game_players WHERE session_id = ? AND user_id = ? AND is_alive = 1",
        (session_id, user_id),
    )

    if existing_player:
        await _show_waiting_room(query, session, user_id, context)
        return

    if session["status"] != "waiting":
        await query.answer("This room is no longer accepting players.", show_alert=True)
        return

    if session["current_players"] >= session["max_players"]:
        await query.answer("This room is full.", show_alert=True)
        return

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    if session["entry_fee"] > 0 and balance < session["entry_fee"]:
        text = (
            f"❌ <b>Insufficient Balance</b>\n\n"
            f"Entry fee: <b>{session['entry_fee']:.2f} {CURRENCY_NAME}</b>\n"
            f"Your balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n\n"
            f"You need at least <b>{session['entry_fee'] - balance:.2f} {CURRENCY_NAME}</b> more."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    async with async_transaction():
        if session["entry_fee"] > 0:
            await async_execute(
                "UPDATE wallets SET balance = balance - ?, updated_at = ? WHERE user_id = ?",
                (session["entry_fee"], datetime.utcnow().isoformat(), user_id),
            )
            await async_execute(
                "INSERT INTO transactions (user_id, type, amount, description, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, "game_entry", -session["entry_fee"],
                 f"Entry fee for {session['game_name']} room", datetime.utcnow().isoformat()),
            )

        await async_execute(
            "INSERT INTO game_players (session_id, user_id, is_alive, joined_at) "
            "VALUES (?, ?, 1, ?)",
            (session_id, user_id, datetime.utcnow().isoformat()),
        )

    updated_session = await async_fetchone(
        "SELECT gs.id, gs.game_id, gs.mode, gs.status, "
        "gs.entry_fee, g.name as game_name, g.max_players, g.min_players, "
        "(SELECT COUNT(*) FROM game_players gp WHERE gp.session_id = gs.id AND gp.is_alive = 1) as current_players "
        "FROM game_sessions gs "
        "JOIN games g ON gs.game_id = g.id "
        "WHERE gs.id = ?",
        (session_id,),
    )

    await _show_waiting_room(query, updated_session, user_id, context)


# ──────────────────────────────────────────────
# Leave Room
# ──────────────────────────────────────────────

async def leave_room_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Leave a game room."""
    query = update.callback_query
    await query.answer()

    session_id = query.data.split(":", 1)[1]
    user_id = update.effective_user.id

    session = await async_fetchone(
        "SELECT gs.id, gs.status, gs.entry_fee, "
        "g.name as game_name, "
        "(SELECT COUNT(*) FROM game_players gp WHERE gp.session_id = gs.id AND gp.is_alive = 1) as current_players "
        "FROM game_sessions gs JOIN games g ON gs.game_id = g.id "
        "WHERE gs.id = ?",
        (session_id,),
    )

    if not session:
        await query.answer("Room not found.", show_alert=True)
        return

    player = await async_fetchone(
        "SELECT user_id FROM game_players WHERE session_id = ? AND user_id = ? AND is_alive = 1",
        (session_id, user_id),
    )

    if not player:
        await query.answer("You are not in this room.", show_alert=True)
        return

    if session["status"] in ("active", "completed"):
        await query.answer("Cannot leave a game that is in progress or completed.", show_alert=True)
        return

    async with async_transaction():
        await async_execute(
            "UPDATE game_players SET is_alive = 0 "
            "WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        )

        if session["entry_fee"] > 0:
            await async_execute(
                "UPDATE wallets SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
                (session["entry_fee"], datetime.utcnow().isoformat(), user_id),
            )
            await async_execute(
                "INSERT INTO transactions (user_id, type, amount, description, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, "refund", session["entry_fee"],
                 f"Refund: left {session['game_name']} room", datetime.utcnow().isoformat()),
            )

    # Check remaining players
    remaining = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM game_players WHERE session_id = ? AND is_alive = 1",
        (session_id,),
    )

    if remaining and remaining["cnt"] <= 0:
        await async_execute(
            "UPDATE game_sessions SET status = 'cancelled' WHERE id = ?",
            (session_id,),
        )
        text = (
            "🚪 <b>Room Disbanded</b>\n\n"
            f"You left the room and it has been disbanded (no remaining players).\n"
            f"Entry fee has been refunded."
        )
    else:
        text = (
            "🚪 <b>Left Room</b>\n\n"
            f"You have left the {session['game_name']} room.\n"
            f"Entry fee has been refunded."
        )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Browse Games", callback_data="browse_games")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ──────────────────────────────────────────────
# Start Game
# ──────────────────────────────────────────────

async def start_game_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the game (any player in the room can do this)."""
    query = update.callback_query
    await query.answer()

    session_id = query.data.split(":", 1)[1]
    user_id = update.effective_user.id

    session = await async_fetchone(
        "SELECT gs.id, gs.status, gs.game_id, "
        "g.name as game_name, g.min_players, g.max_players, g.slug as game_slug, "
        "(SELECT COUNT(*) FROM game_players gp WHERE gp.session_id = gs.id AND gp.is_alive = 1) as current_players "
        "FROM game_sessions gs JOIN games g ON gs.game_id = g.id "
        "WHERE gs.id = ?",
        (session_id,),
    )

    if not session:
        await query.answer("Room not found.", show_alert=True)
        return

    if session["status"] != "waiting":
        await query.answer("Game has already started or is not in waiting state.", show_alert=True)
        return

    if session["current_players"] < session["min_players"]:
        await query.answer(
            f"Need at least {session['min_players']} players to start. "
            f"Currently have {session['current_players']}.",
            show_alert=True,
        )
        return

    await async_execute(
        "UPDATE game_sessions SET status = 'active', started_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), session_id),
    )

    players = await async_fetchall(
        "SELECT user_id FROM game_players WHERE session_id = ? AND is_alive = 1",
        (session_id,),
    )

    session_manager = context.bot_data.get("session_manager")
    if session_manager:
        try:
            session_manager.start_session(session_id)
        except Exception as e:
            logger.error("SessionManager.start_session failed for %s: %s", session_id, e)

    text = (
        f"🎮 <b>Game Started!</b>\n\n"
        f"🟢 <b>{session['game_name']}</b>\n"
        f"👥 Players: {len(players)}/{session['max_players']}\n\n"
        f"The game is now in progress. Good luck! 🍀"
    )

    for player in players:
        if player["user_id"] != user_id:
            try:
                await context.bot.send_message(
                    chat_id=player["user_id"],
                    text=f"🎮 <b>{session['game_name']}</b> has started!\n\nGood luck! 🍀",
                    parse_mode="HTML",
                )
            except Exception:
                logger.warning("Could not notify player %s.", player["user_id"])

    game_renderer: "GameRenderer" = context.bot_data.get("game_renderer")
    if game_renderer:
        try:
            render_result = await game_renderer.render_game_start(
                session_id=session_id,
                game_slug=session["game_slug"],
                players=[p["user_id"] for p in players],
            )
            if render_result:
                text += f"\n\n{render_result}"
        except Exception as e:
            logger.warning("GameRenderer render failed: %s", e)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 Game Action", callback_data=f"game_action:play:{session_id}")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ──────────────────────────────────────────────
# Game Action Router
# ──────────────────────────────────────────────

async def game_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all game callback actions to SessionManager.handle_game_callback()."""
    query = update.callback_query
    await query.answer()

    session_manager = context.bot_data.get("session_manager")
    if not session_manager:
        await query.answer("Session manager not available.", show_alert=True)
        return

    try:
        result = session_manager.handle_game_callback(
            session_id=query.data.split(":")[-1],
            user_id=update.effective_user.id,
            action=query.data,
        )

        if result and isinstance(result, dict):
            text = result.get("text", "")
            reply_markup = result.get("reply_markup")

            if text:
                try:
                    await query.edit_message_text(
                        text,
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                    )
                except Exception:
                    if reply_markup:
                        await query.message.reply_text(
                            text,
                            parse_mode="HTML",
                            reply_markup=reply_markup,
                        )
                    else:
                        await query.message.reply_text(text, parse_mode="HTML")
        elif result and isinstance(result, str):
            await query.answer(result, show_alert=True)

    except Exception as e:
        logger.error("Game action handler error: %s", e, exc_info=True)
        await query.answer("An error occurred processing your action.", show_alert=True)


# ──────────────────────────────────────────────
# Helper Functions
# ──────────────────────────────────────────────

async def _show_waiting_room(query, session, user_id, context) -> None:
    """Show the waiting room with player list."""
    players = await async_fetchall(
        "SELECT gp.user_id, gp.joined_at, u.first_name, u.username "
        "FROM game_players gp "
        "JOIN users u ON gp.user_id = u.id "
        "WHERE gp.session_id = ? AND gp.is_alive = 1 "
        "ORDER BY gp.joined_at ASC",
        (session["id"],),
    )

    player_list = ""
    for i, p in enumerate(players, 1):
        name = p["first_name"] or p["username"] or f"Player {i}"
        you = " (You)" if p["user_id"] == user_id else ""
        player_list += f"  {i}. {name}{you}\n"

    mode_emoji = "🌐" if session["mode"] == "public" else "🔒"
    fee_display = f"{session['entry_fee']:.2f} {CURRENCY_NAME}" if session["entry_fee"] > 0 else "Free"

    text = (
        f"🟢 <b>Waiting Room — {session['game_name']}</b>\n\n"
        f"{mode_emoji} Mode: {session['mode'].title()}\n"
        f"💰 Entry Fee: {fee_display}\n"
        f"👥 Players: {session['current_players']}/{session['max_players']}\n"
        f"📋 Min. to Start: {session['min_players']}\n\n"
        f"<b>Players:</b>\n{player_list}\n"
    )

    if session["current_players"] < session["min_players"]:
        text += f"⏳ Waiting for {session['min_players'] - session['current_players']} more player(s)...\n"

    keyboard_rows = []

    if session["current_players"] >= session["min_players"]:
        keyboard_rows.append([
            InlineKeyboardButton("▶ Start Game", callback_data=f"start_game:{session['id']}"),
        ])

    keyboard_rows.append([
        InlineKeyboardButton("🔄 Refresh", callback_data=f"join_room:{session['id']}"),
    ])
    keyboard_rows.append([
        InlineKeyboardButton("🚪 Leave Room", callback_data=f"leave_room:{session['id']}"),
    ])
    keyboard_rows.append([
        InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _show_channel_warning(query, not_joined) -> None:
    """Show channel join warning."""
    from game_bot.handlers.start import build_channels_keyboard

    channels_text = "⚠️ <b>Join Required Channels First</b>\n\n"
    channels_text += "You must join the following channels before playing:\n\n"
    for ch in not_joined:
        channels_text += f"🔹 {ch['channel_name']}\n"
    channels_text += "\nJoin all channels, then click the button below."

    keyboard = build_channels_keyboard(not_joined)

    try:
        await query.edit_message_text(channels_text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(channels_text, parse_mode="HTML", reply_markup=keyboard)
