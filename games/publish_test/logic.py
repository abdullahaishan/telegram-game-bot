"""Auto-generated game plugin for Publish Test."""
import json


# ── Configuration ────────────────────────────────────────────────────────────

EMPTY_CELL = "  "
GAME_NAME = 'Publish Test'
GAME_SLUG = 'publish_test'
MIN_PLAYERS = 2
MAX_PLAYERS = 4
WIN_TYPE = 'score_threshold'
TARGET_SCORE = 10
MAX_TURNS = 20
PLAYER_COLORS = ["🔴", "🔵", "🟢", "🟣", "🟡", "🟠", "⚪", "⚫",
                 "❤️", "💎", "🍀", "♠️", "🍎", "🍊", "🍋", "🍇",
                 "🍓", "🍑", "🍒", "🥝"]

# Button definitions
BUTTONS = [
    {'id': 'btn_1', 'label': 'Score', 'emoji': '', 'action_id': 'action_1', 'effect_type': 'SCORE', 'visibility_rule': 'always', 'condition': None, 'target': None, 'cooldown': 0},
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _player_by_user_id(session, user_id):
    """Return the player dict for a given user_id, or None."""
    for p in session["players"]:
        if p["user_id"] == user_id:
            return p
    return None


# ── Required API ─────────────────────────────────────────────────────────────

def init_game(session):
    """Initialize a new game session."""
    state = {
        "scores": {},
        "moves": [],
        "winner": None,
        "turn_count": 0,
    }
    for p in session["players"]:
        state["scores"][str(p["user_id"])] = 0
    session["game_state"] = state
    session["current_turn_index"] = 0
    session["current_phase"] = "playing"
    return session


def render(session):
    """Build the render context for the UI engine."""
    state = session["game_state"]
    players = session["players"]
    phase = session.get("current_phase", "playing")
    winner = state.get("winner")

    # Player HUD
    player_hud = []
    for p in players:
        idx = p["player_index"]
        color = PLAYER_COLORS[idx % len(PLAYER_COLORS)]
        is_turn = (idx == session.get("current_turn_index", 0)) and phase == "playing"
        player_hud.append({
            "name": p["name"],
            "badge": color,
            "balance": 0.0,
            "wins": p.get("score", 0),
            "role": f"P{idx + 1}",
            "is_turn": is_turn,
            "is_alive": True,
            "score": state["scores"].get(str(p["user_id"]), 0),
            "color": color,
        })

    # Activity log
    activity_log = list(state.get("moves", [])[-10:])

    # Turn owner
    turn_idx = session.get("current_turn_index", 0)
    turn_owner_name = f"P{turn_idx + 1}"
    for p in players:
        if p["player_index"] == turn_idx:
            turn_owner_name = p["name"]
            break

    rules_reminder = f"Press buttons to earn points! Target: {TARGET_SCORE}"
    if WIN_TYPE == "highest_score":
        rules_reminder = f"Score the most in {MAX_TURNS} turns!"
    if winner is not None:
        if winner == "draw":
            rules_reminder = "It's a draw!"
        else:
            wp = _player_by_user_id(session, winner)
            wname = wp["name"] if wp else str(winner)
            rules_reminder = f"🎉 {wname} wins!"

    win_condition = f"First to {TARGET_SCORE} points" if WIN_TYPE == "score_threshold" else f"Highest score after {MAX_TURNS} turns"

    # Footer actions
    footer_actions = []
    if winner is not None or phase == "finished":
        footer_actions.append({"label": "🔄 Play Again", "callback": "restart", "visible": True})
    else:
        footer_actions.append({"label": "🏳️ Forfeit", "callback": "forfeit", "visible": True})

    # Custom buttons
    footer_actions.append({"label": 'Score', "callback": 'action_1', "visible": True})

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
        "board": {"rows": 0, "cols": 0},
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

    # Restart
    if action == "restart":
        return init_game(session)

    # Forfeit
    if action == "forfeit":
        state["winner"] = "draw"
        session["current_phase"] = "finished"
        return session

    # Rules / Lobby
    if action in ("rules", "lobby"):
        return session

    # Only allow during play
    if session.get("current_phase") != "playing" or state["winner"] is not None:
        return session

    # Validate turn
    role_idx = None
    for p in session["players"]:
        if p["user_id"] == user_id:
            role_idx = p["player_index"]
            break
    if role_idx is None:
        return session
    if role_idx != session["current_turn_index"]:
        return session

    # Custom button handlers
    if action == 'action_1':
        state['scores'][str(user_id)] = state['scores'].get(str(user_id), 0) + 1
        state['moves'].append(f'Player {user_id} used Score')
        state['turn_count'] = state.get('turn_count', 0) + 1
        result = check_win(session)
        if result is not None:
            if isinstance(result, dict) and result.get('type') == 'win':
                state['winner'] = result['winner']
                session['current_phase'] = 'finished'
            elif isinstance(result, dict) and result.get('type') == 'draw':
                state['winner'] = 'draw'
                session['current_phase'] = 'finished'
        else:
            num_players = len(session['players'])
            session['current_turn_index'] = (session['current_turn_index'] + 1) % num_players
        return session

    # After action, advance turn
    state["turn_count"] = state.get("turn_count", 0) + 1

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


def check_win(session):
    """Check for a winner based on win condition."""
    state = session["game_state"]
    scores = state.get("scores", {})

    if WIN_TYPE == "score_threshold":
        for uid_str, score in scores.items():
            if score >= TARGET_SCORE:
                return {"type": "win", "winner": int(uid_str)}
        return None

    elif WIN_TYPE == "highest_score":
        if state.get("turn_count", 0) < MAX_TURNS:
            return None
        if not scores:
            return {"type": "draw"}
        max_score = max(scores.values())
        winners = [int(uid) for uid, s in scores.items() if s == max_score]
        if len(winners) == 1:
            return {"type": "win", "winner": winners[0]}
        return {"type": "draw"}

    return None


def serialize_state(session):
    """Serialize the game state for persistent storage."""
    return session.get("state", {})


def deserialize_state(data):
    """Deserialize game state from persistent storage."""
    if isinstance(data, str):
        data = json.loads(data)
    return data
