"""
Tic-Tac-Toe game plugin for Telegram multiplayer game platform.

Classic 3x3 grid game. Two players (X and O) take turns placing
their marks. First to get three in a row wins.
"""

import json


# ── Helpers ──────────────────────────────────────────────────────────────────

ROLES = ["X", "O"]
COLORS = {"X": "🔴", "O": "🔵"}
EMPTY_CELL = "  "

WINNING_LINES = [
    # Rows
    [(0, 0), (0, 1), (0, 2)],
    [(1, 0), (1, 1), (1, 2)],
    [(2, 0), (2, 1), (2, 2)],
    # Columns
    [(0, 0), (1, 0), (2, 0)],
    [(0, 1), (1, 1), (2, 1)],
    [(0, 2), (1, 2), (2, 2)],
    # Diagonals
    [(0, 0), (1, 1), (2, 2)],
    [(0, 2), (1, 1), (2, 0)],
]


def _new_board():
    """Return a fresh 3x3 board filled with empty markers."""
    return [[EMPTY_CELL for _ in range(3)] for _ in range(3)]


def _player_role(session, user_id):
    """Return the role (X/O) for a given user_id, or None."""
    for p in session["players"]:
        if p["user_id"] == user_id:
            return ROLES[p["player_index"]]
    return None


def _player_index_by_role(role):
    """Map role string to player index. X->0, O->1."""
    return ROLES.index(role)


# ── Required API ─────────────────────────────────────────────────────────────

def init_game(session):
    """Initialize a new Tic-Tac-Toe game session."""
    state = {
        "board": _new_board(),
        "moves": [],           # list of {"user_id": int, "role": str, "row": int, "col": int}
        "winner": None,        # "X", "O", "draw", or None
        "winning_line": None,  # list of (r,c) tuples for the winning line, or None
    }
    session["game_state"] = state
    session["current_turn_index"] = 0  # X always goes first
    session["current_phase"] = "playing"
    return session


def render(session):
    """Build the render context for the UI engine."""
    state = session["game_state"]
    board = state["board"]
    players = session["players"]
    status = session.get("status", "active")
    phase = session.get("current_phase", "playing")

    # ── Build player HUD list ────────────────────────────────────────────
    player_hud = []
    for p in players:
        idx = p["player_index"]
        role = ROLES[idx]
        is_turn = (idx == session.get("current_turn_index", 0)) and phase == "playing"
        player_hud.append({
            "name": p["name"],
            "badge": COLORS[role],
            "balance": 0.0,
            "wins": p.get("score", 0),
            "role": role,
            "is_turn": is_turn,
            "is_alive": True,
            "score": p.get("score", 0),
            "color": COLORS[role],
        })

    # ── Build board ──────────────────────────────────────────────────────
    cells = []
    cell_actions = []
    hidden = []
    for r in range(3):
        row_cells = []
        row_actions = []
        row_hidden = []
        for c in range(3):
            val = board[r][c]
            row_cells.append(val)
            # Only allow placement on empty cells during playing phase
            if val == EMPTY_CELL and phase == "playing" and state["winner"] is None:
                row_actions.append(f"place:{r},{c}")
            else:
                row_actions.append(None)
            row_hidden.append(False)
        cells.append(row_cells)
        cell_actions.append(row_actions)
        hidden.append(row_hidden)

    # ── Build activity log from moves ────────────────────────────────────
    activity_log = []
    for move in state.get("moves", []):
        activity_log.append(f"{move['role']} placed at ({move['row']},{move['col']})")

    # Determine turn_owner name
    turn_idx = session.get("current_turn_index", 0)
    turn_role = ROLES[turn_idx]
    turn_owner_name = turn_role
    for p in players:
        if p["player_index"] == turn_idx:
            turn_owner_name = p["name"]
            break

    # Rules reminder and win condition
    rules_reminder = "Tap an empty cell to place your mark."
    if state["winner"] == "draw":
        rules_reminder = "It's a draw! The board is full."
    elif state["winner"] is not None:
        rules_reminder = f"🎉 {state['winner']} wins!"

    win_condition = "3 in a row (horizontal, vertical, diagonal)"

    # ── Footer actions ───────────────────────────────────────────────────
    footer_actions = []
    if state["winner"] is not None or phase == "finished":
        footer_actions.append({
            "label": "🔄 Play Again",
            "callback": "restart",
            "visible": True,
        })
    else:
        footer_actions.append({
            "label": "🏳️ Forfeit",
            "callback": "forfeit",
            "visible": True,
        })

    navigation = [
        {"label": "🏠 Lobby", "callback": "lobby"},
        {"label": "📋 Rules", "callback": "rules"},
    ]

    return {
        "header": {
            "game_name": "Tic-Tac-Toe",
            "room_id": session.get("room_id", ""),
            "mode": session.get("mode", "multiplayer"),
            "visibility": session.get("visibility", "public"),
            "status": status,
        },
        "players": player_hud,
        "board": {
            "rows": 3,
            "cols": 3,
            "cells": cells,
            "cell_actions": cell_actions,
            "hidden": hidden,
        },
        "state": {
            "phase": phase,
            "turn_owner": turn_owner_name,
            "countdown": None,
            "rules_reminder": rules_reminder,
            "win_condition": win_condition,
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

    # ── Restart action ───────────────────────────────────────────────────
    if action == "restart":
        return init_game(session)

    # ── Forfeit action ───────────────────────────────────────────────────
    if action == "forfeit":
        role = _player_role(session, user_id)
        if role is None:
            return session
        other_role = "O" if role == "X" else "X"
        state["winner"] = other_role
        session["current_phase"] = "finished"
        return session

    # ── Rules action (no state change) ───────────────────────────────────
    if action == "rules":
        return session

    # ── Place action ─────────────────────────────────────────────────────
    if not action.startswith("place:"):
        return session

    # Only allow actions while game is playing
    if session.get("current_phase") != "playing" or state["winner"] is not None:
        return session

    # Parse coordinates
    try:
        _, coords = action.split(":", 1)
        row, col = map(int, coords.split(","))
    except (ValueError, IndexError):
        return session

    # Validate coordinates
    if not (0 <= row < 3 and 0 <= col < 3):
        return session

    # Validate it's this player's turn
    role = _player_role(session, user_id)
    if role is None:
        return session
    expected_role = ROLES[session["current_turn_index"]]
    if role != expected_role:
        return session

    # Validate cell is empty
    if state["board"][row][col] != EMPTY_CELL:
        return session

    # Place the mark
    state["board"][row][col] = role
    state["moves"].append({
        "user_id": user_id,
        "role": role,
        "row": row,
        "col": col,
    })

    # Check for winner or draw
    result = check_win(session)
    if result is not None:
        if isinstance(result, dict) and result.get("type") == "win":
            state["winner"] = result["winner"]
            state["winning_line"] = result.get("line", None)
            session["current_phase"] = "finished"
        elif result is True or (isinstance(result, dict) and result.get("type") == "draw"):
            state["winner"] = "draw"
            session["current_phase"] = "finished"
    else:
        # Switch turn
        session["current_turn_index"] = 1 - session["current_turn_index"]

    return session


def check_win(session):
    """Check the board for a winner or draw.

    Returns:
        dict with {"type": "win", "winner": str, "line": list} if a player won
        dict with {"type": "draw"} if the board is full with no winner
        None if the game is still in progress
    """
    board = session["game_state"]["board"]

    # Check all winning lines
    for line in WINNING_LINES:
        values = [board[r][c] for r, c in line]
        if values[0] != EMPTY_CELL and values[0] == values[1] == values[2]:
            return {
                "type": "win",
                "winner": values[0],
                "line": line,
            }

    # Check for draw (board full, no winner)
    all_filled = all(
        board[r][c] != EMPTY_CELL
        for r in range(3)
        for c in range(3)
    )
    if all_filled:
        return {"type": "draw"}

    return None


def serialize_state(session):
    """Serialize the game state for persistent storage."""
    state = session.get("state", {})
    # Convert winning_line tuples to lists for JSON serialization
    serializable = {
        "board": state.get("board", _new_board()),
        "moves": state.get("moves", []),
        "winner": state.get("winner"),
        "winning_line": [list(pos) for pos in state["winning_line"]] if state.get("winning_line") else None,
    }
    return serializable


def deserialize_state(data):
    """Deserialize game state from persistent storage.

    Returns a dict to be placed at session["game_state"].
    """
    if isinstance(data, str):
        data = json.loads(data)

    board = data.get("board", _new_board())
    moves = data.get("moves", [])
    winner = data.get("winner")
    winning_line_raw = data.get("winning_line")
    # Convert lists back to tuples for consistency
    winning_line = [tuple(pos) for pos in winning_line_raw] if winning_line_raw else None

    return {
        "board": board,
        "moves": moves,
        "winner": winner,
        "winning_line": winning_line,
    }
