"""
Live In-Message Game UI Rendering Engine

Renders structured game state into beautiful Telegram messages using
Unicode box-drawing characters, emoji indicators, and InlineKeyboardMarkup.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# ---------------------------------------------------------------------------
# Unicode box-drawing constants
# ---------------------------------------------------------------------------
BOX = {
    # Double-line box (header / footer)
    "dtl": "╔", "dtr": "╗", "dbl": "╚", "dbr": "╝",
    "dh": "═", "dv": "║",
    "dtj": "╦", "dbj": "╩", "dlj": "╠", "drj": "╣", "dx": "╬",
    # Single-line box (internal sections)
    "stl": "┌", "str": "┐", "sbl": "└", "sbr": "┘",
    "sh": "─", "sv": "│",
    "stj": "┬", "sbj": "┴", "slj": "├", "srj": "┤", "sx": "┼",
}

# ---------------------------------------------------------------------------
# Emoji helpers
# ---------------------------------------------------------------------------
EMOJI = {
    "turn": "▶️",
    "coin": "🪙",
    "trophy": "🏆",
    "skull": "💀",
    "eye": "👁",
    "lock": "🔒",
    "unlock": "🔓",
    "clock": "⏳",
    "star": "⭐",
    "check": "✅",
    "cross": "❌",
    "fire": "🔥",
    "crown": "👑",
    "shield": "🛡",
    "sword": "⚔️",
    "dice": "🎲",
    "cards": "🃏",
    "party": "🎉",
    "warning": "⚠️",
    "info": "ℹ️",
    "play": "▶️",
    "pause": "⏸",
    "stop": "⏹",
    "refresh": "🔄",
    "rocket": "🚀",
    "boom": "💥",
    "heart": "❤️",
    "diamond": "💎",
    "clover": "🍀",
    "spade": "♠️",
    "target": "🎯",
    "megaphone": "📢",
    "scroll": "📜",
    "key": "🔑",
    "ghost": "👻",
    "robot": "🤖",
    "person": "🧑",
    "vs": "⚔️",
    "online": "🟢",
    "offline": "🔴",
    "waiting": "🟡",
}

BADGE_EMOJI = {
    "gold": "🥇",
    "silver": "🥈",
    "bronze": "🥉",
    "vip": "💎",
    "admin": "👑",
    "newbie": "🌱",
    "veteran": "🎖",
    "champion": "🏆",
    "legend": "🌟",
}

ROLE_DISPLAY = {
    "mafia": "🔫 Mafia",
    "detective": "🔍 Detective",
    "doctor": "💊 Doctor",
    "villager": "🏠 Villager",
    "werewolf": "🐺 Werewolf",
    "seer": "🔮 Seer",
    "hunter": "🏹 Hunter",
    "hidden": "❓ Hidden",
}

STATUS_DISPLAY = {
    "waiting": f"{EMOJI['waiting']} Waiting",
    "active": f"{EMOJI['online']} Active",
    "completed": f"{EMOJI['check']} Completed",
    "cancelled": f"{EMOJI['cross']} Cancelled",
    "paused": f"{EMOJI['pause']} Paused",
}

VISIBILITY_DISPLAY = {
    "public": f"{EMOJI['unlock']} Public",
    "private": f"{EMOJI['lock']} Private",
    "friends": "👥 Friends Only",
}


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _center(text: str, width: int, fill: str = " ") -> str:
    """Center *text* within *width* characters using *fill*."""
    if len(text) >= width:
        return text[:width]
    left = (width - len(text)) // 2
    right = width - len(text) - left
    return fill * left + text + fill * right


def _pad(text: str, width: int, align: str = "left") -> str:
    """Pad *text* to *width*, stripping newlines."""
    text = text.replace("\n", " ")
    if len(text) >= width:
        return text[:width]
    space = width - len(text)
    if align == "center":
        return _center(text, width)
    elif align == "right":
        return " " * space + text
    return text + " " * space


def _badge_display(badge: str) -> str:
    """Return emoji representation for a profile badge."""
    return BADGE_EMOJI.get(badge, f"[{badge}]" if badge else "")


def _role_display(role: str, reveal: bool = True) -> str:
    """Return styled role string.  If not *reveal*, show hidden."""
    if not reveal:
        return ROLE_DISPLAY.get("hidden", "❓ ???")
    return ROLE_DISPLAY.get(role, role)


def _color_dot(color: str) -> str:
    """Return a colored circle emoji for the given color name."""
    mapping = {
        "red": "🔴", "blue": "🔵", "green": "🟢", "yellow": "🟡",
        "purple": "🟣", "orange": "🟠", "white": "⚪", "black": "⚫",
        "pink": "🩷", "cyan": "🩵",
    }
    return mapping.get(color, "⬜")


# ---------------------------------------------------------------------------
# GameRenderer
# ---------------------------------------------------------------------------

class GameRenderer:
    """
    Renders a full in-message game UI from a structured context dict.

    Returns ``(message_text, InlineKeyboardMarkup)`` suitable for
    ``bot.send_message`` or ``bot.edit_message_text``.
    """

    # Default cell width (characters) inside a grid
    CELL_WIDTH = 3

    def __init__(self, cell_width: int = 3):
        self.cell_width = cell_width

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(self, context: dict) -> tuple[str, InlineKeyboardMarkup]:
        """
        Build the complete game message and inline keyboard.

        Parameters
        ----------
        context : dict
            Full render context (see module docstring for schema).

        Returns
        -------
        tuple[str, InlineKeyboardMarkup]
            (formatted_message_text, reply_markup)
        """
        sections: list[str] = []

        # 1) Header
        sections.append(self._render_header(context.get("header", {})))

        # 2) Player HUD
        sections.append(self._render_players(context.get("players", [])))

        # 3) Board / Game area
        board_ctx = context.get("board")
        if board_ctx and board_ctx.get("cells"):
            sections.append(self._render_board(board_ctx))

        # 4) Action / State section
        sections.append(self._render_state(context.get("state", {})))

        # 5) Footer
        sections.append(self._render_footer(context.get("footer", {})))

        # Join with blank lines
        message = "\n".join(s for s in sections if s)

        # Build inline keyboard
        keyboard = self._build_keyboard(context)

        return message, InlineKeyboardMarkup(keyboard)

    # ------------------------------------------------------------------
    # Section 1 – Header
    # ------------------------------------------------------------------

    def _render_header(self, header: dict) -> str:
        game_name = header.get("game_name", "Unknown Game")
        room_id = header.get("room_id", "—")
        mode = header.get("mode", "classic")
        visibility = header.get("visibility", "public")
        status = header.get("status", "waiting")

        vis_str = VISIBILITY_DISPLAY.get(visibility, visibility)
        status_str = STATUS_DISPLAY.get(status, status)

        inner_width = 30
        top = f"{BOX['dtl']}{BOX['dh'] * inner_width}{BOX['dtr']}"
        bot = f"{BOX['dbl']}{BOX['dh'] * inner_width}{BOX['dbr']}"

        lines = [
            top,
            f"{BOX['dv']}{_center(f'🎮 {game_name}', inner_width)}{BOX['dv']}",
            f"{BOX['dlj']}{BOX['dh'] * inner_width}{BOX['drj']}",
            f"{BOX['dv']}{_pad(f'🏷 Room: {room_id}', inner_width)}{BOX['dv']}",
            f"{BOX['dv']}{_pad(f'🎲 Mode: {mode}', inner_width)}{BOX['dv']}",
            f"{BOX['dv']}{_pad(f'{vis_str}  •  {status_str}', inner_width)}{BOX['dv']}",
            bot,
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Section 2 – Player HUD
    # ------------------------------------------------------------------

    def _render_players(self, players: list[dict]) -> str:
        if not players:
            return f"{EMOJI['person']} No players yet"

        lines: list[str] = []
        inner_width = 30

        top = f"{BOX['stl']}{BOX['sh'] * inner_width}{BOX['str']}"
        bot = f"{BOX['sbl']}{BOX['sh'] * inner_width}{BOX['sbr']}"
        mid = f"{BOX['slj']}{BOX['sh'] * inner_width}{BOX['srj']}"

        lines.append(top)
        lines.append(f"{BOX['sv']}{_center('👥 Players', inner_width)}{BOX['sv']}")

        for i, p in enumerate(players):
            if i > 0:
                lines.append(mid)

            name = p.get("name", "???")
            badge = p.get("badge", "")
            balance = p.get("balance", 0)
            wins = p.get("wins", 0)
            role = p.get("role", "")
            is_turn = p.get("is_turn", False)
            is_alive = p.get("is_alive", True)
            score = p.get("score", 0)
            color = p.get("color", "")

            # Turn indicator
            turn_marker = f"{EMOJI['turn']} " if is_turn else "  "

            # Alive / dead
            alive_marker = "" if is_alive else f" {EMOJI['skull']}"

            # Color dot
            color_marker = _color_dot(color) + " " if color else ""

            # Badge
            badge_str = _badge_display(badge)
            badge_part = f" {badge_str}" if badge_str else ""

            # Name line
            name_line = f"{turn_marker}{color_marker}{name}{badge_part}{alive_marker}"
            lines.append(f"{BOX['sv']}{_pad(name_line, inner_width)}{BOX['sv']}")

            # Stats line
            parts = []
            parts.append(f"{EMOJI['coin']}{balance:.0f}")
            if wins:
                parts.append(f"{EMOJI['trophy']}{wins}")
            if score:
                parts.append(f"🎯{score}")
            if role:
                # Only reveal role if alive and not hidden; game logic controls
                parts.append(_role_display(role, reveal=is_alive))

            stat_line = "  ".join(parts)
            lines.append(f"{BOX['sv']}{_pad(stat_line, inner_width)}{BOX['sv']}")

        lines.append(bot)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Section 3 – Board / Game Area
    # ------------------------------------------------------------------

    def _render_board(self, board: dict) -> str:
        rows = board.get("rows", 0)
        cols = board.get("cols", 0)
        cells = board.get("cells", [])
        hidden = board.get("hidden", [])

        if rows == 0 or cols == 0:
            return ""

        cw = self.cell_width  # cell width in chars

        # Build row separator lines
        top_sep = BOX["dtl"] + (BOX["dh"] * cw + BOX["dtj"]) * (cols - 1) + (BOX["dh"] * cw) + BOX["dtr"]
        mid_sep = BOX["dlj"] + (BOX["dh"] * cw + BOX["dx"]) * (cols - 1) + (BOX["dh"] * cw) + BOX["drj"]
        bot_sep = BOX["dbl"] + (BOX["dh"] * cw + BOX["dbj"]) * (cols - 1) + (BOX["dh"] * cw) + BOX["dbr"]

        lines: list[str] = []
        lines.append(top_sep)

        for r in range(rows):
            row_cells: list[str] = []
            for c in range(cols):
                # Check if cell is hidden
                is_hidden = False
                if hidden and r < len(hidden) and c < len(hidden[r]):
                    is_hidden = hidden[r][c]

                if is_hidden:
                    display = _center("❓", cw)
                else:
                    val = ""
                    if cells and r < len(cells) and c < len(cells[r]):
                        val = str(cells[r][c]) if cells[r][c] is not None else ""
                    display = _center(val, cw)
                row_cells.append(display)

            line = BOX["dv"] + BOX["dv"].join(row_cells) + BOX["dv"]
            lines.append(line)

            if r < rows - 1:
                lines.append(mid_sep)
            else:
                lines.append(bot_sep)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Section 4 – Action / State
    # ------------------------------------------------------------------

    def _render_state(self, state: dict) -> str:
        phase = state.get("phase", "")
        turn_owner = state.get("turn_owner", "")
        countdown = state.get("countdown")
        rules_reminder = state.get("rules_reminder", "")
        win_condition = state.get("win_condition", "")
        activity_log = state.get("activity_log", [])

        inner_width = 30
        lines: list[str] = []

        top = f"{BOX['stl']}{BOX['sh'] * inner_width}{BOX['str']}"
        bot = f"{BOX['sbl']}{BOX['sh'] * inner_width}{BOX['sbr']}"
        mid = f"{BOX['slj']}{BOX['sh'] * inner_width}{BOX['srj']}"

        lines.append(top)

        # Phase
        if phase:
            phase_label = EMOJI['megaphone'] + " Phase: " + phase
            lines.append(
                f"{BOX['sv']}{_pad(phase_label, inner_width)}{BOX['sv']}"
            )

        # Turn owner
        if turn_owner:
            turn_label = EMOJI['turn'] + " Turn: " + turn_owner
            lines.append(
                f"{BOX['sv']}{_pad(turn_label, inner_width)}{BOX['sv']}"
            )

        # Countdown
        if countdown is not None:
            cd_bar = self._countdown_bar(countdown, width=20)
            cd_label = EMOJI['clock'] + " " + cd_bar + " " + str(countdown) + "s"
            lines.append(
                f"{BOX['sv']}{_pad(cd_label, inner_width)}{BOX['sv']}"
            )

        # Rules reminder
        if rules_reminder:
            lines.append(mid)
            wrapped = self._wrap_text(rules_reminder, inner_width - 2)
            for wline in wrapped:
                lines.append(f"{BOX['sv']} {_pad(wline, inner_width - 1)}{BOX['sv']}")

        # Win condition
        if win_condition:
            lines.append(mid)
            win_label = EMOJI['crown'] + " " + win_condition
            lines.append(
                f"{BOX['sv']}{_pad(win_label, inner_width)}{BOX['sv']}"
            )

        # Activity log
        if activity_log:
            lines.append(mid)
            lines.append(
                f"{BOX['sv']}{_center('📜 Log', inner_width)}{BOX['sv']}"
            )
            # Show last 5 entries
            for entry in activity_log[-5:]:
                wrapped = self._wrap_text(entry, inner_width - 2)
                for wline in wrapped:
                    lines.append(f"{BOX['sv']} {_pad(wline, inner_width - 1)}{BOX['sv']}")

        lines.append(bot)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Section 5 – Footer
    # ------------------------------------------------------------------

    def _render_footer(self, footer: dict) -> str:
        actions = footer.get("actions", [])
        navigation = footer.get("navigation", [])

        if not actions and not navigation:
            return ""

        inner_width = 30
        lines: list[str] = []

        top = f"{BOX['dtl']}{BOX['dh'] * inner_width}{BOX['dtr']}"
        bot = f"{BOX['dbl']}{BOX['dh'] * inner_width}{BOX['dbr']}"
        mid = f"{BOX['dlj']}{BOX['dh'] * inner_width}{BOX['drj']}"

        lines.append(top)

        # Visible actions
        visible_actions = [a for a in actions if a.get("visible", True)]
        if visible_actions:
            lines.append(
                f"{BOX['dv']}{_center('🎯 Actions', inner_width)}{BOX['dv']}"
            )
            for action in visible_actions:
                label = action.get("label", "???")
                lines.append(
                    f"{BOX['dv']}{_pad(f'  • {label}', inner_width)}{BOX['dv']}"
                )

        # Navigation
        if navigation:
            if visible_actions:
                lines.append(mid)
            lines.append(
                f"{BOX['dv']}{_center('🧭 Navigation', inner_width)}{BOX['dv']}"
            )
            nav_labels = "  ".join(n.get("label", "—") for n in navigation)
            # Truncate if too long
            if len(nav_labels) > inner_width:
                nav_labels = nav_labels[:inner_width - 1] + "…"
            lines.append(
                f"{BOX['dv']}{_pad(f'  {nav_labels}', inner_width)}{BOX['dv']}"
            )

        lines.append(bot)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Inline Keyboard Builder
    # ------------------------------------------------------------------

    def _build_keyboard(self, context: dict) -> list[list[InlineKeyboardButton]]:
        """
        Build the InlineKeyboardMarkup rows.

        Layout:
        1. Board cell buttons (if interactive)
        2. Action buttons
        3. Navigation buttons
        """
        rows: list[list[InlineKeyboardButton]] = []

        # --- Board buttons ---
        board_ctx = context.get("board")
        if board_ctx and board_ctx.get("cell_actions"):
            board_rows = self._build_board_buttons(board_ctx)
            rows.extend(board_rows)

        # --- Action buttons ---
        footer = context.get("footer", {})
        actions = footer.get("actions", [])
        visible_actions = [a for a in actions if a.get("visible", True)]
        if visible_actions:
            action_rows = self._chunk_buttons(
                [
                    InlineKeyboardButton(
                        a.get("label", "???"),
                        callback_data=a.get("callback", "noop"),
                    )
                    for a in visible_actions
                ],
                max_per_row=8,
            )
            rows.extend(action_rows)

        # --- Navigation buttons ---
        navigation = footer.get("navigation", [])
        if navigation:
            nav_row = [
                InlineKeyboardButton(
                    n.get("label", "—"),
                    callback_data=n.get("callback", "noop"),
                )
                for n in navigation
            ]
            rows.extend(self._chunk_buttons(nav_row, max_per_row=8))

        return rows

    def _build_board_buttons(
        self, board: dict
    ) -> list[list[InlineKeyboardButton]]:
        """
        Build one row of buttons per board row.

        Each cell that has ``cell_actions`` entry becomes a button.
        Hidden cells still get a button (with ❓ label).
        """
        rows = board.get("rows", 0)
        cols = board.get("cols", 0)
        cells = board.get("cells", [])
        cell_actions = board.get("cell_actions", [])
        hidden = board.get("hidden", [])

        button_rows: list[list[InlineKeyboardButton]] = []

        # Telegram allows max 8 buttons per row.
        # If cols > 8 we split into sub-rows of 8.
        chunk_size = min(cols, 8)

        for r in range(rows):
            full_row: list[InlineKeyboardButton] = []
            for c in range(cols):
                is_hidden = False
                if hidden and r < len(hidden) and c < len(hidden[r]):
                    is_hidden = hidden[r][c]

                # Determine label
                if is_hidden:
                    label = "❓"
                elif cells and r < len(cells) and c < len(cells[r]):
                    val = cells[r][c]
                    label = str(val) if val is not None else "·"
                else:
                    label = "·"

                # Determine callback data
                cb = None
                if cell_actions and r < len(cell_actions) and c < len(cell_actions[r]):
                    cb = cell_actions[r][c]

                # Only add button if there is callback data (interactive cell)
                if cb is not None:
                    full_row.append(
                        InlineKeyboardButton(label, callback_data=str(cb))
                    )
                else:
                    # Non-interactive placeholder — Telegram doesn't support
                    # disabled buttons, so we add a no-op styled button.
                    full_row.append(
                        InlineKeyboardButton(label, callback_data=f"cell:{r}:{c}")
                    )

            # Split into chunks of chunk_size
            for chunk_start in range(0, len(full_row), chunk_size):
                chunk = full_row[chunk_start : chunk_start + chunk_size]
                if chunk:
                    button_rows.append(chunk)

        return button_rows

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _chunk_buttons(
        buttons: list[InlineKeyboardButton], max_per_row: int = 8
    ) -> list[list[InlineKeyboardButton]]:
        """Split a flat list of buttons into rows of at most *max_per_row*."""
        rows: list[list[InlineKeyboardButton]] = []
        for i in range(0, len(buttons), max_per_row):
            rows.append(buttons[i : i + max_per_row])
        return rows

    @staticmethod
    def _countdown_bar(seconds: int, width: int = 20) -> str:
        """Return a textual progress bar for a countdown."""
        if seconds <= 0:
            return "░" * width
        filled = max(1, int(width * min(seconds, 60) / 60))
        return "█" * filled + "░" * (width - filled)

    @staticmethod
    def _wrap_text(text: str, width: int) -> list[str]:
        """Word-wrap *text* to *width* characters per line."""
        if not text:
            return []
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            if current and len(current) + 1 + len(word) > width:
                lines.append(current)
                current = word
            else:
                current = current + " " + word if current else word
        if current:
            lines.append(current)
        return lines


# ---------------------------------------------------------------------------
# Convenience: render a context with defaults
# ---------------------------------------------------------------------------

def quick_render(context: dict, cell_width: int = 3) -> tuple[str, InlineKeyboardMarkup]:
    """One-shot render shortcut."""
    return GameRenderer(cell_width=cell_width).render(context)
