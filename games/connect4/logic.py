"""
Connect Four game plugin for Telegram multiplayer game platform.

6 rows x 7 columns grid. Two players (Red 🔴 vs Yellow 🟡) take turns
dropping discs into columns. Discs fall to the lowest available row.
First to connect four in a row wins.
"""

import json


# ── Helpers ──────────────────────────────────────────────────────────────────

ROWS = 6
COLS = 7
ROLES = ["Red", "Yellow"]
COLORS = {"Red": "🔴", "Yellow": "🟡"}
EMPTY_CELL = "  "

# Directions for checking 4-in-a-row: (row_delta, col_delta)
DIRECTIONS = [
    (0, 1),   # horizontal
    (1, 0),   # vertical
    (1, 1),   # diagonal down-right
    (1, -1),  # diagonal down-left
]


def _new_board():
    """Return a fresh 6x7 board filled with empty markers."""
    return [[EMPTY_CELL for _ in range(COLS)] for _ in range(ROWS)]


def _player_role(session, user_id):
    """Return the role (Red/Yellow) for a given user_id, or None."""
    for p in session["players"]:
        if p["user_id"] == user_id:
            return ROLES[p["player_index"]]
    return None


def _lowest_empty_row(board, col):
    """Return the lowest empty row index in the given column, or -1 if full."""
    for r in range(ROWS - 1, -1, -1):
        if board[r][col] == EMPTY_CELL:
            return r
    return -1


def _count_direction(board, row, col, dr, dc, role):
    """Count consecutive discs of `role` starting from (row, col) in direction (dr, dc)."""
    count = 0
    r, c = row + dr, col + dc
    while 0 <= r < ROWS and 0 <= c < COLS and board[r][c] == role:
        count += 1
        r += dr
        c += dc
    return count


def _check_four(board, row, col, role):
    """Check if placing `role` at (row, col) creates a 4-in-a-row.

    Returns the list of winning positions if found, or None.
    """
    for dr, dc in DIRECTIONS:
        forward = _count_direction(board, row, col, dr, dc, role)
        backward = _count_direction(board, row, col, -dr, -dc, role)
        total = 1 + forward + backward  # 1 for the placed disc itself
        if total >= 4:
            # Build the winning line positions
            line = [(row, col)]
            # Forward direction
            r, c = row + dr, col + dc
            while 0 <= r < ROWS and 0 <= c < COLS and board[r][c] == role:
                line.append((r, c))
                r += dr
                c += dc
            # Backward direction
            r, c = row - dr, col - dc
            while 0 <= r < ROWS and 0 <= c < COLS and board[r][c] == role:
                line.append((r, c))
                r -= dr
                c -= dc
            return line
    return None


def _is_board_full(board):
    """Return True if every cell in the top row is occupied."""
    return all(board[0][c] != EMPTY_CELL for c in range(COLS))


# ── Required API ─────────────────────────────────────────────────────────────

def init_game(session):
    """Initialize a new Connect Four game session."""
    state = {
        "board": _new_board(),
        "moves": [],           # list of {"user_id": int, "role": str, "col": int, "row": int}
        "winner": None,        # "Red", "Yellow", "draw", or None
        "winning_line": None,  # list of (r,c) tuples for the winning line, or None
    }
    session["game_state"] = state
    session["current_turn_index"] = 0  # Red always goes first
    session["current_phase"] = "playing"
    return session


def render(session):
    """Build the render context for the UI engine."""
    state = session["game_state"]
    board = state["board"]
    players = session["players"]
    status = session.get("status", "active")
    phase = session.get("current_phase", "playing")
    winner = state.get("winner")

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
    # For Connect Four, we use cell_actions on the top row only to represent
    # column selection. All other cells have None for cell_actions.
    # The UI should render column-header buttons using the top row's actions.
    cells = []
    cell_actions = []
    hidden = []
    for r in range(ROWS):
        row_cells = []
        row_actions = []
        row_hidden = []
        for c in range(COLS):
            val = board[r][c]
            # Convert role names to display emojis on the board
            if val == "Red":
                row_cells.append("🔴")
            elif val == "Yellow":
                row_cells.append("🟡")
            else:
                row_cells.append("  ")

            # Only the top row gets drop actions; all others None
            if r == 0 and phase == "playing" and winner is None:
                row_actions.append(f"drop:{c}")
            else:
                row_actions.append(None)

            row_hidden.append(False)
        cells.append(row_cells)
        cell_actions.append(row_actions)
        hidden.append(row_hidden)

    # ── Build activity log from moves ────────────────────────────────────
    activity_log = []
    for move in state.get("moves", []):
        activity_log.append(
            f"{COLORS[move['role']]} {move['role']} dropped in column {move['col'] + 1}"
        )

    # Determine turn_owner
    turn_idx = session.get("current_turn_index", 0)
    turn_role = ROLES[turn_idx]
    turn_owner_name = turn_role
    for p in players:
        if p["player_index"] == turn_idx:
            turn_owner_name = p["name"]
            break

    # Rules reminder
    rules_reminder = "Tap a column to drop your disc."
    if winner == "draw":
        rules_reminder = "It's a draw! The board is full."
    elif winner is not None:
        rules_reminder = f"🎉 {winner} wins with four in a row!"

    win_condition = "4 in a row (horizontal, vertical, diagonal)"

    # ── Footer actions ───────────────────────────────────────────────────
    footer_actions = []
    if winner is not None or phase == "finished":
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
            "game_name": "Connect Four",
            "room_id": session.get("room_id", ""),
            "mode": session.get("mode", "multiplayer"),
            "visibility": session.get("visibility", "public"),
            "status": status,
        },
        "players": player_hud,
        "board": {
            "rows": ROWS,
            "cols": COLS,
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
        other_role = "Yellow" if role == "Red" else "Red"
        state["winner"] = other_role
        session["current_phase"] = "finished"
        return session

    # ── Rules action (no state change) ───────────────────────────────────
    if action == "rules":
        return session

    # ── Drop action ──────────────────────────────────────────────────────
    if not action.startswith("drop:"):
        return session

    # Only allow actions while game is playing
    if session.get("current_phase") != "playing" or state["winner"] is not None:
        return session

    # Parse column
    try:
        _, col_str = action.split(":", 1)
        col = int(col_str)
    except (ValueError, IndexError):
        return session

    # Validate column
    if not (0 <= col < COLS):
        return session

    # Validate it's this player's turn
    role = _player_role(session, user_id)
    if role is None:
        return session
    expected_role = ROLES[session["current_turn_index"]]
    if role != expected_role:
        return session

    # Find the lowest empty row in this column
    row = _lowest_empty_row(state["board"], col)
    if row == -1:
        # Column is full
        return session

    # Place the disc
    state["board"][row][col] = role
    state["moves"].append({
        "user_id": user_id,
        "role": role,
        "col": col,
        "row": row,
    })

    # Check for winner or draw
    result = check_win(session)
    if result is not None:
        if isinstance(result, dict) and result.get("type") == "win":
            state["winner"] = result["winner"]
            state["winning_line"] = result.get("line", None)
            session["current_phase"] = "finished"
        elif isinstance(result, dict) and result.get("type") == "draw":
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

    # Check from every cell for 4-in-a-row
    for r in range(ROWS):
        for c in range(COLS):
            role = board[r][c]
            if role == EMPTY_CELL:
                continue
            line = _check_four(board, r, c, role)
            if line is not None:
                return {
                    "type": "win",
                    "winner": role,
                    "line": line,
                }

    # Check for draw (board full, no winner)
    if _is_board_full(board):
        return {"type": "draw"}

    return None


def serialize_state(session):
    """Serialize the game state for persistent storage."""
    state = session.get("state", {})
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
