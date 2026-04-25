"""
Builder Renderer

Renders the Game Builder dashboard as a single live Telegram message
that gets updated via editMessageText. Each builder step has its own
render method producing structured Unicode text + InlineKeyboardMarkup.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .steps import (
    HOME,
    GAME_INFO,
    GAME_TYPE,
    PLAYER_CONFIG,
    BUTTON_DESIGN,
    BOARD_DESIGN,
    WIN_LOGIC,
    ECONOMY_SETUP,
    PREVIEW,
    VALIDATION,
    PUBLISH,
    STATE_ORDER,
    GAME_TYPES,
    EFFECT_TYPES,
    WIN_TYPES,
)
from .validator import BuilderValidator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Step display configuration
# ---------------------------------------------------------------------------

STEP_DISPLAY = {
    HOME: {"icon": "\U0001f3e0", "label": "Home", "desc": "Dashboard & navigation"},
    GAME_INFO: {"icon": "\u2139\ufe0f", "label": "Game Info", "desc": "Game name and description"},
    GAME_TYPE: {"icon": "\U0001f3ae", "label": "Game Type", "desc": "Choose game template"},
    PLAYER_CONFIG: {"icon": "\U0001f465", "label": "Players", "desc": "Player settings"},
    BUTTON_DESIGN: {"icon": "\U0001f518", "label": "Buttons", "desc": "Custom buttons"},
    BOARD_DESIGN: {"icon": "\U0001f9f1", "label": "Board", "desc": "Grid layout"},
    WIN_LOGIC: {"icon": "\U0001f9e0", "label": "Win Logic", "desc": "Win conditions"},
    ECONOMY_SETUP: {"icon": "\U0001f4b0", "label": "Economy", "desc": "Rewards and fees"},
    PREVIEW: {"icon": "\U0001f441", "label": "Preview", "desc": "Live preview"},
    VALIDATION: {"icon": "\u2705", "label": "Validation", "desc": "Check readiness"},
    PUBLISH: {"icon": "\U0001f680", "label": "Publish", "desc": "Release your game"},
}

# Cell type emoji for board mini-preview
CELL_TYPE_EMOJI = {
    "normal": "\u2b1c",
    "hidden": "\u2753",
    "trap": "\u2620\ufe0f",
    "reward": "\U0001f4b0",
    "blocked": "\U0001f6ab",
    "teleport": "\U0001f300",
    "reveal": "\U0001f441",
    "start": "\U0001f7e2",
    "finish": "\U0001f7e1",
}

MAX_CB_DATA = 64  # Telegram callback_data max length


def _compute_step_status(validation: dict) -> dict:
    """Compute step status from validation check results."""
    # Map check keys to builder step names
    CHECK_TO_STEP = {
        "game_name": "GAME_INFO",
        "description": "GAME_INFO",
        "creator_name": "GAME_INFO",
        "slug_generation": "GAME_INFO",
        "game_type": "GAME_TYPE",
        "player_counts": "PLAYER_CONFIG",
        "buttons_exist": "BUTTON_DESIGN",
        "button_count": "BUTTON_DESIGN",
        "button_fields": "BUTTON_DESIGN",
        "board_config": "BOARD_DESIGN",
        "board_cells": "BOARD_DESIGN",
        "win_logic_type": "WIN_LOGIC",
        "win_logic_fields": "WIN_LOGIC",
        "economy": "ECONOMY_SETUP",
        "exploit_conditions": "ECONOMY_SETUP",
        "manifest_compilation": "VALIDATION",
        "logic_generation": "VALIDATION",
        "ui_renderer_compat": "VALIDATION",
    }
    step_status = {}
    checks = validation.get("checks", {})
    for key, check in checks.items():
        step = CHECK_TO_STEP.get(key, "")
        if step:
            current = step_status.get(step)
            if current is None or (current == "complete" and not check.get("passed")):
                step_status[step] = "complete" if check.get("passed") else "incomplete"
    return step_status


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _progress_bar(percentage: int, width: int = 10) -> str:
    """Build a visual progress bar string."""
    filled = int(width * percentage / 100)
    empty = width - filled
    return "\u2588" * filled + "\u2591" * empty


def _step_indicator(status: str) -> str:
    """Return an emoji indicator for step status."""
    if status == "complete":
        return "\u2705"
    elif status == "partial":
        return "\U0001f504"
    else:
        return "\u2b1c"


def _truncate(text: str, max_len: int = 30) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "\u2026"


def _safe_cb(text: str) -> str:
    """Ensure callback data is within Telegram's 64-byte limit."""
    if len(text) > MAX_CB_DATA:
        return text[:MAX_CB_DATA]
    return text


class BuilderRenderer:
    """
    Renders the builder dashboard UI for Telegram.

    Each builder step gets its own render method that produces
    ``(message_text, InlineKeyboardMarkup)``.
    """

    def __init__(self):
        self.validator = BuilderValidator()

    def render(self, session_data: dict) -> tuple[str, InlineKeyboardMarkup]:
        """
        Render the current builder state.

        Parameters
        ----------
        session_data : dict
            Full session data including ``current_step`` and ``config``.

        Returns
        -------
        tuple[str, InlineKeyboardMarkup]
            The formatted message and inline keyboard.
        """
        step = session_data.get("current_step", HOME)
        config = session_data.get("config", {})

        renderers = {
            HOME: self._render_home,
            GAME_INFO: self._render_game_info,
            GAME_TYPE: self._render_game_type,
            PLAYER_CONFIG: self._render_player_config,
            BUTTON_DESIGN: self._render_button_design,
            BOARD_DESIGN: self._render_board_design,
            WIN_LOGIC: self._render_win_logic,
            ECONOMY_SETUP: self._render_economy,
            PREVIEW: self._render_preview,
            VALIDATION: self._render_validation,
            PUBLISH: self._render_publish,
        }

        renderer = renderers.get(step, self._render_home)
        return renderer(session_data)

    # ------------------------------------------------------------------
    # HOME
    # ------------------------------------------------------------------

    def _render_home(self, session_data: dict) -> tuple[str, InlineKeyboardMarkup]:
        config = session_data.get("config", {})
        progress = self._calculate_progress(config)

        game_name = config.get("game_name", "") or "Not Set"
        game_type = config.get("game_type", "")
        type_label = GAME_TYPES.get(game_type, {}).get("name", "Not Selected") if game_type else "Not Selected"

        # Step status
        step_status = _compute_step_status(self.validator.validate(config))

        lines = [
            "\U0001f9e0 GAME CREATOR STUDIO",
            "",
            f"Game: {game_name}",
            f"Template: {type_label}",
            f"Progress: [{_progress_bar(progress)}] {progress}%",
            "",
            f"Current Step: HOME",
            "",
            "Steps:",
        ]

        for step_name in STATE_ORDER:
            if step_name == HOME:
                continue
            display = STEP_DISPLAY.get(step_name, {"label": step_name, "desc": ""})
            status = step_status.get(step_name, "incomplete")
            indicator = _step_indicator(status)
            lines.append(f"{indicator} {step_name} - {display['desc']}")

        lines.append("")
        lines.append("Quick Actions:")

        text = "\n".join(lines)

        # Keyboard
        keyboard = [
            [
                InlineKeyboardButton("\u2795 Game Info", callback_data=_safe_cb("builder:goto:GAME_INFO")),
                InlineKeyboardButton("\U0001f3ae Game Type", callback_data=_safe_cb("builder:goto:GAME_TYPE")),
                InlineKeyboardButton("\U0001f465 Players", callback_data=_safe_cb("builder:goto:PLAYER_CONFIG")),
            ],
            [
                InlineKeyboardButton("\U0001f518 Buttons", callback_data=_safe_cb("builder:goto:BUTTON_DESIGN")),
                InlineKeyboardButton("\U0001f9f1 Board", callback_data=_safe_cb("builder:goto:BOARD_DESIGN")),
                InlineKeyboardButton("\U0001f9e0 Win Logic", callback_data=_safe_cb("builder:goto:WIN_LOGIC")),
            ],
            [
                InlineKeyboardButton("\U0001f4b0 Economy", callback_data=_safe_cb("builder:goto:ECONOMY_SETUP")),
                InlineKeyboardButton("\U0001f441 Preview", callback_data=_safe_cb("builder:goto:PREVIEW")),
                InlineKeyboardButton("\U0001f4e6 Save", callback_data=_safe_cb("builder:save")),
            ],
            [
                InlineKeyboardButton("\U0001f680 Publish", callback_data=_safe_cb("builder:goto:PUBLISH")),
                InlineKeyboardButton("\u274c Cancel", callback_data=_safe_cb("builder:cancel")),
            ],
        ]

        return text, InlineKeyboardMarkup(keyboard)

    # ------------------------------------------------------------------
    # GAME_INFO
    # ------------------------------------------------------------------

    def _render_game_info(self, session_data: dict) -> tuple[str, InlineKeyboardMarkup]:
        config = session_data.get("config", {})
        progress = self._calculate_progress(config)

        lines = [
            "\u2139\ufe0f GAME INFO",
            f"[{_progress_bar(progress)}] {progress}%",
            "",
            f"\U0001f3ae Name: {config.get('game_name', '') or '(not set)'}",
            f"\U0001f4dd Description: {_truncate(config.get('description', '') or '(not set)', 40)}",
            f"\U0001f464 Creator: {config.get('creator_name', '') or '(not set)'}",
            f"\U0001f513 Visibility: {config.get('visibility', 'public')}",
            f"\U0001f3f7 Tags: {', '.join(config.get('tags', [])) or '(none)'}",
            f"\U0001f4dc Summary: {_truncate(config.get('summary', '') or '(not set)', 40)}",
            "",
            "Edit fields by tapping below:",
        ]

        text = "\n".join(lines)

        keyboard = [
            [
                InlineKeyboardButton("\u270f\ufe0f Name", callback_data=_safe_cb("builder:set:game_name:prompt")),
                InlineKeyboardButton("\U0001f4dd Desc", callback_data=_safe_cb("builder:set:description:prompt")),
            ],
            [
                InlineKeyboardButton("\U0001f464 Creator", callback_data=_safe_cb("builder:set:creator_name:prompt")),
                InlineKeyboardButton("\U0001f513 Visible", callback_data=_safe_cb("builder:set:visibility:toggle")),
            ],
            [
                InlineKeyboardButton("\U0001f3f7 Tags", callback_data=_safe_cb("builder:set:tags:prompt")),
                InlineKeyboardButton("\U0001f4dc Summary", callback_data=_safe_cb("builder:set:summary:prompt")),
            ],
            self._nav_row(GAME_INFO),
        ]

        return text, InlineKeyboardMarkup(keyboard)

    # ------------------------------------------------------------------
    # GAME_TYPE
    # ------------------------------------------------------------------

    def _render_game_type(self, session_data: dict) -> tuple[str, InlineKeyboardMarkup]:
        config = session_data.get("config", {})
        progress = self._calculate_progress(config)
        current_type = config.get("game_type", "")

        lines = [
            "\U0001f3ae GAME TYPE",
            f"[{_progress_bar(progress)}] {progress}%",
            "",
        ]

        for type_key, type_info in GAME_TYPES.items():
            marker = "\u25b6\ufe0f " if type_key == current_type else "  "
            selected = " \u2705" if type_key == current_type else ""
            lines.append(
                f"{marker}{type_info['icon']} {type_info['name']}{selected}"
            )
            lines.append(f"    {type_info['description']}")
            if type_info.get("board_required"):
                lines.append("    \U0001f9f1 Board required")

        lines.append("")
        if current_type:
            lines.append(f"Selected: {GAME_TYPES[current_type]['icon']} {GAME_TYPES[current_type]['name']}")
        else:
            lines.append("No type selected yet")

        text = "\n".join(lines)

        rows = []
        type_keys = list(GAME_TYPES.keys())
        for i in range(0, len(type_keys), 2):
            row = []
            for j in range(2):
                if i + j < len(type_keys):
                    tk = type_keys[i + j]
                    ti = GAME_TYPES[tk]
                    marker = "\u25b6 " if tk == current_type else ""
                    row.append(InlineKeyboardButton(
                        f"{marker}{ti['icon']} {ti['name']}",
                        callback_data=_safe_cb(f"builder:game_type:{tk}"),
                    ))
            rows.append(row)

        rows.append(self._nav_row(GAME_TYPE))

        return text, InlineKeyboardMarkup(rows)

    # ------------------------------------------------------------------
    # PLAYER_CONFIG
    # ------------------------------------------------------------------

    def _render_player_config(self, session_data: dict) -> tuple[str, InlineKeyboardMarkup]:
        config = session_data.get("config", {})
        progress = self._calculate_progress(config)

        lines = [
            "\U0001f465 PLAYER CONFIG",
            f"[{_progress_bar(progress)}] {progress}%",
            "",
            f"\U0001f465 Min Players: {config.get('min_players', 2)}",
            f"\U0001f465 Max Players: {config.get('max_players', 4)}",
            f"\U0001f517 Join Rules: {config.get('join_rules', 'open')}",
            f"\U0001f4b0 Entry Fee: {config.get('entry_fee', 0)} SAR",
            f"\U0001f441 Spectator Mode: {'Yes' if config.get('spectator_mode') else 'No'}",
            f"\U0001f512 Private Rooms: {'Yes' if config.get('private_room_support') else 'No'}",
        ]

        text = "\n".join(lines)

        keyboard = [
            [
                InlineKeyboardButton("Min -", callback_data=_safe_cb("builder:set:min_players:dec")),
                InlineKeyboardButton(f"Min: {config.get('min_players', 2)}", callback_data=_safe_cb("builder:set:min_players:show")),
                InlineKeyboardButton("Min +", callback_data=_safe_cb("builder:set:min_players:inc")),
            ],
            [
                InlineKeyboardButton("Max -", callback_data=_safe_cb("builder:set:max_players:dec")),
                InlineKeyboardButton(f"Max: {config.get('max_players', 4)}", callback_data=_safe_cb("builder:set:max_players:show")),
                InlineKeyboardButton("Max +", callback_data=_safe_cb("builder:set:max_players:inc")),
            ],
            [
                InlineKeyboardButton(f"Join: {config.get('join_rules', 'open')}", callback_data=_safe_cb("builder:set:join_rules:toggle")),
                InlineKeyboardButton(f"Fee: {config.get('entry_fee', 0)}", callback_data=_safe_cb("builder:set:entry_fee:prompt")),
            ],
            [
                InlineKeyboardButton(f"Spectator: {'On' if config.get('spectator_mode') else 'Off'}", callback_data=_safe_cb("builder:set:spectator_mode:toggle")),
                InlineKeyboardButton(f"Private: {'On' if config.get('private_room_support') else 'Off'}", callback_data=_safe_cb("builder:set:private_room_support:toggle")),
            ],
            self._nav_row(PLAYER_CONFIG),
        ]

        return text, InlineKeyboardMarkup(keyboard)

    # ------------------------------------------------------------------
    # BUTTON_DESIGN
    # ------------------------------------------------------------------

    def _render_button_design(self, session_data: dict) -> tuple[str, InlineKeyboardMarkup]:
        config = session_data.get("config", {})
        progress = self._calculate_progress(config)
        buttons = config.get("buttons", [])

        lines = [
            "\U0001f518 BUTTON DESIGN",
            f"[{_progress_bar(progress)}] {progress}%",
            "",
            f"Buttons: {len(buttons)} defined",
            "",
        ]

        if not buttons:
            lines.append("No buttons yet. Tap 'Add Button' to start.")
        else:
            for i, btn in enumerate(buttons):
                emoji = btn.get("emoji", "") or "\U0001f518"
                label = btn.get("label", "") or "(no label)"
                effect = btn.get("effect_type", "") or "(no effect)"
                cooldown = btn.get("cooldown", 0)
                visibility = btn.get("visibility_rule", "always")
                lines.append(f"  {i + 1}. {emoji} {label}")
                lines.append(f"     Effect: {effect} | CD: {cooldown}s | Vis: {visibility}")

        # Live button row preview
        lines.append("")
        lines.append("\u2500\u2500 Button Row Preview \u2500\u2500")
        if buttons:
            preview_parts = []
            for btn in buttons[:8]:
                emoji = btn.get("emoji", "") or "\U0001f518"
                label = btn.get("label", "") or "?"
                preview_parts.append(f"[{emoji}{_truncate(label, 6)}]")
            lines.append(" ".join(preview_parts))
        else:
            lines.append("(no buttons to preview)")

        text = "\n".join(lines)

        keyboard = [
            [InlineKeyboardButton("\u2795 Add Button", callback_data=_safe_cb("builder:button:add"))],
        ]

        # Per-button edit/delete (max 8 buttons shown)
        for i, btn in enumerate(buttons[:8]):
            label = btn.get("label", f"Btn {i + 1}")
            emoji = btn.get("emoji", "")
            keyboard.append([
                InlineKeyboardButton(
                    f"\u270f\ufe0f {emoji}{_truncate(label, 10)}",
                    callback_data=_safe_cb(f"builder:button:edit:{i}"),
                ),
                InlineKeyboardButton(
                    f"\U0001f5d1 Delete",
                    callback_data=_safe_cb(f"builder:button:delete:{i}"),
                ),
            ])

        keyboard.append(self._nav_row(BUTTON_DESIGN))

        return text, InlineKeyboardMarkup(keyboard)

    # ------------------------------------------------------------------
    # BOARD_DESIGN
    # ------------------------------------------------------------------

    def _render_board_design(self, session_data: dict) -> tuple[str, InlineKeyboardMarkup]:
        config = session_data.get("config", {})
        progress = self._calculate_progress(config)
        board = config.get("board", {})
        game_type = config.get("game_type", "")

        rows = board.get("rows", 3)
        cols = board.get("cols", 3)
        enabled = board.get("enabled", True)
        density = board.get("density", "normal")

        lines = [
            "\U0001f9f1 BOARD DESIGN",
            f"[{_progress_bar(progress)}] {progress}%",
            "",
            f"Board: {'Enabled' if enabled else 'Disabled'}",
            f"Size: {rows}x{cols} ({rows * cols} cells)",
            f"Density: {density}",
        ]

        # Count special cells
        special_counts = {
            "hidden": len(board.get("hidden_cells", [])),
            "trap": len(board.get("trap_cells", [])),
            "reward": len(board.get("reward_cells", [])),
            "blocked": len(board.get("blocked_cells", [])),
            "teleport": len(board.get("teleport_cells", [])),
            "reveal": len(board.get("reveal_cells", [])),
        }
        lines.append("")
        lines.append("Special Cells:")
        for ctype, count in special_counts.items():
            emoji = CELL_TYPE_EMOJI.get(ctype, "\u2b1c")
            lines.append(f"  {emoji} {ctype.title()}: {count}")

        # Mini-grid preview
        lines.append("")
        lines.append("\u2500\u2500 Board Preview \u2500\u2500")
        mini_grid = self._render_mini_grid(board)
        lines.extend(mini_grid)

        text = "\n".join(lines)

        keyboard = [
            [
                InlineKeyboardButton("Rows -", callback_data=_safe_cb("builder:board:rows:dec")),
                InlineKeyboardButton(f"R: {rows}", callback_data=_safe_cb("builder:board:rows:show")),
                InlineKeyboardButton("Rows +", callback_data=_safe_cb("builder:board:rows:inc")),
            ],
            [
                InlineKeyboardButton("Cols -", callback_data=_safe_cb("builder:board:cols:dec")),
                InlineKeyboardButton(f"C: {cols}", callback_data=_safe_cb("builder:board:cols:show")),
                InlineKeyboardButton("Cols +", callback_data=_safe_cb("builder:board:cols:inc")),
            ],
            [
                InlineKeyboardButton(
                    f"Board: {'On' if enabled else 'Off'}",
                    callback_data=_safe_cb("builder:set:board_enabled:toggle"),
                ),
                InlineKeyboardButton(
                    f"Density: {density}",
                    callback_data=_safe_cb("builder:set:density:toggle"),
                ),
            ],
            self._nav_row(BOARD_DESIGN),
        ]

        return text, InlineKeyboardMarkup(keyboard)

    def _render_mini_grid(self, board: dict) -> list[str]:
        """Render a small text representation of the board grid."""
        rows = board.get("rows", 3)
        cols = board.get("cols", 3)

        if rows == 0 or cols == 0:
            return ["(no board)"]

        # Build special cell lookup
        special_map: dict[tuple[int, int], str] = {}
        for cell in board.get("hidden_cells", []):
            special_map[(cell.get("row", 0), cell.get("col", 0))] = "hidden"
        for cell in board.get("trap_cells", []):
            special_map[(cell.get("row", 0), cell.get("col", 0))] = "trap"
        for cell in board.get("reward_cells", []):
            special_map[(cell.get("row", 0), cell.get("col", 0))] = "reward"
        for cell in board.get("blocked_cells", []):
            special_map[(cell.get("row", 0), cell.get("col", 0))] = "blocked"
        for cell in board.get("teleport_cells", []):
            special_map[(cell.get("row", 0), cell.get("col", 0))] = "teleport"
        for cell in board.get("reveal_cells", []):
            special_map[(cell.get("row", 0), cell.get("col", 0))] = "reveal"

        # Limit preview size
        max_preview_rows = min(rows, 10)
        max_preview_cols = min(cols, 8)

        lines: list[str] = []
        for r in range(max_preview_rows):
            row_parts = []
            for c in range(max_preview_cols):
                cell_type = special_map.get((r, c), "normal")
                row_parts.append(CELL_TYPE_EMOJI.get(cell_type, "\u2b1c"))
            lines.append("".join(row_parts))

        if rows > max_preview_rows or cols > max_preview_cols:
            lines.append(f"... ({rows}x{cols} total)")

        return lines

    # ------------------------------------------------------------------
    # WIN_LOGIC
    # ------------------------------------------------------------------

    def _render_win_logic(self, session_data: dict) -> tuple[str, InlineKeyboardMarkup]:
        config = session_data.get("config", {})
        progress = self._calculate_progress(config)
        win_logic = config.get("win_logic", {})
        win_type = win_logic.get("type", "")

        lines = [
            "\U0001f9e0 WIN LOGIC",
            f"[{_progress_bar(progress)}] {progress}%",
            "",
        ]

        for wt, wdesc in WIN_TYPES.items():
            marker = "\u25b6\ufe0f " if wt == win_type else "  "
            selected = " \u2705" if wt == win_type else ""
            lines.append(f"{marker}{wdesc}{selected}")

        lines.append("")
        if win_type:
            lines.append(f"Selected: {WIN_TYPES.get(win_type, win_type)}")
        else:
            lines.append("No win condition selected")

        # Type-specific details
        if win_type == "target_score":
            lines.append(f"\U0001f3af Target Score: {win_logic.get('target_score', 0)}")
        elif win_type == "elimination":
            rules = win_logic.get("elimination_rules", "")
            lines.append(f"\U0001f4a5 Rules: {rules or '(not set)'}")
        elif win_type == "path_completion":
            rules = win_logic.get("path_completion_rules", "")
            lines.append(f"\U0001f6e4\ufe0f Rules: {rules or '(not set)'}")
        elif win_type == "custom":
            rules = win_logic.get("custom_rules", "")
            lines.append(f"\u2728 Custom Rules: {_truncate(rules or '(not set)', 40)}")

        text = "\n".join(lines)

        rows = []
        win_keys = list(WIN_TYPES.keys())
        for i in range(0, len(win_keys), 2):
            row = []
            for j in range(2):
                if i + j < len(win_keys):
                    wk = win_keys[i + j]
                    wd = WIN_TYPES[wk]
                    marker = "\u25b6 " if wk == win_type else ""
                    row.append(InlineKeyboardButton(
                        f"{marker}{_truncate(wd, 18)}",
                        callback_data=_safe_cb(f"builder:win_type:{wk}"),
                    ))
            rows.append(row)

        # Type-specific config buttons
        if win_type == "target_score":
            rows.append([
                InlineKeyboardButton("Score -", callback_data=_safe_cb("builder:set:target_score:dec")),
                InlineKeyboardButton(f"Target: {win_logic.get('target_score', 0)}", callback_data=_safe_cb("builder:set:target_score:show")),
                InlineKeyboardButton("Score +", callback_data=_safe_cb("builder:set:target_score:inc")),
            ])
        elif win_type in ("elimination", "path_completion", "custom"):
            field_map = {
                "elimination": "elimination_rules",
                "path_completion": "path_completion_rules",
                "custom": "custom_rules",
            }
            field = field_map.get(win_type, "")
            if field:
                rows.append([
                    InlineKeyboardButton(f"\u270f\ufe0f Edit Rules", callback_data=_safe_cb(f"builder:set:{field}:prompt")),
                ])

        rows.append(self._nav_row(WIN_LOGIC))

        return text, InlineKeyboardMarkup(rows)

    # ------------------------------------------------------------------
    # ECONOMY_SETUP
    # ------------------------------------------------------------------

    def _render_economy(self, session_data: dict) -> tuple[str, InlineKeyboardMarkup]:
        config = session_data.get("config", {})
        progress = self._calculate_progress(config)
        economy = config.get("economy", {})

        reward = economy.get("reward_per_win", 2)
        fee = economy.get("entry_fee", 0)
        participation = economy.get("participation_reward", 0)
        bonus = economy.get("bonus_reward", 0)
        anti_abuse = economy.get("anti_abuse", True)
        free_access = economy.get("free_access", True)

        lines = [
            "\U0001f4b0 ECONOMY SETUP",
            f"[{_progress_bar(progress)}] {progress}%",
            "",
            f"\U0001f3c6 Win Reward: {reward} SAR",
            f"\U0001f4b3 Entry Fee: {fee} SAR",
            f"\U0001f91d Participation: {participation} SAR",
            f"\U0001f31f Bonus Reward: {bonus} SAR",
            f"\U0001f6e1 Anti-Abuse: {'On' if anti_abuse else 'Off'}",
            f"\U0001f513 Free Access: {'On' if free_access else 'Off'}",
            "",
        ]

        # Net calculation
        if fee > 0 and reward > 0:
            net = reward - fee
            lines.append(f"\U0001f4ca Player Net (win): {net:+.1f} SAR")

        text = "\n".join(lines)

        keyboard = [
            [
                InlineKeyboardButton("Reward -", callback_data=_safe_cb("builder:econ:reward_per_win:dec")),
                InlineKeyboardButton(f"\U0001f3c6 {reward}", callback_data=_safe_cb("builder:econ:reward_per_win:show")),
                InlineKeyboardButton("Reward +", callback_data=_safe_cb("builder:econ:reward_per_win:inc")),
            ],
            [
                InlineKeyboardButton("Fee -", callback_data=_safe_cb("builder:econ:entry_fee:dec")),
                InlineKeyboardButton(f"\U0001f4b3 {fee}", callback_data=_safe_cb("builder:econ:entry_fee:show")),
                InlineKeyboardButton("Fee +", callback_data=_safe_cb("builder:econ:entry_fee:inc")),
            ],
            [
                InlineKeyboardButton("Part. -", callback_data=_safe_cb("builder:econ:participation_reward:dec")),
                InlineKeyboardButton(f"\U0001f91d {participation}", callback_data=_safe_cb("builder:econ:participation_reward:show")),
                InlineKeyboardButton("Part. +", callback_data=_safe_cb("builder:econ:participation_reward:inc")),
            ],
            [
                InlineKeyboardButton("Bonus -", callback_data=_safe_cb("builder:econ:bonus_reward:dec")),
                InlineKeyboardButton(f"\U0001f31f {bonus}", callback_data=_safe_cb("builder:econ:bonus_reward:show")),
                InlineKeyboardButton("Bonus +", callback_data=_safe_cb("builder:econ:bonus_reward:inc")),
            ],
            [
                InlineKeyboardButton(
                    f"Anti-Abuse: {'On' if anti_abuse else 'Off'}",
                    callback_data=_safe_cb("builder:econ:anti_abuse:toggle"),
                ),
                InlineKeyboardButton(
                    f"Free Access: {'On' if free_access else 'Off'}",
                    callback_data=_safe_cb("builder:econ:free_access:toggle"),
                ),
            ],
            self._nav_row(ECONOMY_SETUP),
        ]

        return text, InlineKeyboardMarkup(keyboard)

    # ------------------------------------------------------------------
    # PREVIEW
    # ------------------------------------------------------------------

    def _render_preview(self, session_data: dict) -> tuple[str, InlineKeyboardMarkup]:
        config = session_data.get("config", {})
        progress = self._calculate_progress(config)

        lines = [
            "\U0001f441 GAME PREVIEW",
            f"[{_progress_bar(progress)}] {progress}%",
            "",
            "\u2500\u2500 Simulated Game View \u2500\u2500",
            "",
        ]

        # Simulate header
        game_name = config.get("game_name", "") or "My Game"
        lines.append(f"\U0001f3ae {game_name}")
        lines.append(f"\U0001f3ae Room: PREVIEW-001 | Mode: classic")
        lines.append("")

        # Simulate player HUD
        min_p = config.get("min_players", 2)
        num_preview = min(min_p, 4)
        for i in range(num_preview):
            turn_marker = "\u25b6\ufe0f " if i == 0 else "  "
            score = 0
            lines.append(f"{turn_marker}Player {i + 1}  \U0001f3c6{score}")
        lines.append("")

        # Simulate board
        board = config.get("board", {})
        if board.get("enabled", True):
            rows = board.get("rows", 3)
            cols = board.get("cols", 3)
            for r in range(min(rows, 5)):
                row_str = "|".join([" \u00b7 " for _ in range(min(cols, 8))])
                lines.append(f"|{row_str}|")
            if rows > 5 or cols > 8:
                lines.append(f"... ({rows}x{cols} board)")
        else:
            lines.append("(no board)")

        lines.append("")

        # Simulate buttons
        buttons = config.get("buttons", [])
        if buttons:
            btn_preview = " ".join(
                f"[{b.get('emoji', '')}{_truncate(b.get('label', '?'), 5)}]"
                for b in buttons[:8]
            )
            lines.append(f"Actions: {btn_preview}")
        else:
            lines.append("Actions: (no buttons)")

        lines.append("")
        lines.append(f"\U0001f9e0 Win: {WIN_TYPES.get(config.get('win_logic', {}).get('type', ''), 'Not set')}")
        lines.append(f"\U0001f4b0 Fee: {config.get('economy', {}).get('entry_fee', 0)} SAR | Reward: {config.get('economy', {}).get('reward_per_win', 2)} SAR")

        text = "\n".join(lines)

        keyboard = [
            [
                InlineKeyboardButton("\U0001f504 Refresh", callback_data=_safe_cb("builder:goto:PREVIEW")),
                InlineKeyboardButton("\u2705 Validate", callback_data=_safe_cb("builder:goto:VALIDATION")),
            ],
            self._nav_row(PREVIEW),
        ]

        return text, InlineKeyboardMarkup(keyboard)

    # ------------------------------------------------------------------
    # VALIDATION
    # ------------------------------------------------------------------

    def _render_validation(self, session_data: dict) -> tuple[str, InlineKeyboardMarkup]:
        config = session_data.get("config", {})
        progress = self._calculate_progress(config)
        validation = self.validator.validate(config)

        lines = [
            "\u2705 VALIDATION CHECK",
            f"[{_progress_bar(progress)}] {progress}%",
            "",
        ]

        if validation["valid"]:
            lines.append("\U0001f389 All checks passed! Ready to publish.")
        else:
            error_count = len(validation["errors"])
            lines.append(f"\u26a0\ufe0f {error_count} issue(s) found:")

        lines.append("")

        # Per-step checklist
        step_status = _compute_step_status(validation)
        for step_name in STATE_ORDER:
            if step_name in (HOME, PREVIEW, VALIDATION, PUBLISH):
                continue
            display = STEP_DISPLAY.get(step_name, {"label": step_name, "desc": ""})
            status = step_status.get(step_name, "incomplete")
            indicator = _step_indicator(status)
            lines.append(f"  {indicator} {display['label']}")

        # Show errors with suggestions
        if validation["errors"]:
            lines.append("")
            lines.append("\u2500\u2500 Issues & Fixes \u2500\u2500")
            for err in validation["errors"][:10]:
                if isinstance(err, dict):
                    lines.append(f"  \u274c {err.get('field', '')}: {err.get('message', str(err))}")
                    if err.get("suggestion"):
                        lines.append(f"     \U0001f4a1 {err['suggestion']}")
                else:
                    lines.append(f"  \u274c {err}")

        text = "\n".join(lines)

        keyboard = []
        if validation["valid"]:
            keyboard.append([
                InlineKeyboardButton("\U0001f680 Publish Now!", callback_data=_safe_cb("builder:goto:PUBLISH")),
            ])
        else:
            keyboard.append([
                InlineKeyboardButton("\U0001f504 Re-validate", callback_data=_safe_cb("builder:goto:VALIDATION")),
            ])

        keyboard.append(self._nav_row(VALIDATION))

        return text, InlineKeyboardMarkup(keyboard)

    # ------------------------------------------------------------------
    # PUBLISH
    # ------------------------------------------------------------------

    def _render_publish(self, session_data: dict) -> tuple[str, InlineKeyboardMarkup]:
        config = session_data.get("config", {})
        progress = self._calculate_progress(config)
        validation = self.validator.validate(config)

        lines = [
            "\U0001f680 PUBLISH GAME",
            f"[{_progress_bar(progress)}] {progress}%",
            "",
            "\u2500\u2500 Final Summary \u2500\u2500",
            "",
            f"\U0001f3ae Name: {config.get('game_name', '') or 'Not Set'}",
            f"\U0001f464 Creator: {config.get('creator_name', '') or 'Not Set'}",
            f"\U0001f4dd Description: {_truncate(config.get('description', '') or 'Not Set', 40)}",
        ]

        game_type = config.get("game_type", "")
        type_info = GAME_TYPES.get(game_type, {})
        lines.append(f"\U0001f3ae Type: {type_info.get('icon', '')} {type_info.get('name', game_type or 'Not Set')}")

        lines.append(f"\U0001f465 Players: {config.get('min_players', 2)}-{config.get('max_players', 4)}")

        buttons = config.get("buttons", [])
        lines.append(f"\U0001f518 Buttons: {len(buttons)}")

        board = config.get("board", {})
        if board.get("enabled", True):
            lines.append(f"\U0001f9f1 Board: {board.get('rows', 3)}x{board.get('cols', 3)}")
        else:
            lines.append("\U0001f9f1 Board: Disabled")

        win_logic = config.get("win_logic", {})
        win_type = win_logic.get("type", "")
        lines.append(f"\U0001f9e0 Win: {WIN_TYPES.get(win_type, 'Not Set')}")

        economy = config.get("economy", {})
        lines.append(f"\U0001f4b0 Entry: {economy.get('entry_fee', 0)} SAR | Reward: {economy.get('reward_per_win', 2)} SAR")

        lines.append("")

        if validation["valid"]:
            lines.append("\u2705 All validation checks passed!")
            lines.append("")
            lines.append("Tap 'Publish' to create your game!")
        else:
            lines.append("\u274c Validation issues detected:")
            for err in validation["errors"][:5]:
                if isinstance(err, dict):
                    lines.append(f"  \u274c {err.get('field', '')}: {err.get('message', str(err))}")
                else:
                    lines.append(f"  \u274c {err}")
            lines.append("")
            lines.append("Fix the issues above before publishing.")

        text = "\n".join(lines)

        keyboard = []
        if validation["valid"]:
            keyboard.append([
                InlineKeyboardButton("\U0001f680 PUBLISH GAME", callback_data=_safe_cb("builder:publish")),
            ])
        else:
            keyboard.append([
                InlineKeyboardButton("\u2705 Go to Validation", callback_data=_safe_cb("builder:goto:VALIDATION")),
            ])

        keyboard.append(self._nav_row(PUBLISH))

        return text, InlineKeyboardMarkup(keyboard)

    # ------------------------------------------------------------------
    # Navigation helper
    # ------------------------------------------------------------------

    @staticmethod
    def _nav_row(current_step: str) -> list[InlineKeyboardButton]:
        """Build a standard navigation row for a step."""
        buttons = [
            InlineKeyboardButton("\u2b05\ufe0f Back", callback_data=_safe_cb("builder:prev")),
            InlineKeyboardButton("\U0001f3e0 Home", callback_data=_safe_cb("builder:goto:HOME")),
            InlineKeyboardButton("\U0001f4e6 Save", callback_data=_safe_cb("builder:save")),
            InlineKeyboardButton("\u27a1\ufe0f Next", callback_data=_safe_cb("builder:next")),
        ]
        return buttons

    # ------------------------------------------------------------------
    # Progress calculation
    # ------------------------------------------------------------------

    def _calculate_progress(self, config: dict) -> int:
        """Calculate completion percentage (0-100) based on config."""
        total_fields = 0
        filled_fields = 0

        # Game Info fields
        for field in ("game_name", "description", "creator_name"):
            total_fields += 1
            if config.get(field, "").strip():
                filled_fields += 1

        # Visibility (has default)
        total_fields += 1
        if config.get("visibility", ""):
            filled_fields += 1

        # Game Type
        total_fields += 1
        if config.get("game_type", ""):
            filled_fields += 1

        # Player Config
        total_fields += 2
        if isinstance(config.get("min_players"), int) and config["min_players"] >= 1:
            filled_fields += 1
        if isinstance(config.get("max_players"), int) and config["max_players"] >= 1:
            filled_fields += 1

        # Buttons
        total_fields += 1
        if config.get("buttons"):
            filled_fields += 1

        # Board
        board = config.get("board", {})
        total_fields += 1
        if board.get("rows", 0) > 0 and board.get("cols", 0) > 0:
            filled_fields += 1

        # Win Logic
        total_fields += 1
        if config.get("win_logic", {}).get("type", ""):
            filled_fields += 1

        # Economy
        total_fields += 1
        economy = config.get("economy", {})
        if economy.get("reward_per_win", 0) > 0 or economy.get("free_access", True):
            filled_fields += 1

        if total_fields == 0:
            return 0

        return min(100, int(100 * filled_fields / total_fields))
