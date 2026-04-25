"""
Admin Games Handler
Game management: list, approve/reject, enable/disable, sessions, reload plugins.
"""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import ADMIN_IDS, GAMES_DIR
from database import async_fetchone, async_fetchall, async_execute
from admin_bot.utils import admin_guard, log_admin_action


def _back_to_dashboard_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🔙 Dashboard", callback_data="admin_dashboard")


def _back_to_games_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🎮 Games", callback_data="admin_games")


async def cb_games(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all games (approved + pending)."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "games_list", details="Viewed games list")

    # Get all games grouped by approval status
    all_games = await async_fetchall(
        """
        SELECT g.*
        FROM games g
        ORDER BY
            CASE g.is_approved
                WHEN 0 THEN 1
                WHEN 1 THEN 2
            END,
            g.name ASC
        """
    )

    if not all_games:
        text = "🎮 *Games*\n\nNo games found."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Reload Games", callback_data="admin_reload_games")],
            [_back_to_dashboard_button()],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    # Group by approval status
    pending = [g for g in all_games if not g["is_approved"] and g["is_active"]]
    approved = [g for g in all_games if g["is_approved"] and g["is_active"]]
    disabled = [g for g in all_games if not g["is_active"]]

    lines = ["🎮 *Games Overview*\n"]

    if pending:
        lines.append(f"\n⏳ *Pending Approval* ({len(pending)}):")
        for g in pending:
            lines.append(f"  • #{g['id']} {g['name']}")

    if approved:
        lines.append(f"\n✅ *Approved* ({len(approved)}):")
        for g in approved:
            lines.append(f"  • #{g['id']} {g['name']}")

    if disabled:
        lines.append(f"\n🔴 *Disabled* ({len(disabled)}):")
        for g in disabled:
            lines.append(f"  • #{g['id']} {g['name']}")

    text = "\n".join(lines)

    # Buttons for each game (up to 10)
    game_buttons = []
    for g in all_games[:10]:
        if not g["is_active"]:
            status_emoji = "🔴"
        elif not g["is_approved"]:
            status_emoji = "⏳"
        else:
            status_emoji = "✅"
        game_buttons.append(
            InlineKeyboardButton(
                f"{status_emoji} {g['name'][:20]}",
                callback_data=f"admin_game_detail:{g['id']}",
            )
        )

    game_rows = [game_buttons[i:i + 2] for i in range(0, len(game_buttons), 2)]

    keyboard = InlineKeyboardMarkup(
        game_rows + [
            [InlineKeyboardButton("🎮 Active Sessions", callback_data="admin_active_sessions")],
            [InlineKeyboardButton("🔄 Reload Games", callback_data="admin_reload_games")],
            [_back_to_dashboard_button()],
        ]
    )

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_game_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show game details."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    game_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    await log_admin_action(
        admin_id, "game_detail", target_type="game", target_id=str(game_id)
    )

    game = await async_fetchone(
        "SELECT * FROM games WHERE id = ?",
        (game_id,),
    )

    if not game:
        await query.edit_message_text(
            "❌ Game not found.",
            reply_markup=InlineKeyboardMarkup([[_back_to_games_button()]]),
        )
        return

    # Get session stats
    total_sessions = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM game_sessions WHERE game_id = ?", (game_id,)
    )
    active_sessions = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM game_sessions WHERE game_id = ? AND status = 'active'", (game_id,)
    )

    if not game["is_active"]:
        status_emoji = "🔴"
        status_text = "Disabled"
    elif not game["is_approved"]:
        status_emoji = "⏳"
        status_text = "Pending"
    else:
        status_emoji = "✅"
        status_text = "Approved"

    text = (
        f"🎮 *Game Details*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"ID: `{game['id']}`\n"
        f"Name: {game['name']}\n"
        f"Slug: `{game['slug']}`\n"
        f"Status: {status_emoji} {status_text}\n"
        f"Creator: {game.get('creator', 'N/A')}\n"
        f"Min Players: {game.get('min_players', 'N/A')}\n"
        f"Max Players: {game.get('max_players', 'N/A')}\n"
        f"Entry Fee: {game.get('entry_fee_sar', 0):.2f} SAR\n"
        f"Reward: {game.get('reward_sar', 0):.2f} SAR\n"
        f"Total Sessions: {total_sessions['cnt'] if total_sessions else 0}\n"
        f"Active Sessions: {active_sessions['cnt'] if active_sessions else 0}\n"
        f"Description: {game.get('description', 'N/A')}\n"
        f"Created: {game.get('created_at', 'N/A')}\n"
    )

    # Build action buttons based on status
    action_rows = []
    if not game["is_approved"] and game["is_active"]:
        action_rows.append([
            InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_game:{game_id}"),
        ])
    elif game["is_approved"] and game["is_active"]:
        action_rows.append([
            InlineKeyboardButton("🔴 Disable", callback_data=f"admin_disable_game:{game_id}"),
        ])
    elif not game["is_active"]:
        action_rows.append([
            InlineKeyboardButton("✅ Enable", callback_data=f"admin_enable_game:{game_id}"),
        ])

    keyboard = InlineKeyboardMarkup(
        action_rows + [
            [_back_to_games_button(), _back_to_dashboard_button()],
        ]
    )

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_approve_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Approve a pending game."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    game_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    game = await async_fetchone("SELECT * FROM games WHERE id = ?", (game_id,))
    if not game:
        await query.answer("❌ Game not found.", show_alert=True)
        return

    if game["is_approved"]:
        await query.answer("Game is already approved.", show_alert=True)
        return

    await async_execute("UPDATE games SET is_approved = 1 WHERE id = ?", (game_id,))

    await log_admin_action(
        admin_id, "approve_game", target_type="game", target_id=str(game_id),
        details=f"Approved game: {game['name']}"
    )

    await query.answer()

    await query.edit_message_text(
        f"✅ Game *{game['name']}* has been approved!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 Game Detail", callback_data=f"admin_game_detail:{game_id}")],
            [_back_to_games_button(), _back_to_dashboard_button()],
        ]),
        parse_mode="Markdown",
    )


async def cb_reject_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reject a pending game."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    game_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    game = await async_fetchone("SELECT * FROM games WHERE id = ?", (game_id,))
    if not game:
        await query.answer("❌ Game not found.", show_alert=True)
        return

    await async_execute("UPDATE games SET is_approved = 0, is_active = 0 WHERE id = ?", (game_id,))

    await log_admin_action(
        admin_id, "reject_game", target_type="game", target_id=str(game_id),
        details=f"Rejected game: {game['name']}"
    )

    await query.answer()

    await query.edit_message_text(
        f"❌ Game *{game['name']}* has been rejected.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 Game Detail", callback_data=f"admin_game_detail:{game_id}")],
            [_back_to_games_button(), _back_to_dashboard_button()],
        ]),
        parse_mode="Markdown",
    )


async def cb_disable_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disable an approved game."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    game_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    game = await async_fetchone("SELECT * FROM games WHERE id = ?", (game_id,))
    if not game:
        await query.answer("❌ Game not found.", show_alert=True)
        return

    if not game["is_active"]:
        await query.answer("Game is already disabled.", show_alert=True)
        return

    await async_execute("UPDATE games SET is_active = 0 WHERE id = ?", (game_id,))

    await log_admin_action(
        admin_id, "disable_game", target_type="game", target_id=str(game_id),
        details=f"Disabled game: {game['name']}"
    )

    await query.answer()

    await query.edit_message_text(
        f"🔴 Game *{game['name']}* has been disabled.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 Game Detail", callback_data=f"admin_game_detail:{game_id}")],
            [_back_to_games_button(), _back_to_dashboard_button()],
        ]),
        parse_mode="Markdown",
    )


async def cb_enable_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enable a disabled game."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    game_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    game = await async_fetchone("SELECT * FROM games WHERE id = ?", (game_id,))
    if not game:
        await query.answer("❌ Game not found.", show_alert=True)
        return

    await async_execute("UPDATE games SET is_active = 1 WHERE id = ?", (game_id,))

    await log_admin_action(
        admin_id, "enable_game", target_type="game", target_id=str(game_id),
        details=f"Enabled game: {game['name']}"
    )

    await query.answer()

    await query.edit_message_text(
        f"✅ Game *{game['name']}* has been enabled.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 Game Detail", callback_data=f"admin_game_detail:{game_id}")],
            [_back_to_games_button(), _back_to_dashboard_button()],
        ]),
        parse_mode="Markdown",
    )


async def cb_active_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all active game sessions."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "active_sessions", details="Viewed active sessions")

    sessions = await async_fetchall(
        """
        SELECT gs.*, g.name as game_name,
               (SELECT COUNT(*) FROM game_players WHERE session_id = gs.id) as player_count
        FROM game_sessions gs
        JOIN games g ON gs.game_id = g.id
        WHERE gs.status = 'active'
        ORDER BY gs.created_at DESC
        """
    )

    if not sessions:
        text = "🎮 *Active Sessions*\n\nNo active sessions."
        keyboard = InlineKeyboardMarkup([[_back_to_games_button()]])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    lines = ["🎮 *Active Sessions*\n"]
    session_buttons = []

    for s in sessions:
        lines.append(
            f"• Session #{s['id']} — {s['game_name']} | "
            f"Players: {s['player_count']} | Started: {s.get('created_at', 'N/A')}"
        )
        session_buttons.append(
            InlineKeyboardButton(
                f"📋 #{s['id']} {s['game_name'][:15]}",
                callback_data=f"admin_session_detail:{s['id']}",
            )
        )

    text = "\n".join(lines)
    session_rows = [session_buttons[i:i + 2] for i in range(0, len(session_buttons), 2)]

    keyboard = InlineKeyboardMarkup(session_rows + [[_back_to_games_button()]])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_session_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show session details."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    session_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    await log_admin_action(
        admin_id, "session_detail", target_type="session", target_id=str(session_id)
    )

    session = await async_fetchone(
        """
        SELECT gs.*, g.name as game_name
        FROM game_sessions gs
        JOIN games g ON gs.game_id = g.id
        WHERE gs.id = ?
        """,
        (session_id,),
    )

    if not session:
        await query.edit_message_text(
            "❌ Session not found.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎮 Active Sessions", callback_data="admin_active_sessions")],
            ]),
        )
        return

    # Get players
    players = await async_fetchall(
        """
        SELECT gp.*, u.username, u.first_name, u.telegram_id
        FROM game_players gp
        JOIN users u ON gp.user_id = u.id
        WHERE gp.session_id = ?
        """,
        (session_id,),
    )

    player_lines = []
    for p in players:
        name = p.get("first_name") or p.get("username") or str(p["telegram_id"])
        player_lines.append(f"  • {name}")

    players_text = "\n".join(player_lines) if player_lines else "  No players"

    # Get recent actions
    actions = await async_fetchall(
        """
        SELECT * FROM game_actions
        WHERE session_id = ?
        ORDER BY created_at DESC LIMIT 10
        """,
        (session_id,),
    )

    action_lines = []
    for a in actions:
        action_lines.append(f"  • {a.get('action', 'N/A')}: {a.get('data_json', '')} ({a.get('created_at', '')})")

    actions_text = "\n".join(action_lines) if action_lines else "  No actions"

    text = (
        f"🎮 *Session Details*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Session ID: `{session_id}`\n"
        f"Game: {session['game_name']}\n"
        f"Status: {session['status'].title()}\n"
        f"Players: {len(players)}\n"
        f"Entry Fee: {session.get('entry_fee', 0):.2f} SAR\n"
        f"Reward Pool: {session.get('reward_pool', 0):.2f} SAR\n"
        f"Started: {session.get('created_at', 'N/A')}\n"
        f"\n👥 *Players:*\n{players_text}\n"
        f"\n📝 *Recent Actions:*\n{actions_text}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔴 Force End", callback_data=f"admin_end_session:{session_id}"),
            InlineKeyboardButton("🎮 Sessions", callback_data="admin_active_sessions"),
        ],
        [_back_to_games_button(), _back_to_dashboard_button()],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_end_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force-end a game session."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    session_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    session = await async_fetchone(
        """
        SELECT gs.*, g.name as game_name
        FROM game_sessions gs
        JOIN games g ON gs.game_id = g.id
        WHERE gs.id = ?
        """,
        (session_id,),
    )

    if not session:
        await query.answer("❌ Session not found.", show_alert=True)
        return

    if session["status"] != "active":
        await query.answer("Session is not active.", show_alert=True)
        return

    # End the session - refund all players
    players = await async_fetchall(
        "SELECT * FROM game_players WHERE session_id = ?", (session_id,)
    )

    # Refund entry fees if the game hadn't finished
    entry_fee = session.get("entry_fee", 0) or 0
    for p in players:
        if entry_fee > 0:
            await async_execute(
                "UPDATE wallets SET balance = balance + ? WHERE user_id = ?",
                (entry_fee, p["user_id"]),
            )
            await async_execute(
                """
                INSERT INTO transactions (user_id, type, amount, description)
                VALUES (?, ?, ?, ?)
                """,
                (
                    p["user_id"],
                    "credit",
                    entry_fee,
                    f"Session #{session_id} force-ended by admin - entry fee refund",
                ),
            )

    await async_execute(
        "UPDATE game_sessions SET status = 'cancelled', ended_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), session_id),
    )

    await log_admin_action(
        admin_id, "force_end_session", target_type="session", target_id=str(session_id),
        details=f"Force-ended session #{session_id} ({session['game_name']}). Refunded {len(players)} players."
    )

    await query.answer()

    await query.edit_message_text(
        f"🔴 Session #{session_id} (*{session['game_name']}*) has been force-ended.\n"
        f"Refunded {len(players)} players ({entry_fee:.2f} SAR each).",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 Active Sessions", callback_data="admin_active_sessions")],
            [_back_to_games_button(), _back_to_dashboard_button()],
        ]),
        parse_mode="Markdown",
    )


async def cb_reload_games(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger plugin reload for games directory."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(
        admin_id, "reload_games", details=f"Triggered game plugin reload from {GAMES_DIR}"
    )

    # Attempt to reload game plugins
    reloaded = 0
    errors = []

    try:
        import importlib
        import os
        import sys

        if os.path.isdir(GAMES_DIR):
            # Remove old game modules from sys.modules
            to_remove = [
                name for name in sys.modules
                if name.startswith("games.") or name == "games"
            ]
            for name in to_remove:
                try:
                    del sys.modules[name]
                except Exception:
                    pass

            # Re-import game modules
            for item in os.listdir(GAMES_DIR):
                game_path = os.path.join(GAMES_DIR, item)
                if os.path.isdir(game_path) and os.path.isfile(
                    os.path.join(game_path, "__init__.py")
                ):
                    try:
                        module = importlib.import_module(f"games.{item}")
                        if hasattr(module, "setup"):
                            module.setup()
                        reloaded += 1
                    except Exception as e:
                        errors.append(f"{item}: {str(e)}")

    except Exception as e:
        errors.append(f"System error: {str(e)}")

    text = f"🔄 *Game Plugin Reload*\n\nReloaded: {reloaded} games"
    if errors:
        text += f"\n\n❌ Errors:\n" + "\n".join(f"• {e}" for e in errors[:5])

    keyboard = InlineKeyboardMarkup([
        [_back_to_games_button(), _back_to_dashboard_button()],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
