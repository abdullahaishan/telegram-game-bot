"""
Memory Match game plugin for Telegram multiplayer game platform.

4x4 grid of cards with 8 emoji pairs. Players take turns flipping
two cards. If they match, the player keeps the pair and goes again.
If they don't match, both cards flip back and the next player goes.
The player with the most pairs when all cards are matched wins.
"""

import json
import random


# ── Helpers ──────────────────────────────────────────────────────────────────

ROWS = 4
COLS = 4
NUM_PAIRS = (ROWS * COLS) // 2  # 8 pairs

CARD_EMOJIS = ["🍎", "🍊", "🍋", "🍇", "🍓", "🍑", "🍒", "🥝"]
PLAYER_COLORS = ["🔴", "🔵", "🟢", "🟣"]
HIDDEN_DISPLAY = "❓"
EMPTY_CELL = "  "


def _shuffle_cards():
    """Create a shuffled 4x4 grid of emoji pairs. Returns a flat list of 16 items."""
    pairs = CARD_EMOJIS[:NUM_PAIRS] * 2  # 8 pairs = 16 cards
    random.shuffle(pairs)
    # Return as 2D list
    grid = []
    for r in range(ROWS):
        row = []
        for c in range(COLS):
            row.append(pairs[r * COLS + c])
        grid.append(row)
    return grid


def _player_by_index(session, idx):
    """Return the player dict for a given player_index, or None."""
    for p in session["players"]:
        if p["player_index"] == idx:
            return p
    return None


def _count_pairs_for_player(session, player_index):
    """Count how many pairs a player has found."""
    state = session["game_state"]
    count = 0
    for pair in state.get("matched_pairs", []):
        if pair["player_index"] == player_index:
            count += 1
    return count


def _all_cards_matched(state):
    """Return True if all 8 pairs have been matched."""
    return len(state.get("matched_pairs", [])) >= NUM_PAIRS


# ── Required API ─────────────────────────────────────────────────────────────

def init_game(session):
    """Initialize a new Memory Match game session."""
    card_grid = _shuffle_cards()

    state = {
        "card_grid": card_grid,         # 4x4 grid of emoji strings (the actual card values)
        "revealed": [[False] * COLS for _ in range(ROWS)],  # currently face-up (temporarily or permanently)
        "matched": [[False] * COLS for _ in range(ROWS)],   # permanently matched (removed from play)
        "matched_pairs": [],            # list of {"player_index": int, "emoji": str, "positions": [(r,c),(r,c)]}
        "flip_count": 0,                # 0, 1, or 2 flips this turn
        "first_flip": None,             # {"row": int, "col": int} or None
        "second_flip": None,            # {"row": int, "col": int} or None
        "moves": [],                    # activity log entries
        "winner": None,                 # player_index (int), "draw", or None
    }
    session["game_state"] = state
    session["current_turn_index"] = 0
    session["current_phase"] = "playing"
    return session


def render(session):
    """Build the render context for the UI engine."""
    state = session["game_state"]
    players = session["players"]
    status = session.get("status", "active")
    phase = session.get("current_phase", "playing")
    winner = state.get("winner")

    # ── Build player HUD list ────────────────────────────────────────────
    player_hud = []
    for p in players:
        idx = p["player_index"]
        is_turn = (idx == session.get("current_turn_index", 0)) and phase == "playing"
        pairs_found = _count_pairs_for_player(session, idx)
        color = PLAYER_COLORS[idx % len(PLAYER_COLORS)]
        player_hud.append({
            "name": p["name"],
            "badge": color,
            "balance": 0.0,
            "wins": p.get("score", 0),
            "role": f"P{idx + 1}",
            "is_turn": is_turn,
            "is_alive": True,
            "score": pairs_found,
            "color": color,
        })

    # ── Build board ──────────────────────────────────────────────────────
    cells = []
    cell_actions = []
    hidden = []

    for r in range(ROWS):
        row_cells = []
        row_actions = []
        row_hidden = []
        for c in range(COLS):
            # If the card is permanently matched, show empty
            if state["matched"][r][c]:
                row_cells.append(EMPTY_CELL)
                row_actions.append(None)
                row_hidden.append(False)
            # If the card is currently revealed (temporarily or was just flipped)
            elif state["revealed"][r][c]:
                row_cells.append(state["card_grid"][r][c])
                row_actions.append(None)  # can't flip an already revealed card
                row_hidden.append(False)
            # If the card is face-down
            else:
                row_cells.append(HIDDEN_DISPLAY)
                # Allow flipping if the game is still playing
                if phase == "playing" and winner is None:
                    row_actions.append(f"flip:{r},{c}")
                else:
                    row_actions.append(None)
                row_hidden.append(True)
        cells.append(row_cells)
        cell_actions.append(row_actions)
        hidden.append(row_hidden)

    # ── Build activity log ───────────────────────────────────────────────
    activity_log = list(state.get("moves", []))

    # Determine turn_owner
    turn_idx = session.get("current_turn_index", 0)
    turn_player = _player_by_index(session, turn_idx)
    turn_owner_name = turn_player["name"] if turn_player else f"Player {turn_idx + 1}"

    # Rules reminder
    rules_reminder = "Flip two cards per turn. Match a pair to go again!"
    if state["flip_count"] == 1:
        rules_reminder = "Flip one more card to complete your turn."
    if winner is not None:
        if winner == "draw":
            rules_reminder = "It's a draw! All pairs matched."
        else:
            wp = _player_by_index(session, winner)
            wname = wp["name"] if wp else f"Player {winner + 1}"
            rules_reminder = f"🎉 {wname} wins with the most pairs!"
    elif phase == "finished":
        rules_reminder = "Game over!"

    win_condition = "Most pairs when all cards are matched"

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
            "game_name": "Memory Match",
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
        # Find the player index for the forfeiting user
        for p in session["players"]:
            if p["user_id"] == user_id:
                # Mark remaining unmatched cards as matched by no one
                # Then check if game is done
                break
        # Simple forfeit: award win to the other player with most pairs
        # For multi-player, just end the game
        scores = {}
        for p in session["players"]:
            if p["user_id"] != user_id:
                scores[p["player_index"]] = _count_pairs_for_player(session, p["player_index"])
        if scores:
            max_score = max(scores.values())
            winners = [idx for idx, s in scores.items() if s == max_score]
            if len(winners) == 1:
                state["winner"] = winners[0]
            else:
                state["winner"] = "draw"
        session["current_phase"] = "finished"
        return session

    # ── Rules action (no state change) ───────────────────────────────────
    if action == "rules":
        return session

    # ── Flip action ──────────────────────────────────────────────────────
    if not action.startswith("flip:"):
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
    if not (0 <= row < ROWS and 0 <= col < COLS):
        return session

    # Validate it's this player's turn
    current_turn = session["current_turn_index"]
    current_player = None
    for p in session["players"]:
        if p["player_index"] == current_turn:
            current_player = p
            break
    if current_player is None or current_player["user_id"] != user_id:
        return session

    # Validate cell is flippable (not already revealed, not already matched)
    if state["revealed"][row][col] or state["matched"][row][col]:
        return session

    # Validate we haven't already flipped 2 cards this turn
    if state["flip_count"] >= 2:
        return session

    # ── First flip ───────────────────────────────────────────────────────
    if state["flip_count"] == 0:
        state["revealed"][row][col] = True
        state["first_flip"] = {"row": row, "col": col}
        state["flip_count"] = 1
        state["moves"].append(
            f"{current_player['name']} flipped ({row},{col}): {state['card_grid'][row][col]}"
        )
        return session

    # ── Second flip ──────────────────────────────────────────────────────
    if state["flip_count"] == 1:
        # Prevent flipping the same card as the first flip
        first = state["first_flip"]
        if row == first["row"] and col == first["col"]:
            return session

        state["revealed"][row][col] = True
        state["second_flip"] = {"row": row, "col": col}
        state["flip_count"] = 2
        state["moves"].append(
            f"{current_player['name']} flipped ({row},{col}): {state['card_grid'][row][col]}"
        )

        # Check for match
        first_emoji = state["card_grid"][first["row"]][first["col"]]
        second_emoji = state["card_grid"][row][col]

        if first_emoji == second_emoji:
            # ── Match found! ─────────────────────────────────────────────
            state["matched"][first["row"]][first["col"]] = True
            state["matched"][row][col] = True
            state["matched_pairs"].append({
                "player_index": current_turn,
                "emoji": first_emoji,
                "positions": [
                    (first["row"], first["col"]),
                    (row, col),
                ],
            })
            state["moves"].append(
                f"✅ {current_player['name']} matched {first_emoji}!"
            )
            # Update player score
            for p in session["players"]:
                if p["player_index"] == current_turn:
                    p["score"] = p.get("score", 0) + 1
                    break

            # Reset flip state (player goes again)
            state["flip_count"] = 0
            state["first_flip"] = None
            state["second_flip"] = None
            # Reveal stays True but matched is True, so they'll render as empty

            # Check if all pairs matched
            if _all_cards_matched(state):
                result = check_win(session)
                if isinstance(result, dict):
                    state["winner"] = result.get("winner", "draw")
                session["current_phase"] = "finished"
            # Player goes again – no turn change
            return session
        else:
            # ── No match ─────────────────────────────────────────────────
            state["moves"].append(
                f"❌ No match. Cards flip back."
            )
            # Hide both cards (they'll be hidden on next render after a brief display)
            state["revealed"][first["row"]][first["col"]] = False
            state["revealed"][row][col] = False
            state["flip_count"] = 0
            state["first_flip"] = None
            state["second_flip"] = None

            # Move to next player
            num_players = len(session["players"])
            session["current_turn_index"] = (current_turn + 1) % num_players
            return session

    return session


def check_win(session):
    """Check if all pairs are matched and determine the winner.

    Returns:
        dict with {"type": "win", "winner": int} where winner is player_index
        dict with {"type": "draw"} if tied
        None if the game is still in progress
    """
    state = session["game_state"]

    if not _all_cards_matched(state):
        return None

    # Count pairs per player
    scores = {}
    for p in session["players"]:
        idx = p["player_index"]
        scores[idx] = _count_pairs_for_player(session, idx)

    # Find the highest score
    max_score = max(scores.values())
    winners = [idx for idx, s in scores.items() if s == max_score]

    if len(winners) == 1:
        return {
            "type": "win",
            "winner": winners[0],
        }
    else:
        return {"type": "draw"}


def serialize_state(session):
    """Serialize the game state for persistent storage."""
    state = session.get("state", {})

    # Convert matched_pairs positions from tuples to lists for JSON
    matched_pairs = []
    for pair in state.get("matched_pairs", []):
        mp = {
            "player_index": pair["player_index"],
            "emoji": pair["emoji"],
            "positions": [list(pos) for pos in pair["positions"]],
        }
        matched_pairs.append(mp)

    serializable = {
        "card_grid": state.get("card_grid", []),
        "revealed": state.get("revealed", [[False] * COLS for _ in range(ROWS)]),
        "matched": state.get("matched", [[False] * COLS for _ in range(ROWS)]),
        "matched_pairs": matched_pairs,
        "flip_count": state.get("flip_count", 0),
        "first_flip": state.get("first_flip"),
        "second_flip": state.get("second_flip"),
        "moves": state.get("moves", []),
        "winner": state.get("winner"),
    }
    return serializable


def deserialize_state(data):
    """Deserialize game state from persistent storage.

    Returns a dict to be placed at session["game_state"].
    """
    if isinstance(data, str):
        data = json.loads(data)

    # Convert matched_pairs positions from lists back to tuples
    matched_pairs = []
    for pair in data.get("matched_pairs", []):
        mp = {
            "player_index": pair["player_index"],
            "emoji": pair["emoji"],
            "positions": [tuple(pos) for pos in pair["positions"]],
        }
        matched_pairs.append(mp)

    return {
        "card_grid": data.get("card_grid", []),
        "revealed": data.get("revealed", [[False] * COLS for _ in range(ROWS)]),
        "matched": data.get("matched", [[False] * COLS for _ in range(ROWS)]),
        "matched_pairs": matched_pairs,
        "flip_count": data.get("flip_count", 0),
        "first_flip": data.get("first_flip"),
        "second_flip": data.get("second_flip"),
        "moves": data.get("moves", []),
        "winner": data.get("winner"),
    }
