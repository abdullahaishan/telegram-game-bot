"""Auto-generated game plugin for Battle Grid."""
import json


# ── Configuration ────────────────────────────────────────────────────────────

EMPTY_CELL = "  "
GAME_NAME = 'Battle Grid'
GAME_SLUG = 'battle_grid'
BOARD_ROWS = 3
BOARD_COLS = 3
LINE_LENGTH = 3
PLAYER_SYMBOLS = ['🔴', '🔵', '🟢', '🟣', '🟡', '🟠', '⚪', '⚫', '❤️', '💎', '🍀', '♠️', '🍎', '🍊', '🍋', '🍇', '🍓', '🍑', '🍒', '🥝']

# Button definitions
BUTTONS = [
    {'id': 'btn_1', 'label': 'Attack', 'emoji': '', 'action_id': 'action_1', 'effect_type': 'ATTACK', 'visibility_rule': 'always', 'condition': None, 'target': None, 'cooldown': 0},
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _player_role(session, user_id):
    """Return the player symbol index for a given user_id, or None."""
    for p in session["players"]:
        if p["user_id"] == user_id:
            return p["player_index"]
    return None


def _player_symbol(player_index):
    """Return the display symbol for a player index."""
    return PLAYER_SYMBOLS[player_index % len(PLAYER_SYMBOLS)]


def _check_line(board, row, col, dr, dc, role_val, length):
    """Count consecutive role_val starting from (row, col) in direction (dr, dc)."""
    count = 0
    r, c = row, col
    while 0 <= r < BOARD_ROWS and 0 <= c < BOARD_COLS and board[r][c] == role_val:
        count += 1
        r += dr
        c += dc
    return count


def _check_winner(board):
    """Check the board for a winner. Returns dict with type/win/draw or None."""
    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
    for r in range(BOARD_ROWS):
        for c in range(BOARD_COLS):
            val = board[r][c]
            if val == EMPTY_CELL:
                continue
            for dr, dc in directions:
                forward = _check_line(board, r, c, dr, dc, val, LINE_LENGTH)
                if forward >= LINE_LENGTH:
                    line = []
                    for i in range(forward):
                        lr, lc = r + dr * i, c + dc * i
                        if 0 <= lr < BOARD_ROWS and 0 <= lc < BOARD_COLS:
                            line.append((lr, lc))
                    return {"type": "win", "winner": val, "line": line[:LINE_LENGTH]}
    # Check draw
    if all(board[r][c] != EMPTY_CELL for r in range(BOARD_ROWS) for c in range(BOARD_COLS)):
        return {"type": "draw"}
    return None


# ── Required API ─────────────────────────────────────────────────────────────

def init_game(session):
    """Initialize a new game session."""
    state = {
        "board": [[EMPTY_CELL for _ in range(BOARD_COLS)] for _ in range(BOARD_ROWS)],
        "moves": [],
        "winner": None,
        "scores": {},
    }
    for p in session["players"]:
        state["scores"][str(p["user_id"])] = 0
    state["health"] = {str(p["user_id"]): 3 for p in session["players"]}
    session["game_state"] = state
    session["current_turn_index"] = 0
    session["current_phase"] = "playing"
    return session


def render(session):
    """Build the render context for the UI engine."""
    state = session["game_state"]
    board = state["board"]
    players = session["players"]
    phase = session.get("current_phase", "playing")
    winner = state.get("winner")

    # Player HUD
    player_hud = []
    for p in players:
        idx = p["player_index"]
        symbol = _player_symbol(idx)
        is_turn = (idx == session.get("current_turn_index", 0)) and phase == "playing"
        player_hud.append({
            "name": p["name"],
            "badge": symbol,
            "balance": 0.0,
            "wins": p.get("score", 0),
            "role": f"P{idx + 1}",
            "is_turn": is_turn,
            "is_alive": True,
            "score": state["scores"].get(str(p["user_id"]), 0),
            "color": symbol,
        })

    # Board
    cells = []
    cell_actions = []
    hidden = []
    for r in range(BOARD_ROWS):
        row_cells = []
        row_actions = []
        row_hidden = []
        for c in range(BOARD_COLS):
            val = board[r][c]
            row_cells.append(val if val != EMPTY_CELL else "·")
            if val == EMPTY_CELL and phase == "playing" and winner is None:
                row_actions.append(f"place:{r},{c}")
            else:
                row_actions.append(None)
            row_hidden.append(False)
        cells.append(row_cells)
        cell_actions.append(row_actions)
        hidden.append(row_hidden)

    # Activity log
    activity_log = []
    for move in state.get("moves", [])[-10:]:
        activity_log.append(move)

    # Turn owner
    turn_idx = session.get("current_turn_index", 0)
    turn_owner_name = f"P{turn_idx + 1}"
    for p in players:
        if p["player_index"] == turn_idx:
            turn_owner_name = p["name"]
            break

    rules_reminder = "Tap an empty cell to place your mark."
    if winner is not None:
        if isinstance(winner, str) and winner != "draw":
            rules_reminder = f"🎉 {winner} wins with {LINE_LENGTH} in a row!"
        elif winner == "draw":
            rules_reminder = "It's a draw! The board is full."

    # Footer
    footer_actions = []
    if winner is not None or phase == "finished":
        footer_actions.append({"label": "🔄 Play Again", "callback": "restart", "visible": True})
    else:
        footer_actions.append({"label": "🏳️ Forfeit", "callback": "forfeit", "visible": True})

    # Add custom buttons
    # Custom buttons
    footer_actions.append({"label": 'Attack', "callback": 'action_1', "visible": True})

    navigation = [
        {"label": "🏠 Lobby", "callback": "lobby"},
        {"label": "📋 Rules", "callback": "rules"},
    ]

    return {
        "header": {
            "game_name": GAME_NAME,
            "room_id": session.get("room_id", ""),
            "mode": session.get("mode", "multiplayer"),
            "visibility": session.get("visibility", "public"),
            "status": session.get("status", "active"),
        },
        "players": player_hud,
        "board": {
            "rows": BOARD_ROWS,
            "cols": BOARD_COLS,
            "cells": cells,
            "cell_actions": cell_actions,
            "hidden": hidden,
        },
        "state": {
            "phase": phase,
            "turn_owner": turn_owner_name,
            "countdown": None,
            "rules_reminder": rules_reminder,
            "win_condition": f"{LINE_LENGTH} in a row",
            "activity_log": activity_log,
        },
        "footer": {
            "actions": footer_actions,
            "navigation": navigation,
        },
    }


def handle_callback(session, user_id, action):
    """Process a player action and return updated session."""
    state = session["game_state"]

    # Restart
    if action == "restart":
        return init_game(session)

    # Forfeit
    if action == "forfeit":
        # Find the other player(s) and declare them winner
        other_players = [p for p in session["players"] if p["user_id"] != user_id]
        if other_players:
            state["winner"] = other_players[0]["user_id"]
        else:
            state["winner"] = "draw"
        session["current_phase"] = "finished"
        return session

    # Rules
    if action == "rules":
        return session

    # Lobby
    if action == "lobby":
        return session

    # Board placement
    if action.startswith("place:"):
        if session.get("current_phase") != "playing" or state["winner"] is not None:
            return session
        try:
            _, coords = action.split(":", 1)
            row, col = map(int, coords.split(","))
        except (ValueError, IndexError):
            return session

        if not (0 <= row < BOARD_ROWS and 0 <= col < BOARD_COLS):
            return session

        role_idx = _player_role(session, user_id)
        if role_idx is None:
            return session
        if role_idx != session["current_turn_index"]:
            return session

        if state["board"][row][col] != EMPTY_CELL:
            return session

        symbol = _player_symbol(role_idx)
        state["board"][row][col] = symbol
        player_name = str(user_id)
        for p in session["players"]:
            if p["user_id"] == user_id:
                player_name = p["name"]
                break
        state["moves"].append(f"{symbol} {player_name} placed at ({row},{col})")

        # Check win
        result = check_win(session)
        if result is not None:
            if isinstance(result, dict) and result.get("type") == "win":
                state["winner"] = result["winner"]
                session["current_phase"] = "finished"
            elif isinstance(result, dict) and result.get("type") == "draw":
                state["winner"] = "draw"
                session["current_phase"] = "finished"
        else:
            num_players = len(session["players"])
            session["current_turn_index"] = (session["current_turn_index"] + 1) % num_players
        return session

    # Custom button handlers
    if action == 'action_1':
        # Attack: find next alive opponent
        for p in session['players']:
            if p['user_id'] != user_id:
                hp_key = str(p['user_id'])
                if state.get('health', {}).get(hp_key, 0) > 0:
                    state['health'][hp_key] = max(0, state['health'].get(hp_key, 0) - 1)
                    state['moves'].append(f'⚔️ Player {user_id} attacked {p["name"]}')
                    # Check if opponent eliminated
                    if state['health'][hp_key] <= 0:
                        state['winner'] = user_id
                        session['current_phase'] = 'finished'
                    else:
                        # Advance turn
                        num_players = len(session['players'])
                        session['current_turn_index'] = (session['current_turn_index'] + 1) % num_players
                    break
        return session

    return session


def check_win(session):
    """Check the board for a winner or draw."""
    board = session["game_state"]["board"]
    return _check_winner(board)


def serialize_state(session):
    """Serialize the game state for persistent storage."""
    return session.get("state", {})


def deserialize_state(data):
    """Deserialize game state from persistent storage."""
    if isinstance(data, str):
        data = json.loads(data)
    return data
