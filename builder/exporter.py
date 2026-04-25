"""
Game Exporter

Compiles a validated game configuration into a deployable plugin directory
containing manifest.json and logic.py, registers the game in the database,
and triggers the plugin loader for hot-reloading.

The generated logic.py implements all 6 required entry-point methods
(init_game, render, handle_callback, check_win, serialize_state,
deserialize_state) with game-type-specific logic for grid_strategy,
button_logic, hidden_role, elimination, and generic types.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import database.db as db
import config
from .steps import GAME_TYPES, WIN_TYPES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GameExporter
# ---------------------------------------------------------------------------

class GameExporter:
    """
    Compiles a validated game config into a deployable game plugin.

    Usage::

        exporter = GameExporter()
        result = exporter.export(config_dict, user_id=42)
        if result["success"]:
            print(f"Published: {result['slug']} at {result['path']}")
        else:
            print(f"Errors: {result['errors']}")
    """

    # Types that require a board
    BOARD_REQUIRED_TYPES = {
        gt for gt, meta in GAME_TYPES.items()
        if meta.get("requires_board", False) or meta.get("board_required", False)
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export(self, config: Dict[str, Any], user_id: int) -> Dict[str, Any]:
        """
        Full export pipeline: validate, generate, write, register.

        Returns::

            {
                "success": bool,
                "slug": str,
                "path": str,
                "errors": [str, ...],
            }
        """
        errors: List[str] = []

        # 1. Generate slug
        try:
            slug = self.generate_slug(config.get("game_name", ""))
        except ValueError as exc:
            errors.append(str(exc))
            return {"success": False, "slug": "", "path": "", "errors": errors}

        if not slug:
            errors.append("Could not generate a valid slug from the game name.")
            return {"success": False, "slug": "", "path": "", "errors": errors}

        # 2. Check for slug collision
        existing = db.fetchone("SELECT id FROM games WHERE slug = ?", (slug,))
        if existing:
            errors.append(f"A game with slug '{slug}' already exists.")
            return {"success": False, "slug": slug, "path": "", "errors": errors}

        # 3. Generate manifest
        try:
            manifest = self.generate_manifest(config)
        except Exception as exc:
            errors.append(f"Failed to generate manifest: {exc}")
            return {"success": False, "slug": slug, "path": "", "errors": errors}

        # 4. Generate logic code
        try:
            logic_code = self.generate_logic(config)
        except Exception as exc:
            errors.append(f"Failed to generate logic: {exc}")
            return {"success": False, "slug": slug, "path": "", "errors": errors}

        # 5. Validate generated logic compiles
        try:
            compile(logic_code, f"<{slug}_logic>", "exec")
        except SyntaxError as exc:
            errors.append(f"Generated logic has syntax error: {exc}")
            return {"success": False, "slug": slug, "path": "", "errors": errors}

        # 6. Create plugin directory and write files
        try:
            plugin_path = self.create_plugin_directory(slug)
            self.write_manifest(slug, manifest)
            self.write_logic(slug, logic_code)
        except OSError as exc:
            errors.append(f"Failed to write plugin files: {exc}")
            return {"success": False, "slug": slug, "path": "", "errors": errors}

        # 7. Register in database
        try:
            game_id = self.register_in_db(slug, config, user_id, manifest)
        except Exception as exc:
            errors.append(f"Failed to register game in database: {exc}")
            return {"success": False, "slug": slug, "path": str(plugin_path), "errors": errors}

        # 8. Assign ownership
        try:
            self.assign_ownership(user_id, slug, config.get("creator_name", ""))
        except Exception as exc:
            logger.warning("Failed to assign ownership for %s: %s", slug, exc)
            # Non-fatal

        # 9. Hot reload
        try:
            self.hot_reload(slug)
        except Exception as exc:
            logger.warning("Hot reload failed for %s: %s", slug, exc)
            # Non-fatal — game is on disk, will be loaded on next restart

        return {
            "success": True,
            "slug": slug,
            "path": str(plugin_path),
            "errors": [],
        }

    # ------------------------------------------------------------------
    # Slug generation
    # ------------------------------------------------------------------

    def generate_slug(self, game_name: str) -> str:
        """Convert a game name to a URL-safe slug (alphanumeric + underscore)."""
        if not game_name or not isinstance(game_name, str):
            raise ValueError("Game name is required for slug generation.")

        slug = game_name.lower().strip()
        # Replace spaces and common separators with underscores
        slug = re.sub(r'[\s\-]+', '_', slug)
        # Remove non-alphanumeric characters (except underscore)
        slug = re.sub(r'[^a-z0-9_]', '', slug)
        # Collapse multiple underscores
        slug = re.sub(r'_+', '_', slug)
        # Strip leading/trailing underscores
        slug = slug.strip('_')

        if len(slug) < 2:
            raise ValueError("Generated slug is too short — use a more descriptive game name.")
        if len(slug) > 50:
            slug = slug[:50].rstrip('_')

        return slug

    # ------------------------------------------------------------------
    # Manifest generation
    # ------------------------------------------------------------------

    def generate_manifest(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a complete manifest.json structure matching plugin_loader expectations.
        """
        name = config.get("game_name", "").strip()
        slug = self.generate_slug(name)
        gt = config.get("game_type", "button_logic")
        board_enabled = config.get("board_enabled", False) or gt in self.BOARD_REQUIRED_TYPES

        default_rows = config.get("board", {}).get("rows", 3)
        default_cols = config.get("board", {}).get("cols", 3)
        try:
            rows = int(config.get("board_rows", default_rows))
        except (ValueError, TypeError):
            rows = 3
        try:
            cols = int(config.get("board_cols", default_cols))
        except (ValueError, TypeError):
            cols = 3

        try:
            min_p = int(config.get("min_players", 2))
        except (ValueError, TypeError):
            min_p = 2
        try:
            max_p = int(config.get("max_players", 2))
        except (ValueError, TypeError):
            max_p = 2

        reward = 0.0
        fee = 0.0
        try:
            reward = float(config.get("reward_per_win", 0))
        except (ValueError, TypeError):
            pass
        try:
            fee = float(config.get("entry_fee", 0))
        except (ValueError, TypeError):
            pass

        buttons = config.get("buttons", [])
        if not isinstance(buttons, list):
            buttons = []

        win_type = config.get("win_logic", {}).get("type", "")
        win_config = config.get("win_logic", {})
        if not isinstance(win_config, dict):
            win_config = {}

        # Build win_condition description
        win_condition = self._build_win_condition_text(win_type, win_config)

        # Build UI template
        ui_template = {
            "header_enabled": True,
            "player_hud_enabled": True,
            "board_enabled": board_enabled,
            "status_enabled": True,
            "footer_enabled": True,
        }

        # Build HUD fields based on game type
        hud_fields = ["name", "badge", "score", "role", "turn"]
        if gt == "elimination":
            hud_fields = ["name", "badge", "health", "role", "turn", "is_alive"]
        elif gt == "hidden_role":
            hud_fields = ["name", "badge", "role", "turn", "is_alive"]

        manifest = {
            "slug": slug,
            "name": name,
            "creator": config.get("creator_name", "").strip(),
            "description": config.get("description", "").strip(),
            "version": "1.0.0",
            "game_type": gt,
            "mode": "turn_based",
            "min_players": min_p,
            "max_players": max_p,
            "board": {"rows": rows, "cols": cols} if board_enabled else {"rows": 0, "cols": 0},
            "rewards": {"entry_fee": fee, "win_reward": reward},
            "board_rows": rows if board_enabled else 0,
            "board_cols": cols if board_enabled else 0,
            "turn_based": True,
            "single_message_only": True,
            "win_condition": win_condition,
            "reward_sar": reward,
            "entry_fee_sar": fee,
            "allowed_chat_types": ["group", "private"],
            "required_files": ["logic.py"],
            "ui_template": ui_template,
            "hud_fields": hud_fields,
            "buttons": buttons,
            "builder_generated": True,
            "builder_config": config,
        }
        return manifest

    @staticmethod
    def _build_win_condition_text(win_type: str, win_config: Dict[str, Any]) -> str:
        """Build a human-readable win condition string."""
        if not win_type:
            return ""
        descriptions = {
            "line_match": f"{win_config.get('line_length', 3)} in a row",
            "last_standing": "Last player alive wins",
            "score_threshold": f"First to {win_config.get('target_score', 10)} points",
            "highest_score": f"Highest score after {win_config.get('max_turns', 10)} turns",
            "role_reveal": "Identify the hidden roles",
            "board_full": "Best placement when board is full",
            "majority_control": "Control more than half the board",
        }
        return descriptions.get(win_type, win_type.replace('_', ' ').title())

    # ------------------------------------------------------------------
    # Logic generation
    # ------------------------------------------------------------------

    def generate_logic(self, config: Dict[str, Any]) -> str:
        """
        Generate a complete, working logic.py source code as a string.

        The generated code implements all 6 required entry-point methods
        and is tailored to the game_type specified in the config.
        """
        gt = config.get("game_type", "button_logic")
        generators = {
            "grid_strategy": self._generate_grid_strategy_logic,
            "button_logic": self._generate_button_logic_logic,
            "hidden_role": self._generate_hidden_role_logic,
            "elimination": self._generate_elimination_logic,
        }
        generator = generators.get(gt, self._generate_generic_logic)
        return generator(config)

    # ------------------------------------------------------------------
    # Grid Strategy Logic
    # ------------------------------------------------------------------

    def _generate_grid_strategy_logic(self, config: Dict[str, Any]) -> str:
        name = config.get("game_name", "Untitled").strip()
        slug = self.generate_slug(name)

        try:
            rows = int(config.get("board_rows", 3))
        except (ValueError, TypeError):
            rows = 3
        try:
            cols = int(config.get("board_cols", 3))
        except (ValueError, TypeError):
            cols = 3

        buttons = config.get("buttons", [])
        if not isinstance(buttons, list):
            buttons = []

        win_type = config.get("win_type", "line_match")
        win_config = config.get("win_logic", {})
        if not isinstance(win_config, dict):
            win_config = {}

        line_length = int(win_config.get("line_length", min(rows, cols, 3)))

        # Build button definitions
        button_defs = self._build_button_definitions(buttons)
        button_handler_code = self._build_button_handler_code(buttons, "grid_strategy")

        # Player symbols for placement
        player_symbols = ["🔴", "🔵", "🟢", "🟣", "🟡", "🟠", "⚪", "⚫",
                          "❤️", "💎", "🍀", "♠️", "🍎", "🍊", "🍋", "🍇",
                          "🍓", "🍑", "🍒", "🥝"]

        code = f'''"""Auto-generated game plugin for {name}."""
import json


# ── Configuration ────────────────────────────────────────────────────────────

EMPTY_CELL = "  "
GAME_NAME = {repr(name)}
GAME_SLUG = {repr(slug)}
BOARD_ROWS = {rows}
BOARD_COLS = {cols}
LINE_LENGTH = {line_length}
PLAYER_SYMBOLS = {repr(player_symbols[:20])}

# Button definitions
{button_defs}


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
                    return {{"type": "win", "winner": val, "line": line[:LINE_LENGTH]}}
    # Check draw
    if all(board[r][c] != EMPTY_CELL for r in range(BOARD_ROWS) for c in range(BOARD_COLS)):
        return {{"type": "draw"}}
    return None


# ── Required API ─────────────────────────────────────────────────────────────

def init_game(session):
    """Initialize a new game session."""
    state = {{
        "board": [[EMPTY_CELL for _ in range(BOARD_COLS)] for _ in range(BOARD_ROWS)],
        "moves": [],
        "winner": None,
        "scores": {{}},
    }}
    for p in session["players"]:
        state["scores"][str(p["user_id"])] = 0
    session["state"] = state
    session["current_turn_index"] = 0
    session["current_phase"] = "playing"
    return session


def render(session):
    """Build the render context for the UI engine."""
    state = session["state"]
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
        player_hud.append({{
            "name": p["name"],
            "badge": symbol,
            "balance": 0.0,
            "wins": p.get("score", 0),
            "role": f"P{{idx + 1}}",
            "is_turn": is_turn,
            "is_alive": True,
            "score": state["scores"].get(str(p["user_id"]), 0),
            "color": symbol,
        }})

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
                row_actions.append(f"place:{{r}},{{c}}")
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
    turn_owner_name = f"P{{turn_idx + 1}}"
    for p in players:
        if p["player_index"] == turn_idx:
            turn_owner_name = p["name"]
            break

    rules_reminder = "Tap an empty cell to place your mark."
    if winner is not None:
        if isinstance(winner, str) and winner != "draw":
            rules_reminder = f"🎉 {{winner}} wins with {{LINE_LENGTH}} in a row!"
        elif winner == "draw":
            rules_reminder = "It's a draw! The board is full."

    # Footer
    footer_actions = []
    if winner is not None or phase == "finished":
        footer_actions.append({{"label": "🔄 Play Again", "callback": "restart", "visible": True}})
    else:
        footer_actions.append({{"label": "🏳️ Forfeit", "callback": "forfeit", "visible": True}})

    # Add custom buttons
{self._indent(self._build_footer_buttons_code(buttons), 4)}

    navigation = [
        {{"label": "🏠 Lobby", "callback": "lobby"}},
        {{"label": "📋 Rules", "callback": "rules"}},
    ]

    return {{
        "header": {{
            "game_name": GAME_NAME,
            "room_id": session.get("room_id", ""),
            "mode": session.get("mode", "multiplayer"),
            "visibility": session.get("visibility", "public"),
            "status": session.get("status", "active"),
        }},
        "players": player_hud,
        "board": {{
            "rows": BOARD_ROWS,
            "cols": BOARD_COLS,
            "cells": cells,
            "cell_actions": cell_actions,
            "hidden": hidden,
        }},
        "state": {{
            "phase": phase,
            "turn_owner": turn_owner_name,
            "countdown": None,
            "rules_reminder": rules_reminder,
            "win_condition": f"{{LINE_LENGTH}} in a row",
            "activity_log": activity_log,
        }},
        "footer": {{
            "actions": footer_actions,
            "navigation": navigation,
        }},
    }}


def handle_callback(session, user_id, action):
    """Process a player action and return updated session."""
    state = session["state"]

    # Restart
    if action == "restart":
        return init_game(session)

    # Forfeit
    if action == "forfeit":
        role_idx = _player_role(session, user_id)
        if role_idx is None:
            return session
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
        state["moves"].append(f"{{symbol}} {{player_name}} placed at ({{row}},{{col}})")

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
{self._indent(button_handler_code, 4)}

    return session


def check_win(session):
    """Check the board for a winner or draw."""
    board = session["state"]["board"]
    return _check_winner(board)


def serialize_state(session):
    """Serialize the game state for persistent storage."""
    return session.get("state", {{}})


def deserialize_state(data):
    """Deserialize game state from persistent storage."""
    if isinstance(data, str):
        data = json.loads(data)
    return data
'''
        return code

    # ------------------------------------------------------------------
    # Button Logic
    # ------------------------------------------------------------------

    def _generate_button_logic_logic(self, config: Dict[str, Any]) -> str:
        name = config.get("game_name", "Untitled").strip()
        slug = self.generate_slug(name)

        try:
            min_p = int(config.get("min_players", 2))
        except (ValueError, TypeError):
            min_p = 2
        try:
            max_p = int(config.get("max_players", 4))
        except (ValueError, TypeError):
            max_p = 4

        buttons = config.get("buttons", [])
        if not isinstance(buttons, list):
            buttons = []

        win_type = config.get("win_type", "score_threshold")
        win_config = config.get("win_logic", {})
        if not isinstance(win_config, dict):
            win_config = {}

        target_score = int(win_config.get("target_score", 10))
        max_turns = int(win_config.get("max_turns", 20))

        button_defs = self._build_button_definitions(buttons)
        button_handler_code = self._build_button_handler_code(buttons, "button_logic")

        code = f'''"""Auto-generated game plugin for {name}."""
import json


# ── Configuration ────────────────────────────────────────────────────────────

EMPTY_CELL = "  "
GAME_NAME = {repr(name)}
GAME_SLUG = {repr(slug)}
MIN_PLAYERS = {min_p}
MAX_PLAYERS = {max_p}
WIN_TYPE = {repr(win_type)}
TARGET_SCORE = {target_score}
MAX_TURNS = {max_turns}
PLAYER_COLORS = ["🔴", "🔵", "🟢", "🟣", "🟡", "🟠", "⚪", "⚫",
                 "❤️", "💎", "🍀", "♠️", "🍎", "🍊", "🍋", "🍇",
                 "🍓", "🍑", "🍒", "🥝"]

# Button definitions
{button_defs}


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
    state = {{
        "scores": {{}},
        "moves": [],
        "winner": None,
        "turn_count": 0,
    }}
    for p in session["players"]:
        state["scores"][str(p["user_id"])] = 0
    session["state"] = state
    session["current_turn_index"] = 0
    session["current_phase"] = "playing"
    return session


def render(session):
    """Build the render context for the UI engine."""
    state = session["state"]
    players = session["players"]
    phase = session.get("current_phase", "playing")
    winner = state.get("winner")

    # Player HUD
    player_hud = []
    for p in players:
        idx = p["player_index"]
        color = PLAYER_COLORS[idx % len(PLAYER_COLORS)]
        is_turn = (idx == session.get("current_turn_index", 0)) and phase == "playing"
        player_hud.append({{
            "name": p["name"],
            "badge": color,
            "balance": 0.0,
            "wins": p.get("score", 0),
            "role": f"P{{idx + 1}}",
            "is_turn": is_turn,
            "is_alive": True,
            "score": state["scores"].get(str(p["user_id"]), 0),
            "color": color,
        }})

    # Activity log
    activity_log = list(state.get("moves", [])[-10:])

    # Turn owner
    turn_idx = session.get("current_turn_index", 0)
    turn_owner_name = f"P{{turn_idx + 1}}"
    for p in players:
        if p["player_index"] == turn_idx:
            turn_owner_name = p["name"]
            break

    rules_reminder = f"Press buttons to earn points! Target: {{TARGET_SCORE}}"
    if WIN_TYPE == "highest_score":
        rules_reminder = f"Score the most in {{MAX_TURNS}} turns!"
    if winner is not None:
        if winner == "draw":
            rules_reminder = "It's a draw!"
        else:
            wp = _player_by_user_id(session, winner)
            wname = wp["name"] if wp else str(winner)
            rules_reminder = f"🎉 {{wname}} wins!"

    win_condition = f"First to {{TARGET_SCORE}} points" if WIN_TYPE == "score_threshold" else f"Highest score after {{MAX_TURNS}} turns"

    # Footer actions
    footer_actions = []
    if winner is not None or phase == "finished":
        footer_actions.append({{"label": "🔄 Play Again", "callback": "restart", "visible": True}})
    else:
        footer_actions.append({{"label": "🏳️ Forfeit", "callback": "forfeit", "visible": True}})

{self._indent(self._build_footer_buttons_code(buttons), 4)}

    navigation = [
        {{"label": "🏠 Lobby", "callback": "lobby"}},
        {{"label": "📋 Rules", "callback": "rules"}},
    ]

    return {{
        "header": {{
            "game_name": GAME_NAME,
            "room_id": session.get("room_id", ""),
            "mode": session.get("mode", "multiplayer"),
            "visibility": session.get("visibility", "public"),
            "status": session.get("status", "active"),
        }},
        "players": player_hud,
        "board": {{"rows": 0, "cols": 0}},
        "state": {{
            "phase": phase,
            "turn_owner": turn_owner_name,
            "countdown": None,
            "rules_reminder": rules_reminder,
            "win_condition": win_condition,
            "activity_log": activity_log,
        }},
        "footer": {{
            "actions": footer_actions,
            "navigation": navigation,
        }},
    }}


def handle_callback(session, user_id, action):
    """Process a player action and return updated session."""
    state = session["state"]

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
{self._indent(button_handler_code, 4)}

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
    state = session["state"]
    scores = state.get("scores", {{}})

    if WIN_TYPE == "score_threshold":
        for uid_str, score in scores.items():
            if score >= TARGET_SCORE:
                return {{"type": "win", "winner": int(uid_str)}}
        return None

    elif WIN_TYPE == "highest_score":
        if state.get("turn_count", 0) < MAX_TURNS:
            return None
        if not scores:
            return {{"type": "draw"}}
        max_score = max(scores.values())
        winners = [int(uid) for uid, s in scores.items() if s == max_score]
        if len(winners) == 1:
            return {{"type": "win", "winner": winners[0]}}
        return {{"type": "draw"}}

    return None


def serialize_state(session):
    """Serialize the game state for persistent storage."""
    return session.get("state", {{}})


def deserialize_state(data):
    """Deserialize game state from persistent storage."""
    if isinstance(data, str):
        data = json.loads(data)
    return data
'''
        return code

    # ------------------------------------------------------------------
    # Hidden Role Logic
    # ------------------------------------------------------------------

    def _generate_hidden_role_logic(self, config: Dict[str, Any]) -> str:
        name = config.get("game_name", "Untitled").strip()
        slug = self.generate_slug(name)

        try:
            min_p = int(config.get("min_players", 4))
        except (ValueError, TypeError):
            min_p = 4
        try:
            max_p = int(config.get("max_players", 8))
        except (ValueError, TypeError):
            max_p = 8

        buttons = config.get("buttons", [])
        if not isinstance(buttons, list):
            buttons = []

        win_type = config.get("win_type", "role_reveal")
        win_config = config.get("win_logic", {})
        if not isinstance(win_config, dict):
            win_config = {}

        winning_roles = win_config.get("winning_roles", ["mafia"])
        if isinstance(winning_roles, str):
            winning_roles = [winning_roles]

        button_defs = self._build_button_definitions(buttons)
        button_handler_code = self._build_button_handler_code(buttons, "hidden_role")

        # Default roles pool
        role_pool = ["villager", "mafia", "detective", "doctor"]

        code = f'''"""Auto-generated game plugin for {name}."""
import json
import random


# ── Configuration ────────────────────────────────────────────────────────────

EMPTY_CELL = "  "
GAME_NAME = {repr(name)}
GAME_SLUG = {repr(slug)}
MIN_PLAYERS = {min_p}
MAX_PLAYERS = {max_p}
WIN_TYPE = {repr(win_type)}
WINNING_ROLES = {repr(winning_roles)}
ROLE_POOL = {repr(role_pool)}
ROLE_EMOJI = {{
    "mafia": "🔫", "detective": "🔍", "doctor": "💊",
    "villager": "🏠", "werewolf": "🐺", "seer": "🔮",
    "hunter": "🏹", "hidden": "❓",
}}
PLAYER_COLORS = ["🔴", "🔵", "🟢", "🟣", "🟡", "🟠", "⚪", "⚫"]

# Button definitions
{button_defs}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _assign_roles(num_players):
    """Assign roles to players. At least 1 mafia, rest balanced."""
    roles = ["mafia"]
    num_mafia = max(1, num_players // 4)
    for _ in range(num_mafia - 1):
        if len(roles) < num_players:
            roles.append("mafia")
    special_roles = ["detective", "doctor"]
    for sr in special_roles:
        if len(roles) < num_players:
            roles.append(sr)
    while len(roles) < num_players:
        roles.append("villager")
    random.shuffle(roles)
    return roles[:num_players]


def _player_by_user_id(session, user_id):
    """Return the player dict for a given user_id, or None."""
    for p in session["players"]:
        if p["user_id"] == user_id:
            return p
    return None


def _count_alive_by_role(session, role):
    """Count alive players with a given role."""
    state = session["state"]
    count = 0
    for uid_str, pdata in state.get("player_roles", {{}}).items():
        if pdata.get("role") == role and pdata.get("alive", True):
            count += 1
    return count


# ── Required API ─────────────────────────────────────────────────────────────

def init_game(session):
    """Initialize a new game session."""
    players = session["players"]
    roles = _assign_roles(len(players))
    player_roles = {{}}
    for i, p in enumerate(players):
        role = roles[i] if i < len(roles) else "villager"
        player_roles[str(p["user_id"])] = {{
            "role": role,
            "alive": True,
            "revealed": False,
        }}

    state = {{
        "player_roles": player_roles,
        "moves": [],
        "winner": None,
        "phase": "day",
        "votes": {{}},
        "round": 1,
    }}
    session["state"] = state
    session["current_turn_index"] = 0
    session["current_phase"] = "playing"
    return session


def render(session):
    """Build the render context for the UI engine."""
    state = session["state"]
    players = session["players"]
    phase = session.get("current_phase", "playing")
    winner = state.get("winner")

    # Player HUD
    player_hud = []
    for p in players:
        idx = p["player_index"]
        color = PLAYER_COLORS[idx % len(PLAYER_COLORS)]
        is_turn = (idx == session.get("current_turn_index", 0)) and phase == "playing"
        pdata = state["player_roles"].get(str(p["user_id"]), {{}})
        alive = pdata.get("alive", True)
        role = pdata.get("role", "villager")
        revealed = pdata.get("revealed", False)
        role_display = ROLE_EMOJI.get(role, "❓") + " " + role.title() if revealed else "❓ Hidden"
        player_hud.append({{
            "name": p["name"],
            "badge": color,
            "balance": 0.0,
            "wins": p.get("score", 0),
            "role": role_display,
            "is_turn": is_turn,
            "is_alive": alive,
            "score": 0,
            "color": color,
        }})

    # Activity log
    activity_log = list(state.get("moves", [])[-10:])

    # Turn owner
    turn_idx = session.get("current_turn_index", 0)
    turn_owner_name = f"P{{turn_idx + 1}}"
    for p in players:
        if p["player_index"] == turn_idx:
            turn_owner_name = p["name"]
            break

    game_phase = state.get("phase", "day")
    rules_reminder = f"Phase: {{game_phase.title()}}. Vote to eliminate a suspect!"
    if winner is not None:
        if winner == "mafia":
            rules_reminder = "🔫 Mafia wins! They have taken over."
        elif winner == "village":
            rules_reminder = "🏠 Village wins! All mafia eliminated."
        else:
            rules_reminder = "Game over!"

    # Footer actions
    footer_actions = []
    if winner is not None or phase == "finished":
        footer_actions.append({{"label": "🔄 Play Again", "callback": "restart", "visible": True}})
    else:
        footer_actions.append({{"label": "🏳️ Forfeit", "callback": "forfeit", "visible": True}})

{self._indent(self._build_footer_buttons_code(buttons), 4)}

    navigation = [
        {{"label": "🏠 Lobby", "callback": "lobby"}},
        {{"label": "📋 Rules", "callback": "rules"}},
    ]

    return {{
        "header": {{
            "game_name": GAME_NAME,
            "room_id": session.get("room_id", ""),
            "mode": session.get("mode", "multiplayer"),
            "visibility": session.get("visibility", "public"),
            "status": session.get("status", "active"),
        }},
        "players": player_hud,
        "board": {{"rows": 0, "cols": 0}},
        "state": {{
            "phase": phase,
            "turn_owner": turn_owner_name,
            "countdown": None,
            "rules_reminder": rules_reminder,
            "win_condition": "Eliminate the hidden role(s)",
            "activity_log": activity_log,
        }},
        "footer": {{
            "actions": footer_actions,
            "navigation": navigation,
        }},
    }}


def handle_callback(session, user_id, action):
    """Process a player action and return updated session."""
    state = session["state"]

    # Restart
    if action == "restart":
        return init_game(session)

    # Forfeit
    if action == "forfeit":
        pdata = state["player_roles"].get(str(user_id), {{}})
        pdata["alive"] = False
        pdata["revealed"] = True
        state["moves"].append(f"Player {{user_id}} forfeited (was {{pdata.get('role', 'unknown')}})")
        result = check_win(session)
        if result is not None:
            state["winner"] = result.get("winner", "draw")
            session["current_phase"] = "finished"
        return session

    # Rules / Lobby
    if action in ("rules", "lobby"):
        return session

    # Only allow during play
    if session.get("current_phase") != "playing" or state["winner"] is not None:
        return session

    # Verify player is alive
    pdata = state["player_roles"].get(str(user_id), {{}})
    if not pdata.get("alive", True):
        return session

    # Vote action
    if action.startswith("vote:"):
        try:
            target_id = int(action.split(":", 1)[1])
        except (ValueError, IndexError):
            return session
        state["votes"][str(user_id)] = target_id
        state["moves"].append(f"Player {{user_id}} voted for {{target_id}}")
        # If all alive players have voted, process the vote
        alive_count = sum(1 for pd in state["player_roles"].values() if pd.get("alive", True))
        if len(state["votes"]) >= alive_count:
            vote_counts = {{}}
            for voter, target in state["votes"].items():
                vote_counts[target] = vote_counts.get(target, 0) + 1
            if vote_counts:
                max_votes = max(vote_counts.values())
                targets = [t for t, c in vote_counts.items() if c == max_votes]
                eliminated_id = targets[0]
                epdata = state["player_roles"].get(str(eliminated_id), {{}})
                epdata["alive"] = False
                epdata["revealed"] = True
                role = epdata.get("role", "villager")
                state["moves"].append(f"Player {{eliminated_id}} was eliminated! They were {{ROLE_EMOJI.get(role, '❓')}} {{role}}")
                state["votes"] = {{}}
                result = check_win(session)
                if result is not None:
                    state["winner"] = result.get("winner", "draw")
                    session["current_phase"] = "finished"
        return session

    # Reveal role action
    if action == "reveal_role":
        pdata["revealed"] = True
        state["moves"].append(f"Player {{user_id}} revealed their role")
        return session

    # Custom button handlers
{self._indent(button_handler_code, 4)}

    return session


def check_win(session):
    """Check for a winner based on role elimination."""
    state = session["state"]
    player_roles = state.get("player_roles", {{}})

    mafia_alive = sum(1 for pd in player_roles.values() if pd.get("role") in WINNING_ROLES and pd.get("alive", True))
    village_alive = sum(1 for pd in player_roles.values() if pd.get("role") not in WINNING_ROLES and pd.get("alive", True))

    if mafia_alive == 0:
        return {{"type": "win", "winner": "village"}}
    if mafia_alive >= village_alive:
        return {{"type": "win", "winner": "mafia"}}
    return None


def serialize_state(session):
    """Serialize the game state for persistent storage."""
    return session.get("state", {{}})


def deserialize_state(data):
    """Deserialize game state from persistent storage."""
    if isinstance(data, str):
        data = json.loads(data)
    return data
'''
        return code

    # ------------------------------------------------------------------
    # Elimination Logic
    # ------------------------------------------------------------------

    def _generate_elimination_logic(self, config: Dict[str, Any]) -> str:
        name = config.get("game_name", "Untitled").strip()
        slug = self.generate_slug(name)

        try:
            min_p = int(config.get("min_players", 2))
        except (ValueError, TypeError):
            min_p = 2
        try:
            max_p = int(config.get("max_players", 8))
        except (ValueError, TypeError):
            max_p = 8

        buttons = config.get("buttons", [])
        if not isinstance(buttons, list):
            buttons = []

        win_type = config.get("win_type", "last_standing")
        win_config = config.get("win_logic", {})
        if not isinstance(win_config, dict):
            win_config = {}

        initial_health = int(win_config.get("initial_health", 3))

        button_defs = self._build_button_definitions(buttons)
        button_handler_code = self._build_button_handler_code(buttons, "elimination")

        code = f'''"""Auto-generated game plugin for {name}."""
import json


# ── Configuration ────────────────────────────────────────────────────────────

EMPTY_CELL = "  "
GAME_NAME = {repr(name)}
GAME_SLUG = {repr(slug)}
MIN_PLAYERS = {min_p}
MAX_PLAYERS = {max_p}
WIN_TYPE = {repr(win_type)}
INITIAL_HEALTH = {initial_health}
ATTACK_DAMAGE = 1
HEAL_AMOUNT = 1
PLAYER_COLORS = ["🔴", "🔵", "🟢", "🟣", "🟡", "🟠", "⚪", "⚫",
                 "❤️", "💎", "🍀", "♠️", "🍎", "🍊", "🍋", "🍇",
                 "🍓", "🍑", "🍒", "🥝"]

# Button definitions
{button_defs}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _player_by_user_id(session, user_id):
    """Return the player dict for a given user_id, or None."""
    for p in session["players"]:
        if p["user_id"] == user_id:
            return p
    return None


def _alive_players(session):
    """Return list of alive player user_ids."""
    state = session["state"]
    return [int(uid) for uid, hp in state.get("health", {{}}).items() if hp > 0]


# ── Required API ─────────────────────────────────────────────────────────────

def init_game(session):
    """Initialize a new game session."""
    state = {{
        "health": {{}},
        "moves": [],
        "winner": None,
    }}
    for p in session["players"]:
        state["health"][str(p["user_id"])] = INITIAL_HEALTH
    session["state"] = state
    session["current_turn_index"] = 0
    session["current_phase"] = "playing"
    return session


def render(session):
    """Build the render context for the UI engine."""
    state = session["state"]
    players = session["players"]
    phase = session.get("current_phase", "playing")
    winner = state.get("winner")

    # Player HUD
    player_hud = []
    for p in players:
        idx = p["player_index"]
        color = PLAYER_COLORS[idx % len(PLAYER_COLORS)]
        is_turn = (idx == session.get("current_turn_index", 0)) and phase == "playing"
        hp = state["health"].get(str(p["user_id"]), 0)
        alive = hp > 0
        hearts = "❤️" * min(hp, 5) + "💔" if not alive else ""
        if hp > 5:
            hearts = f"❤️x{{hp}}"
        player_hud.append({{
            "name": p["name"],
            "badge": color,
            "balance": 0.0,
            "wins": p.get("score", 0),
            "role": hearts,
            "is_turn": is_turn,
            "is_alive": alive,
            "score": hp,
            "color": color,
        }})

    # Activity log
    activity_log = list(state.get("moves", [])[-10:])

    # Turn owner
    turn_idx = session.get("current_turn_index", 0)
    turn_owner_name = f"P{{turn_idx + 1}}"
    for p in players:
        if p["player_index"] == turn_idx:
            turn_owner_name = p["name"]
            break

    rules_reminder = f"⚔️ Attack or 🛡 Defend! HP: {{INITIAL_HEALTH}}"
    if winner is not None:
        if isinstance(winner, int):
            wp = _player_by_user_id(session, winner)
            wname = wp["name"] if wp else str(winner)
            rules_reminder = f"🏆 {{wname}} is the last one standing!"
        else:
            rules_reminder = "Game over!"

    # Footer actions — include attack buttons for alive opponents
    footer_actions = []
    if winner is not None or phase == "finished":
        footer_actions.append({{"label": "🔄 Play Again", "callback": "restart", "visible": True}})
    else:
        # Attack buttons for each alive opponent
        current_uid = None
        for p in players:
            if p["player_index"] == session.get("current_turn_index", 0):
                current_uid = p["user_id"]
                break
        if current_uid is not None:
            for p in players:
                if p["user_id"] != current_uid and state["health"].get(str(p["user_id"]), 0) > 0:
                    footer_actions.append({{
                        "label": f"⚔️ {{p['name']}}",
                        "callback": f"attack:{{p['user_id']}}",
                        "visible": True,
                    }})
        footer_actions.append({{"label": "🛡 Defend", "callback": "defend", "visible": True}})
        footer_actions.append({{"label": "💊 Heal", "callback": "heal", "visible": True}})
        footer_actions.append({{"label": "🏳️ Forfeit", "callback": "forfeit", "visible": True}})

{self._indent(self._build_footer_buttons_code(buttons), 4)}

    navigation = [
        {{"label": "🏠 Lobby", "callback": "lobby"}},
        {{"label": "📋 Rules", "callback": "rules"}},
    ]

    return {{
        "header": {{
            "game_name": GAME_NAME,
            "room_id": session.get("room_id", ""),
            "mode": session.get("mode", "multiplayer"),
            "visibility": session.get("visibility", "public"),
            "status": session.get("status", "active"),
        }},
        "players": player_hud,
        "board": {{"rows": 0, "cols": 0}},
        "state": {{
            "phase": phase,
            "turn_owner": turn_owner_name,
            "countdown": None,
            "rules_reminder": rules_reminder,
            "win_condition": "Last player standing",
            "activity_log": activity_log,
        }},
        "footer": {{
            "actions": footer_actions,
            "navigation": navigation,
        }},
    }}


def handle_callback(session, user_id, action):
    """Process a player action and return updated session."""
    state = session["state"]

    # Restart
    if action == "restart":
        return init_game(session)

    # Forfeit
    if action == "forfeit":
        state["health"][str(user_id)] = 0
        state["moves"].append(f"💀 Player {{user_id}} forfeited")
        result = check_win(session)
        if result is not None:
            state["winner"] = result.get("winner", "draw")
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

    # Verify player is alive
    if state["health"].get(str(user_id), 0) <= 0:
        return session

    # Attack action
    if action.startswith("attack:"):
        try:
            target_id = int(action.split(":", 1)[1])
        except (ValueError, IndexError):
            return session
        target_hp = state["health"].get(str(target_id), 0)
        if target_hp <= 0:
            return session
        new_hp = max(0, target_hp - ATTACK_DAMAGE)
        state["health"][str(target_id)] = new_hp
        target_name = str(target_id)
        for p in session["players"]:
            if p["user_id"] == target_id:
                target_name = p["name"]
                break
        if new_hp <= 0:
            state["moves"].append(f"💀 {{target_name}} was eliminated by {{user_id}}!")
        else:
            state["moves"].append(f"⚔️ {{user_id}} attacked {{target_name}} (HP: {{new_hp}})")
        result = check_win(session)
        if result is not None:
            state["winner"] = result.get("winner", "draw")
            session["current_phase"] = "finished"
        else:
            num_players = len(session["players"])
            session["current_turn_index"] = (session["current_turn_index"] + 1) % num_players
        return session

    # Defend action
    if action == "defend":
        state["moves"].append(f"🛡 Player {{user_id}} defended")
        num_players = len(session["players"])
        session["current_turn_index"] = (session["current_turn_index"] + 1) % num_players
        return session

    # Heal action
    if action == "heal":
        current_hp = state["health"].get(str(user_id), 0)
        new_hp = min(INITIAL_HEALTH, current_hp + HEAL_AMOUNT)
        state["health"][str(user_id)] = new_hp
        state["moves"].append(f"💊 Player {{user_id}} healed (HP: {{new_hp}})")
        num_players = len(session["players"])
        session["current_turn_index"] = (session["current_turn_index"] + 1) % num_players
        return session

    # Custom button handlers
{self._indent(button_handler_code, 4)}

    return session


def check_win(session):
    """Check for a winner — last player alive wins."""
    state = session["state"]
    alive = _alive_players(session)
    if len(alive) == 1:
        return {{"type": "win", "winner": alive[0]}}
    if len(alive) == 0:
        return {{"type": "draw"}}
    return None


def serialize_state(session):
    """Serialize the game state for persistent storage."""
    return session.get("state", {{}})


def deserialize_state(data):
    """Deserialize game state from persistent storage."""
    if isinstance(data, str):
        data = json.loads(data)
    return data
'''
        return code

    # ------------------------------------------------------------------
    # Generic / Fallback Logic
    # ------------------------------------------------------------------

    def _generate_generic_logic(self, config: Dict[str, Any]) -> str:
        name = config.get("game_name", "Untitled").strip()
        slug = self.generate_slug(name)
        gt = config.get("game_type", "score_attack")

        try:
            min_p = int(config.get("min_players", 2))
        except (ValueError, TypeError):
            min_p = 2
        try:
            max_p = int(config.get("max_players", 4))
        except (ValueError, TypeError):
            max_p = 4

        board_enabled = config.get("board_enabled", False) or gt in self.BOARD_REQUIRED_TYPES
        try:
            rows = int(config.get("board_rows", 3))
        except (ValueError, TypeError):
            rows = 3
        try:
            cols = int(config.get("board_cols", 3))
        except (ValueError, TypeError):
            cols = 3

        buttons = config.get("buttons", [])
        if not isinstance(buttons, list):
            buttons = []

        win_type = config.get("win_type", "highest_score")
        win_config = config.get("win_logic", {})
        if not isinstance(win_config, dict):
            win_config = {}

        target_score = int(win_config.get("target_score", 10))
        max_turns = int(win_config.get("max_turns", 20))

        button_defs = self._build_button_definitions(buttons)
        button_handler_code = self._build_button_handler_code(buttons, gt)

        code = f'''"""Auto-generated game plugin for {name}."""
import json


# ── Configuration ────────────────────────────────────────────────────────────

EMPTY_CELL = "  "
GAME_NAME = {repr(name)}
GAME_SLUG = {repr(slug)}
GAME_TYPE = {repr(gt)}
MIN_PLAYERS = {min_p}
MAX_PLAYERS = {max_p}
BOARD_ENABLED = {board_enabled}
BOARD_ROWS = {rows}
BOARD_COLS = {cols}
WIN_TYPE = {repr(win_type)}
TARGET_SCORE = {target_score}
MAX_TURNS = {max_turns}
PLAYER_COLORS = ["🔴", "🔵", "🟢", "🟣", "🟡", "🟠", "⚪", "⚫",
                 "❤️", "💎", "🍀", "♠️", "🍎", "🍊", "🍋", "🍇",
                 "🍓", "🍑", "🍒", "🥝"]

# Button definitions
{button_defs}


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
    state = {{
        "board": [[EMPTY_CELL for _ in range(BOARD_COLS)] for _ in range(BOARD_ROWS)] if BOARD_ENABLED else [],
        "scores": {{}},
        "moves": [],
        "winner": None,
        "turn_count": 0,
    }}
    for p in session["players"]:
        state["scores"][str(p["user_id"])] = 0
    session["state"] = state
    session["current_turn_index"] = 0
    session["current_phase"] = "playing"
    return session


def render(session):
    """Build the render context for the UI engine."""
    state = session["state"]
    players = session["players"]
    phase = session.get("current_phase", "playing")
    winner = state.get("winner")

    # Player HUD
    player_hud = []
    for p in players:
        idx = p["player_index"]
        color = PLAYER_COLORS[idx % len(PLAYER_COLORS)]
        is_turn = (idx == session.get("current_turn_index", 0)) and phase == "playing"
        player_hud.append({{
            "name": p["name"],
            "badge": color,
            "balance": 0.0,
            "wins": p.get("score", 0),
            "role": f"P{{idx + 1}}",
            "is_turn": is_turn,
            "is_alive": True,
            "score": state["scores"].get(str(p["user_id"]), 0),
            "color": color,
        }})

    # Board (if enabled)
    board_data = {{"rows": 0, "cols": 0}}
    if BOARD_ENABLED and state.get("board"):
        board = state["board"]
        cells = []
        cell_actions = []
        hidden = []
        for r in range(BOARD_ROWS):
            row_cells = []
            row_actions = []
            row_hidden = []
            for c in range(BOARD_COLS):
                val = board[r][c] if r < len(board) and c < len(board[r]) else EMPTY_CELL
                row_cells.append(val if val != EMPTY_CELL else "·")
                if val == EMPTY_CELL and phase == "playing" and winner is None:
                    row_actions.append(f"place:{{r}},{{c}}")
                else:
                    row_actions.append(None)
                row_hidden.append(False)
            cells.append(row_cells)
            cell_actions.append(row_actions)
            hidden.append(row_hidden)
        board_data = {{
            "rows": BOARD_ROWS,
            "cols": BOARD_COLS,
            "cells": cells,
            "cell_actions": cell_actions,
            "hidden": hidden,
        }}

    # Activity log
    activity_log = list(state.get("moves", [])[-10:])

    # Turn owner
    turn_idx = session.get("current_turn_index", 0)
    turn_owner_name = f"P{{turn_idx + 1}}"
    for p in players:
        if p["player_index"] == turn_idx:
            turn_owner_name = p["name"]
            break

    rules_reminder = "Take your turn!"
    if winner is not None:
        if isinstance(winner, int):
            wp = _player_by_user_id(session, winner)
            wname = wp["name"] if wp else str(winner)
            rules_reminder = f"🎉 {{wname}} wins!"
        elif winner == "draw":
            rules_reminder = "It's a draw!"
        else:
            rules_reminder = "Game over!"

    win_condition = WIN_TYPE.replace('_', ' ').title()

    # Footer actions
    footer_actions = []
    if winner is not None or phase == "finished":
        footer_actions.append({{"label": "🔄 Play Again", "callback": "restart", "visible": True}})
    else:
        footer_actions.append({{"label": "🏳️ Forfeit", "callback": "forfeit", "visible": True}})

{self._indent(self._build_footer_buttons_code(buttons), 4)}

    navigation = [
        {{"label": "🏠 Lobby", "callback": "lobby"}},
        {{"label": "📋 Rules", "callback": "rules"}},
    ]

    return {{
        "header": {{
            "game_name": GAME_NAME,
            "room_id": session.get("room_id", ""),
            "mode": session.get("mode", "multiplayer"),
            "visibility": session.get("visibility", "public"),
            "status": session.get("status", "active"),
        }},
        "players": player_hud,
        "board": board_data,
        "state": {{
            "phase": phase,
            "turn_owner": turn_owner_name,
            "countdown": None,
            "rules_reminder": rules_reminder,
            "win_condition": win_condition,
            "activity_log": activity_log,
        }},
        "footer": {{
            "actions": footer_actions,
            "navigation": navigation,
        }},
    }}


def handle_callback(session, user_id, action):
    """Process a player action and return updated session."""
    state = session["state"]

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

    # Board placement (if board enabled)
    if BOARD_ENABLED and action.startswith("place:"):
        try:
            _, coords = action.split(":", 1)
            row, col = map(int, coords.split(","))
        except (ValueError, IndexError):
            return session
        if not (0 <= row < BOARD_ROWS and 0 <= col < BOARD_COLS):
            return session
        if state["board"][row][col] != EMPTY_CELL:
            return session
        color = PLAYER_COLORS[role_idx % len(PLAYER_COLORS)]
        state["board"][row][col] = color
        state["moves"].append(f"{{color}} placed at ({{row}},{{col}})")
        state["scores"][str(user_id)] = state["scores"].get(str(user_id), 0) + 1
        state["turn_count"] += 1
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

    # Score action
    if action.startswith("score:"):
        try:
            points = int(action.split(":", 1)[1])
        except (ValueError, IndexError):
            points = 1
        state["scores"][str(user_id)] = state["scores"].get(str(user_id), 0) + points
        state["moves"].append(f"Player {{user_id}} scored {{points}} point(s)")
        state["turn_count"] += 1
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
{self._indent(button_handler_code, 4)}

    # Default: score 1 point
    state["scores"][str(user_id)] = state["scores"].get(str(user_id), 0) + 1
    state["turn_count"] += 1
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
    state = session["state"]
    scores = state.get("scores", {{}})

    if WIN_TYPE == "score_threshold":
        for uid_str, score in scores.items():
            if score >= TARGET_SCORE:
                return {{"type": "win", "winner": int(uid_str)}}
        return None

    elif WIN_TYPE == "highest_score":
        if state.get("turn_count", 0) < MAX_TURNS:
            return None
        if not scores:
            return {{"type": "draw"}}
        max_score = max(scores.values())
        winners = [int(uid) for uid, s in scores.items() if s == max_score]
        if len(winners) == 1:
            return {{"type": "win", "winner": winners[0]}}
        return {{"type": "draw"}}

    elif WIN_TYPE == "board_full":
        if not BOARD_ENABLED:
            return None
        board = state.get("board", [])
        if all(board[r][c] != EMPTY_CELL for r in range(BOARD_ROWS) for c in range(BOARD_COLS)):
            max_score = max(scores.values()) if scores else 0
            winners = [int(uid) for uid, s in scores.items() if s == max_score]
            if len(winners) == 1:
                return {{"type": "win", "winner": winners[0]}}
            return {{"type": "draw"}}
        return None

    elif WIN_TYPE == "majority_control":
        if not BOARD_ENABLED:
            return None
        total_cells = BOARD_ROWS * BOARD_COLS
        threshold = total_cells // 2 + 1
        for uid_str, score in scores.items():
            if score >= threshold:
                return {{"type": "win", "winner": int(uid_str)}}
        return None

    # Default: highest score after max turns
    if state.get("turn_count", 0) < MAX_TURNS:
        return None
    if not scores:
        return {{"type": "draw"}}
    max_score = max(scores.values())
    winners = [int(uid) for uid, s in scores.items() if s == max_score]
    if len(winners) == 1:
        return {{"type": "win", "winner": winners[0]}}
    return {{"type": "draw"}}


def serialize_state(session):
    """Serialize the game state for persistent storage."""
    return session.get("state", {{}})


def deserialize_state(data):
    """Deserialize game state from persistent storage."""
    if isinstance(data, str):
        data = json.loads(data)
    return data
'''
        return code

    # ------------------------------------------------------------------
    # Helper: Button definitions code
    # ------------------------------------------------------------------

    def _build_button_definitions(self, buttons: List[Dict[str, Any]]) -> str:
        """Build Python constant definitions for buttons."""
        if not buttons:
            return "BUTTONS = []"

        lines = ["BUTTONS = ["]
        for btn in buttons:
            if isinstance(btn, dict):
                lines.append(f"    {repr(btn)},")
            else:
                lines.append(f"    {repr(btn)},")
        lines.append("]")
        return "\n".join(lines)

    def _build_button_handler_code(self, buttons: List[Dict[str, Any]], game_type: str) -> str:
        """Build the button handler dispatch code for handle_callback."""
        if not buttons:
            return "# No custom buttons defined\npass"

        lines = []
        for btn in buttons:
            if not isinstance(btn, dict):
                continue
            action_id = btn.get("action_id", "")
            effect_type = btn.get("effect_type", "CUSTOM")
            label = btn.get("label", "???")

            if not action_id:
                continue

            if effect_type == "MOVE":
                lines.append(f"if action == {repr(action_id)}:")
                lines.append(f"    state['moves'].append({repr(f'{label}')})")
                lines.append(f"    num_players = len(session['players'])")
                lines.append(f"    session['current_turn_index'] = (session['current_turn_index'] + 1) % num_players")
                lines.append(f"    return session")
            elif effect_type == "SCORE":
                lines.append(f"if action == {repr(action_id)}:")
                lines.append(f"    state['scores'][str(user_id)] = state['scores'].get(str(user_id), 0) + 1")
                lines.append(f"    state['moves'].append(f'Player {{user_id}} used {label}')")
                lines.append(f"    state['turn_count'] = state.get('turn_count', 0) + 1")
                lines.append(f"    result = check_win(session)")
                lines.append(f"    if result is not None:")
                lines.append(f"        if isinstance(result, dict) and result.get('type') == 'win':")
                lines.append(f"            state['winner'] = result['winner']")
                lines.append(f"            session['current_phase'] = 'finished'")
                lines.append(f"        elif isinstance(result, dict) and result.get('type') == 'draw':")
                lines.append(f"            state['winner'] = 'draw'")
                lines.append(f"            session['current_phase'] = 'finished'")
                lines.append(f"    else:")
                lines.append(f"        num_players = len(session['players'])")
                lines.append(f"        session['current_turn_index'] = (session['current_turn_index'] + 1) % num_players")
                lines.append(f"    return session")
            elif effect_type == "ATTACK":
                lines.append(f"if action == {repr(action_id)}:")
                lines.append(f"    # Attack: find next alive opponent")
                lines.append(f"    for p in session['players']:")
                lines.append(f"        if p['user_id'] != user_id:")
                lines.append(f"            hp_key = str(p['user_id'])")
                lines.append(f"            if state.get('health', {{}}).get(hp_key, 0) > 0:")
                lines.append(f"                state['health'][hp_key] = max(0, state['health'].get(hp_key, 0) - 1)")
                lines.append(f"                state['moves'].append(f'⚔️ Player {{user_id}} attacked {{p[\"name\"]}}')")
                lines.append(f"                break")
                lines.append(f"    return session")
            elif effect_type == "DEFEND":
                lines.append(f"if action == {repr(action_id)}:")
                lines.append(f"    state['moves'].append(f'🛡 Player {{user_id}} defended')")
                lines.append(f"    num_players = len(session['players'])")
                lines.append(f"    session['current_turn_index'] = (session['current_turn_index'] + 1) % num_players")
                lines.append(f"    return session")
            elif effect_type == "HEAL":
                lines.append(f"if action == {repr(action_id)}:")
                lines.append(f"    hp_key = str(user_id)")
                lines.append(f"    current = state.get('health', {{}}).get(hp_key, 0)")
                lines.append(f"    state.setdefault('health', {{}})[hp_key] = current + 1")
                lines.append(f"    state['moves'].append(f'💊 Player {{user_id}} healed')")
                lines.append(f"    num_players = len(session['players'])")
                lines.append(f"    session['current_turn_index'] = (session['current_turn_index'] + 1) % num_players")
                lines.append(f"    return session")
            elif effect_type == "REVEAL":
                lines.append(f"if action == {repr(action_id)}:")
                lines.append(f"    pdata = state.get('player_roles', {{}}).get(str(user_id), {{}})")
                lines.append(f"    pdata['revealed'] = True")
                lines.append(f"    state['moves'].append(f'🔍 Player {{user_id}} revealed their role')")
                lines.append(f"    return session")
            elif effect_type == "VOTE":
                lines.append(f"if action.startswith({repr(action_id + ':')}):")
                lines.append(f"    try:")
                lines.append(f"        target_id = int(action.split(':', 1)[1])")
                lines.append(f"    except (ValueError, IndexError):")
                lines.append(f"        return session")
                lines.append(f"    state.setdefault('votes', {{}})[str(user_id)] = target_id")
                lines.append(f"    state['moves'].append(f'🗳 Player {{user_id}} voted for {{target_id}}')")
                lines.append(f"    return session")
            elif effect_type == "PLACE":
                lines.append(f"if action == {repr(action_id)}:")
                lines.append(f"    # Place on board - handled by cell_actions")
                lines.append(f"    return session")
            elif effect_type == "SKIP":
                lines.append(f"if action == {repr(action_id)}:")
                lines.append(f"    state['moves'].append(f'⏩ Player {{user_id}} skipped')")
                lines.append(f"    num_players = len(session['players'])")
                lines.append(f"    session['current_turn_index'] = (session['current_turn_index'] + 1) % num_players")
                lines.append(f"    return session")
            else:
                # CUSTOM or unknown
                lines.append(f"if action == {repr(action_id)}:")
                lines.append(f"    state['scores'][str(user_id)] = state['scores'].get(str(user_id), 0) + 1")
                lines.append(f"    state['moves'].append(f'Player {{user_id}} used {label}')")
                lines.append(f"    state['turn_count'] = state.get('turn_count', 0) + 1")
                lines.append(f"    result = check_win(session)")
                lines.append(f"    if result is not None:")
                lines.append(f"        if isinstance(result, dict) and result.get('type') == 'win':")
                lines.append(f"            state['winner'] = result['winner']")
                lines.append(f"            session['current_phase'] = 'finished'")
                lines.append(f"        elif isinstance(result, dict) and result.get('type') == 'draw':")
                lines.append(f"            state['winner'] = 'draw'")
                lines.append(f"            session['current_phase'] = 'finished'")
                lines.append(f"    else:")
                lines.append(f"        num_players = len(session['players'])")
                lines.append(f"        session['current_turn_index'] = (session['current_turn_index'] + 1) % num_players")
                lines.append(f"    return session")

        if not lines:
            return "# No custom buttons with valid action_ids\npass"

        return "\n".join(lines)

    def _build_footer_buttons_code(self, buttons: List[Dict[str, Any]]) -> str:
        """Build code that adds custom buttons to footer actions."""
        if not buttons:
            return "# No custom buttons"

        lines = ["# Custom buttons"]
        for btn in buttons:
            if isinstance(btn, dict) and btn.get("action_id") and btn.get("label"):
                lines.append(
                    f'footer_actions.append({{"label": {repr(btn["label"])}, '
                    f'"callback": {repr(btn["action_id"])}, "visible": True}})'
                )
        return "\n".join(lines)

    @staticmethod
    def _indent(code: str, spaces: int) -> str:
        """Indent each line of code by the given number of spaces."""
        indent = " " * spaces
        return "\n".join(indent + line if line.strip() else line for line in code.split("\n"))

    # ------------------------------------------------------------------
    # Plugin directory & file writing
    # ------------------------------------------------------------------

    def create_plugin_directory(self, slug: str) -> Path:
        """Create the plugin directory under /games/<slug>/."""
        games_dir = config.GAMES_DIR
        plugin_dir = games_dir / slug
        plugin_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created plugin directory: %s", plugin_dir)
        return plugin_dir

    def write_manifest(self, slug: str, manifest: Dict[str, Any]) -> None:
        """Write manifest.json to the plugin directory."""
        manifest_path = config.GAMES_DIR / slug / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=4, ensure_ascii=False)
        logger.info("Wrote manifest: %s", manifest_path)

    def write_logic(self, slug: str, logic_code: str) -> None:
        """Write logic.py to the plugin directory."""
        logic_path = config.GAMES_DIR / slug / "logic.py"
        with open(logic_path, "w", encoding="utf-8") as fh:
            fh.write(logic_code)
        logger.info("Wrote logic: %s", logic_path)

    # ------------------------------------------------------------------
    # Database registration
    # ------------------------------------------------------------------

    def register_in_db(self, slug: str, config: Dict[str, Any], user_id: int,
                       manifest: Dict[str, Any]) -> int:
        """Register the game in the games table. Returns the game ID."""
        name = config.get("game_name", "").strip()
        creator = config.get("creator_name", "").strip()
        description = config.get("description", "").strip()
        gt = config.get("game_type", "button_logic")
        board_enabled = config.get("board_enabled", False) or gt in self.BOARD_REQUIRED_TYPES

        try:
            min_p = int(config.get("min_players", 2))
        except (ValueError, TypeError):
            min_p = 2
        try:
            max_p = int(config.get("max_players", 2))
        except (ValueError, TypeError):
            max_p = 2

        try:
            rows = int(config.get("board_rows", 0))
        except (ValueError, TypeError):
            rows = 0
        try:
            cols = int(config.get("board_cols", 0))
        except (ValueError, TypeError):
            cols = 0

        reward = 0.0
        fee = 0.0
        try:
            reward = float(config.get("reward_per_win", 0))
        except (ValueError, TypeError):
            pass
        try:
            fee = float(config.get("entry_fee", 0))
        except (ValueError, TypeError):
            pass

        win_condition = config.get("win_type", "")
        manifest_json = json.dumps(manifest, ensure_ascii=False)

        cursor = db.execute(
            """INSERT INTO games
               (slug, name, creator, description, version, game_type,
                min_players, max_players, board_rows, board_cols,
                turn_based, single_message_only, win_condition,
                reward_sar, entry_fee_sar, is_approved, is_active, manifest_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (slug, name, creator, description, "1.0.0", gt,
             min_p, max_p, rows if board_enabled else None, cols if board_enabled else None,
             1, 1, win_condition,
             reward, fee, 0, 1, manifest_json),
        )
        game_id = cursor.lastrowid
        logger.info("Registered game '%s' (id=%d) in database.", slug, game_id)
        return game_id

    def assign_ownership(self, user_id: int, slug: str, creator_name: str) -> None:
        """Create a game_ownership record."""
        db.execute(
            """INSERT OR IGNORE INTO game_ownership
               (owner_user_id, game_slug, creator_name, rights_status)
               VALUES (?, ?, ?, ?)""",
            (user_id, slug, creator_name, "owned"),
        )
        logger.info("Assigned ownership of '%s' to user %d.", slug, user_id)

    # ------------------------------------------------------------------
    # Hot reload
    # ------------------------------------------------------------------

    def hot_reload(self, slug: str) -> bool:
        """
        Trigger the plugin loader to reload this game.

        Attempts to import the global PluginLoader instance and call
        reload_game() or discover_all().
        """
        try:
            from game_bot.engine.plugin_loader import PluginLoader

            # Try to find a global loader instance
            # The typical pattern is that the bot holds a reference
            import game_bot.bot as bot_module
            loader = getattr(bot_module, "_plugin_loader", None)
            if loader is None:
                # Check if there's a module-level instance
                loader = getattr(bot_module, "plugin_loader", None)

            if loader is not None and isinstance(loader, PluginLoader):
                # If the game is already loaded, reload it
                existing = loader.get_game(slug)
                if existing is not None:
                    result = loader.reload_game(slug)
                    if result is not None:
                        logger.info("Hot-reloaded game '%s'.", slug)
                        return True
                else:
                    # Game not yet loaded — do a full discovery
                    loader.discover_all()
                    if loader.get_game(slug) is not None:
                        logger.info("Discovered new game '%s'.", slug)
                        return True

            logger.warning("Could not find PluginLoader instance for hot reload. Game will be loaded on next restart.")
            return False

        except ImportError as exc:
            logger.warning("Cannot import plugin_loader for hot reload: %s", exc)
            return False
        except Exception as exc:
            logger.warning("Hot reload failed: %s", exc)
            return False
