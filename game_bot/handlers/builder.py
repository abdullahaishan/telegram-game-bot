"""
Game Builder Handler

Provides a step-by-step, callback-driven Game Builder UI inside Telegram.
All interactions edit a single live message.  The builder lets users
create, configure, save drafts, and publish games.

Handler functions:
  - builder_entry_handler   : entry point – license check & session start
  - builder_callback_handler: main callback router for all builder:* actions
  - builder_text_handler    : text-input handler for free-text fields
  - builder_drafts_handler  : list saved drafts with load/delete buttons
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import CURRENCY_NAME, GAMES_DIR
from database import async_execute, async_fetchone, async_fetchall, async_transaction

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Builder Engine – manages builder sessions & game configuration
# ═══════════════════════════════════════════════════════════════════════

BUILDER_STEPS = [
    "name",
    "game_type",
    "board",
    "buttons",
    "win_type",
    "economy",
    "review",
]

STEP_TITLES = {
    "name":      "📝 Name & Description",
    "game_type": "🎮 Game Type",
    "board":     "🔲 Board Configuration",
    "buttons":   "🔘 Button Configuration",
    "win_type":  "🏆 Win Condition",
    "economy":   f"💰 Economy ({CURRENCY_NAME})",
    "review":    "📋 Review & Publish",
}

GAME_TYPES = {
    "grid_strategy": {"name": "Grid Strategy", "icon": "♟️", "board_required": True, "description": "Strategic grid-based games like chess, XO"},
    "turn_based": {"name": "Turn Based", "icon": "🔄", "board_required": False, "description": "Classic turn-based games"},
    "button_logic": {"name": "Button Logic", "icon": "🔘", "board_required": False, "description": "Logic and decision games using buttons"},
    "hidden_role": {"name": "Hidden Role", "icon": "🎭", "board_required": False, "description": "Secret role and deduction games"},
    "path_building": {"name": "Path Building", "icon": "⛴️", "board_required": True, "description": "Build paths and routes to win"},
    "elimination": {"name": "Elimination", "icon": "💥", "board_required": False, "description": "Last player standing wins"},
    "custom": {"name": "Custom Template", "icon": "✨", "board_required": True, "description": "Fully customizable game"},
}

WIN_TYPES = {
    "target_score": {"name": "Target Score", "label": "Target Score", "description": "First to reach target score"},
    "elimination": {"name": "Elimination", "label": "Elimination", "description": "Last player standing"},
    "path_completion": {"name": "Path Completion", "label": "Path Completion", "description": "Complete a path first"},
    "last_survivor": {"name": "Last Survivor", "label": "Last Survivor", "description": "Survive until end"},
    "highest_score": {"name": "Highest Score", "label": "Highest Score", "description": "Highest score when game ends"},
    "first_to_target": {"name": "First to Target", "label": "First to Target", "description": "First to achieve target condition"},
    "line_match": {"name": "Line Match", "label": "Line Match", "description": "First to place N in a row/column/diagonal wins"},
    "board_full": {"name": "Board Full", "label": "Board Full", "description": "Game ends when board is full; best placement wins"},
    "majority_control": {"name": "Majority Control", "label": "Majority Control", "description": "Control more than half the board cells to win"},
    "role_reveal": {"name": "Role Reveal", "label": "Role Reveal", "description": "Identify or eliminate specific hidden roles"},
    "custom": {"name": "Custom Win Rules", "label": "Custom Win Rules", "description": "Custom win rules"},
}

# Fields that accept free-text input
TEXT_FIELDS = {"name", "description", "slug"}


class BuilderEngine:
    """
    Manages a single game-builder session: configuration state,
    step navigation, draft saving, and game publishing.
    """

    def __init__(self, user_id: int, user_name: str = ""):
        self.session_id: str = uuid.uuid4().hex[:10]
        self.user_id: int = user_id
        self.user_name: str = user_name or str(user_id)
        self.current_step: int = 0
        self.created_at: str = datetime.utcnow().isoformat()
        self.draft_id: Optional[int] = None

        # Game configuration defaults
        self.config: dict[str, Any] = {
            "name": "",
            "description": "",
            "slug": "",
            "game_type": "board",
            "min_players": 2,
            "max_players": 10,
            "board_rows": 3,
            "board_cols": 3,
            "cells": [],          # list of list of dicts
            "buttons": [],        # list of dicts {label, callback, style}
            "win_type": "last_standing",
            "win_condition_desc": "",
            "entry_fee": 0.0,
            "win_reward": 0.0,
            "turn_based": True,
        }
        self._init_cells()

    # ── Cell helpers ──────────────────────────────────────────────

    def _init_cells(self) -> None:
        """Rebuild the cells grid from board dimensions."""
        rows = self.config["board_rows"]
        cols = self.config["board_cols"]
        self.config["cells"] = [
            [{"label": "·", "action": f"cell:{r}:{c}"} for c in range(cols)]
            for r in range(rows)
        ]

    # ── Step navigation ───────────────────────────────────────────

    def step_name(self) -> str:
        return BUILDER_STEPS[self.current_step]

    def next_step(self) -> bool:
        if self.current_step < len(BUILDER_STEPS) - 1:
            self.current_step += 1
            return True
        return False

    def prev_step(self) -> bool:
        if self.current_step > 0:
            self.current_step -= 1
            return True
        return False

    def goto_step(self, step: str) -> bool:
        if step in BUILDER_STEPS:
            self.current_step = BUILDER_STEPS.index(step)
            return True
        return False

    # ── Config mutation ───────────────────────────────────────────

    def set_field(self, field: str, value: Any) -> None:
        """Set a top-level config field."""
        if field in self.config:
            self.config[field] = value

        # Auto-regenerate slug from name
        if field == "name" and not self.config["slug"]:
            slug_value = value if isinstance(value, str) else str(value)
            slug = re.sub(r"[^a-z0-9]+", "_", slug_value.lower()).strip("_")
            self.config["slug"] = slug or f"game_{self.session_id}"

    def set_game_type(self, game_type: str) -> None:
        if game_type in GAME_TYPES:
            self.config["game_type"] = game_type
            # Auto-set board if required by this game type
            if GAME_TYPES[game_type].get("board_required", False):
                self.config["board_rows"] = self.config.get("board_rows", 3)
                self.config["board_cols"] = self.config.get("board_cols", 3)

    def set_board_dims(self, rows: int, cols: int) -> None:
        rows = max(1, min(rows, 20))
        cols = max(1, min(cols, 8))
        self.config["board_rows"] = rows
        self.config["board_cols"] = cols
        self._init_cells()

    def set_cell(self, row: int, col: int, field: str, value: str) -> bool:
        cells = self.config["cells"]
        if 0 <= row < len(cells) and 0 <= col < len(cells[row]):
            cells[row][col][field] = value
            return True
        return False

    def set_win_type(self, win_type: str) -> None:
        if win_type in WIN_TYPES:
            self.config["win_type"] = win_type

    def set_economy(self, field: str, value: Any) -> None:
        if field in ("entry_fee", "win_reward"):
            try:
                self.config[field] = float(value)
            except (ValueError, TypeError):
                pass
        elif field in ("turn_based",):
            self.config[field] = bool(value)

    # ── Button management ─────────────────────────────────────────

    def add_button(self) -> int:
        idx = len(self.config["buttons"])
        self.config["buttons"].append({
            "label": f"Button {idx + 1}",
            "callback": f"btn_{idx}",
            "style": "default",
        })
        return idx

    def edit_button(self, idx: int, field: str, value: str) -> bool:
        buttons = self.config["buttons"]
        if 0 <= idx < len(buttons):
            buttons[idx][field] = value
            return True
        return False

    def delete_button(self, idx: int) -> bool:
        buttons = self.config["buttons"]
        if 0 <= idx < len(buttons):
            buttons.pop(idx)
            return True
        return False

    # ── Validation ────────────────────────────────────────────────

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.config["name"].strip():
            errors.append("Game name is required")
        if not self.config["slug"].strip():
            errors.append("Slug is required")
        if self.config["min_players"] < 1:
            errors.append("Minimum players must be ≥ 1")
        if self.config["max_players"] < self.config["min_players"]:
            errors.append("Max players must be ≥ min players")
        if self.config["entry_fee"] < 0:
            errors.append("Entry fee cannot be negative")
        if self.config["win_reward"] < 0:
            errors.append("Win reward cannot be negative")
        return errors

    # ── Draft persistence ─────────────────────────────────────────

    async def save_draft(self) -> int:
        """Save (or update) the current session as a draft.  Returns draft id."""
        config_json = json.dumps(self.config)
        now = datetime.utcnow().isoformat()

        if self.draft_id is not None:
            await async_execute(
                "UPDATE game_drafts SET config_json = ?, updated_at = ? WHERE id = ?",
                (config_json, now, self.draft_id),
            )
            return self.draft_id

        cursor = await async_execute(
            "INSERT INTO game_drafts (user_id, session_id, config_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.user_id, self.session_id, config_json, now, now),
        )
        self.draft_id = cursor.lastrowid
        return self.draft_id  # type: ignore[return-value]

    async def load_draft(self, draft_id: int) -> bool:
        """Load a draft into this engine.  Returns True on success."""
        row = await async_fetchone(
            "SELECT id, config_json FROM game_drafts WHERE id = ? AND user_id = ?",
            (draft_id, self.user_id),
        )
        if not row:
            return False
        try:
            loaded = json.loads(row["config_json"])
        except (json.JSONDecodeError, TypeError):
            return False

        self.config.update(loaded)
        self._init_cells()
        self.draft_id = row["id"]
        return True

    @staticmethod
    async def delete_draft(draft_id: int, user_id: int) -> bool:
        # Verify ownership before deleting
        row = await async_fetchone(
            "SELECT user_id FROM game_drafts WHERE id = ?",
            (draft_id,),
        )
        if row is None or row["user_id"] != user_id:
            return False
        await async_execute(
            "DELETE FROM game_drafts WHERE id = ? AND user_id = ?",
            (draft_id, user_id),
        )
        return True

    @staticmethod
    async def list_drafts(user_id: int) -> list[dict]:
        rows = await async_fetchall(
            "SELECT id, updated_at FROM game_drafts WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )
        return [dict(r) for r in rows] if rows else []

    # ── Publishing ────────────────────────────────────────────────

    async def publish(self) -> Optional[int]:
        """
        Validate, write the game manifest + logic skeleton to disk,
        and register via the GameRegistry.

        Returns the game id on success, None on failure.
        """
        errors = self.validate()
        if errors:
            logger.warning("Publish validation failed: %s", errors)
            return None

        slug = self.config["slug"]
        game_dir = GAMES_DIR / slug
        game_dir.mkdir(parents=True, exist_ok=True)

        # Build manifest.json
        manifest = {
            "slug": slug,
            "name": self.config["name"],
            "version": "1.0.0",
            "description": self.config.get("description", ""),
            "min_players": self.config["min_players"],
            "max_players": self.config["max_players"],
            "mode": "multiplayer" if self.config["max_players"] > 1 else "single",
            "board": {
                "rows": self.config["board_rows"],
                "cols": self.config["board_cols"],
            },
            "rewards": {
                "entry_fee": self.config["entry_fee"],
                "win_reward": self.config["win_reward"],
            },
            "win_type": self.config["win_type"],
            "buttons": self.config["buttons"],
            "author": self.user_name,
            "categories": [self.config["game_type"]],
            "icon": "🎮",
        }

        manifest_path = game_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, ensure_ascii=False)

        # Write a minimal logic.py scaffold
        logic_code = _LOGIC_PY_TEMPLATE.format(
            game_name=self.config["name"],
            slug=slug,
            rows=self.config["board_rows"],
            cols=self.config["board_cols"],
        )
        logic_path = game_dir / "logic.py"
        with open(logic_path, "w", encoding="utf-8") as fh:
            fh.write(logic_code)

        # Register in database via GameRegistry (imported lazily to avoid circulars)
        from game_bot.engine.registry import GameRegistry

        registry = GameRegistry()
        game_id = await registry.register_game(
            slug=slug,
            name=self.config["name"],
            creator=self.user_name,
            game_type=self.config["game_type"],
            version="1.0.0",
            manifest=manifest,
            user_id=self.user_id,
            file_path=str(game_dir),
        )

        # Delete draft after successful publish
        if self.draft_id is not None:
            await self.delete_draft(self.draft_id, self.user_id)

        logger.info("Game published: %s (id=%s) by user %s", slug, game_id, self.user_id)
        return game_id


# ═══════════════════════════════════════════════════════════════════════
# Builder Renderer – turns BuilderEngine state into Telegram messages
# ═══════════════════════════════════════════════════════════════════════

class BuilderRenderer:
    """Renders a BuilderEngine session into (text, InlineKeyboardMarkup)."""

    def render(self, engine: BuilderEngine) -> tuple[str, InlineKeyboardMarkup]:
        step = engine.step_name()
        renderer = _STEP_RENDERERS.get(step, _render_step_name)
        text, keyboard = renderer(engine)
        return text, InlineKeyboardMarkup(keyboard)

    async def render_drafts(self, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        drafts = await BuilderEngine.list_drafts(user_id)
        if not drafts:
            text = "📭 <b>Your Drafts</b>\n\nYou have no saved drafts."
            keyboard = [[InlineKeyboardButton("🔨 New Game", callback_data="builder_start")],
                        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]
            return text, InlineKeyboardMarkup(keyboard)

        text = "📬 <b>Your Drafts</b>\n\nSelect a draft to load or delete:\n\n"
        keyboard: list[list[InlineKeyboardButton]] = []
        for d in drafts:
            name = d.get("name") or "Untitled"
            updated = d.get("updated_at", "")[:16]
            text += f"  📝 <b>{name}</b>  <i>{updated}</i>\n"
            keyboard.append([
                InlineKeyboardButton(
                    f"📂 {name}",
                    callback_data=f"builder_load_draft:{d['id']}",
                ),
                InlineKeyboardButton(
                    "🗑",
                    callback_data=f"builder_delete_draft:{d['id']}",
                ),
            ])

        keyboard.append([InlineKeyboardButton("🔨 New Game", callback_data="builder_start")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
        return text, InlineKeyboardMarkup(keyboard)


# ═══════════════════════════════════════════════════════════════════════
# Step renderers (private)
# ═══════════════════════════════════════════════════════════════════════

def _nav_row(engine: BuilderEngine) -> list[InlineKeyboardButton]:
    """Common navigation row: prev / step indicator / next."""
    buttons: list[InlineKeyboardButton] = []
    if engine.current_step > 0:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data="builder:prev"))
    step_num = engine.current_step + 1
    total = len(BUILDER_STEPS)
    buttons.append(
        InlineKeyboardButton(f"📌 {step_num}/{total}", callback_data="builder:noop")
    )
    if engine.current_step < len(BUILDER_STEPS) - 1:
        buttons.append(InlineKeyboardButton("➡️ Next", callback_data="builder:next"))
    return buttons


def _common_footer(engine: BuilderEngine) -> list[list[InlineKeyboardButton]]:
    """Common footer rows: save draft / cancel / drafts list."""
    return [
        [
            InlineKeyboardButton("💾 Save Draft", callback_data="builder:save"),
            InlineKeyboardButton("📋 Drafts", callback_data="builder_drafts"),
        ],
        [
            InlineKeyboardButton("🚫 Cancel", callback_data="builder:cancel"),
        ],
    ]


def _step_links(engine: BuilderEngine) -> list[list[InlineKeyboardButton]]:
    """Quick-jump buttons for each step (one button per row)."""
    rows: list[list[InlineKeyboardButton]] = []
    for i, step in enumerate(BUILDER_STEPS):
        marker = "▶ " if i == engine.current_step else ""
        title = STEP_TITLES.get(step, step)
        rows.append([
            InlineKeyboardButton(f"{marker}{title}", callback_data=f"builder:goto:{step}")
        ])
    return rows


# ── Step 1: Name & Description ────────────────────────────────────

def _render_step_name(engine: BuilderEngine) -> tuple[str, list[list[InlineKeyboardButton]]]:
    cfg = engine.config
    text = (
        f"📝 <b>Step 1: Name &amp; Description</b>\n\n"
        f"🏷 <b>Name:</b> {cfg['name'] or '<i>not set</i>'}\n"
        f"🔖 <b>Slug:</b> <code>{cfg['slug'] or '—'}</code>\n"
        f"📄 <b>Description:</b> {cfg.get('description') or '<i>not set</i>'}\n\n"
        f"Tap a field below to edit it by typing:"
    )
    keyboard: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("✏️ Edit Name", callback_data="builder_edit_field:name")],
        [InlineKeyboardButton("✏️ Edit Slug", callback_data="builder_edit_field:slug")],
        [InlineKeyboardButton("✏️ Edit Description", callback_data="builder_edit_field:description")],
        _nav_row(engine),
    ]
    keyboard.extend(_common_footer(engine))
    return text, keyboard


# ── Step 2: Game Type ─────────────────────────────────────────────

def _render_step_game_type(engine: BuilderEngine) -> tuple[str, list[list[InlineKeyboardButton]]]:
    cfg = engine.config
    current = cfg["game_type"]
    type_rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for gt in GAME_TYPES:
        marker = " ✅" if gt == current else ""
        row.append(
            InlineKeyboardButton(f"{gt}{marker}", callback_data=f"builder:game_type:{gt}")
        )
        if len(row) >= 3:
            type_rows.append(row)
            row = []
    if row:
        type_rows.append(row)

    text = (
        f"🎮 <b>Step 2: Game Type</b>\n\n"
        f"Current: <b>{current}</b>\n\n"
        f"Select a game type:"
    )
    keyboard = type_rows
    keyboard.append(_nav_row(engine))
    keyboard.extend(_common_footer(engine))
    return text, keyboard


# ── Step 3: Board ─────────────────────────────────────────────────

def _render_step_board(engine: BuilderEngine) -> tuple[str, list[list[InlineKeyboardButton]]]:
    cfg = engine.config
    rows_val = cfg["board_rows"]
    cols_val = cfg["board_cols"]
    current = cfg["game_type"]

    text = (
        f"🔲 <b>Step 3: Board Configuration</b>\n\n"
        f"📊 Rows: <b>{rows_val}</b>\n"
        f"📊 Cols: <b>{cols_val}</b>\n\n"
        f"Adjust board dimensions:"
    )

    # Row adjustment buttons
    row_btns: list[InlineKeyboardButton] = [
        InlineKeyboardButton("➖ Row", callback_data="builder:board:rows:-1"),
        InlineKeyboardButton(f"{rows_val}", callback_data="builder:noop"),
        InlineKeyboardButton("➕ Row", callback_data="builder:board:rows:1"),
    ]
    col_btns: list[InlineKeyboardButton] = [
        InlineKeyboardButton("➖ Col", callback_data="builder:board:cols:-1"),
        InlineKeyboardButton(f"{cols_val}", callback_data="builder:noop"),
        InlineKeyboardButton("➕ Col", callback_data="builder:board:cols:1"),
    ]

    keyboard: list[list[InlineKeyboardButton]] = [row_btns, col_btns]

    # Cell property quick-edit (first 3 rows × all cols shown)
    text += "\n🧩 <b>Cell labels</b> (tap to change):\n"
    for r in range(min(rows_val, 5)):
        cell_row: list[InlineKeyboardButton] = []
        for c in range(min(cols_val, 8)):
            cell = cfg["cells"][r][c] if r < len(cfg["cells"]) and c < len(cfg["cells"][r]) else {}
            label = cell.get("label", "·")
            cell_row.append(
                InlineKeyboardButton(
                    label,
                    callback_data=f"builder:cell:{r}:{c}:label",
                )
            )
        keyboard.append(cell_row)

    keyboard.append(_nav_row(engine))
    keyboard.extend(_common_footer(engine))
    return text, keyboard


# ── Step 4: Buttons ───────────────────────────────────────────────

def _render_step_buttons(engine: BuilderEngine) -> tuple[str, list[list[InlineKeyboardButton]]]:
    cfg = engine.config
    buttons_list = cfg.get("buttons", [])

    text = (
        f"🔘 <b>Step 4: Button Configuration</b>\n\n"
        f"Configured buttons: <b>{len(buttons_list)}</b>\n\n"
    )
    for i, btn in enumerate(buttons_list):
        text += f"  {i + 1}. <b>{btn.get('label', '?')}</b> → <code>{btn.get('callback', '?')}</code>\n"

    if not buttons_list:
        text += "<i>No buttons configured yet. Add one below.</i>\n"

    text += "\nManage buttons:"

    keyboard: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("➕ Add Button", callback_data="builder:button:add")],
    ]

    for i, btn in enumerate(buttons_list):
        keyboard.append([
            InlineKeyboardButton(
                f"✏️ {btn.get('label', f'Btn {i}')}",
                callback_data=f"builder:button:edit:{i}",
            ),
            InlineKeyboardButton(
                "🗑",
                callback_data=f"builder:button:delete:{i}",
            ),
        ])

    keyboard.append(_nav_row(engine))
    keyboard.extend(_common_footer(engine))
    return text, keyboard


# ── Step 5: Win Condition ─────────────────────────────────────────

def _render_step_win_type(engine: BuilderEngine) -> tuple[str, list[list[InlineKeyboardButton]]]:
    cfg = engine.config
    current = cfg["win_type"]
    wt_rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for wt in WIN_TYPES:
        marker = " ✅" if wt == current else ""
        row.append(
            InlineKeyboardButton(f"{wt}{marker}", callback_data=f"builder:win_type:{wt}")
        )
        if len(row) >= 2:
            wt_rows.append(row)
            row = []
    if row:
        wt_rows.append(row)

    text = (
        f"🏆 <b>Step 5: Win Condition</b>\n\n"
        f"Current: <b>{current}</b>\n"
        f"Description: {cfg.get('win_condition_desc') or '<i>not set</i>'}\n\n"
        f"Select a win type:"
    )

    keyboard = wt_rows
    keyboard.append(
        [InlineKeyboardButton("✏️ Edit Win Description", callback_data="builder_edit_field:win_condition_desc")]
    )
    keyboard.append(_nav_row(engine))
    keyboard.extend(_common_footer(engine))
    return text, keyboard


# ── Step 6: Economy ───────────────────────────────────────────────

def _render_step_economy(engine: BuilderEngine) -> tuple[str, list[list[InlineKeyboardButton]]]:
    cfg = engine.config
    entry = cfg.get("entry_fee", 0)
    reward = cfg.get("win_reward", 0)
    turn_based = cfg.get("turn_based", True)

    text = (
        f"💰 <b>Step 6: Economy</b>\n\n"
        f"🎟 Entry Fee: <b>{entry:.2f} {CURRENCY_NAME}</b>\n"
        f"🏆 Win Reward: <b>{reward:.2f} {CURRENCY_NAME}</b>\n"
        f"🔄 Turn Based: <b>{'Yes' if turn_based else 'No'}</b>\n"
    )

    keyboard: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("➖ Fee", callback_data="builder:econ:entry_fee:-1"),
            InlineKeyboardButton(f"Fee: {entry:.0f}", callback_data="builder:noop"),
            InlineKeyboardButton("➕ Fee", callback_data="builder:econ:entry_fee:1"),
        ],
        [
            InlineKeyboardButton("➖ Reward", callback_data="builder:econ:win_reward:-1"),
            InlineKeyboardButton(f"Reward: {reward:.0f}", callback_data="builder:noop"),
            InlineKeyboardButton("➕ Reward", callback_data="builder:econ:win_reward:1"),
        ],
        [
            InlineKeyboardButton(
                f"Turn Based: {'✅' if turn_based else '❌'}",
                callback_data=f"builder:econ:turn_based:{'false' if turn_based else 'true'}",
            ),
        ],
        [
            InlineKeyboardButton("✏️ Set Fee", callback_data="builder_edit_field:entry_fee"),
            InlineKeyboardButton("✏️ Set Reward", callback_data="builder_edit_field:win_reward"),
        ],
        _nav_row(engine),
    ]
    keyboard.extend(_common_footer(engine))
    return text, keyboard


# ── Step 7: Review ────────────────────────────────────────────────

def _render_step_review(engine: BuilderEngine) -> tuple[str, list[list[InlineKeyboardButton]]]:
    cfg = engine.config
    errors = engine.validate()

    text = (
        f"📋 <b>Step 7: Review &amp; Publish</b>\n\n"
        f"🏷 Name: <b>{cfg['name'] or '—'}</b>\n"
        f"🔖 Slug: <code>{cfg['slug'] or '—'}</code>\n"
        f"📄 Description: {cfg.get('description') or '—'}\n"
        f"🎮 Type: <b>{cfg['game_type']}</b>\n"
        f"👥 Players: {cfg['min_players']}–{cfg['max_players']}\n"
        f"🔲 Board: {cfg['board_rows']}×{cfg['board_cols']}\n"
        f"🏆 Win: {cfg['win_type']}\n"
        f"🎟 Entry Fee: {cfg['entry_fee']:.2f} {CURRENCY_NAME}\n"
        f"🏆 Win Reward: {cfg['win_reward']:.2f} {CURRENCY_NAME}\n"
        f"🔘 Buttons: {len(cfg.get('buttons', []))}\n"
        f"🔄 Turn Based: {'Yes' if cfg.get('turn_based', True) else 'No'}\n"
    )

    if errors:
        text += "\n⚠️ <b>Issues to fix:</b>\n"
        for e in errors:
            text += f"  ❌ {e}\n"

    keyboard: list[list[InlineKeyboardButton]] = []

    if not errors:
        keyboard.append([InlineKeyboardButton("🚀 Publish Game", callback_data="builder:publish")])

    keyboard.extend(_step_links(engine))
    keyboard.append(_nav_row(engine))
    keyboard.extend(_common_footer(engine))
    return text, keyboard


# Registry of step renderers
_STEP_RENDERERS = {
    "name":      _render_step_name,
    "game_type": _render_step_game_type,
    "board":     _render_step_board,
    "buttons":   _render_step_buttons,
    "win_type":  _render_step_win_type,
    "economy":   _render_step_economy,
    "review":    _render_step_review,
}


# ═══════════════════════════════════════════════════════════════════════
# Logic.py template for generated games
# ═══════════════════════════════════════════════════════════════════════

_LOGIC_PY_TEMPLATE = '''\
"""
Auto-generated game logic for {game_name} ({slug})
Generated by the Telegram Game Builder.
"""

from __future__ import annotations
import json
from typing import Any, Optional


def init_game(players: list[dict], mode: str = "classic", settings: dict | None = None) -> dict:
    """Initialise game state for a new session."""
    board_rows = {rows}
    board_cols = {cols}
    cells = [["" for _ in range(board_cols)] for _ in range(board_rows)]
    return {{
        "phase": "playing",
        "turn_index": 0,
        "board_rows": board_rows,
        "board_cols": board_cols,
        "cells": cells,
        "players": players,
    }}


def render(game_state: dict) -> dict:
    """Return render context for the GameRenderer."""
    cells = game_state.get("cells", [])
    rows = game_state.get("board_rows", 3)
    cols = game_state.get("board_cols", 3)

    cell_actions = [
        [f"cell:{{r}}:{{c}}" for c in range(cols)]
        for r in range(rows)
    ]

    return {{
        "board": {{
            "rows": rows,
            "cols": cols,
            "cells": cells,
            "cell_actions": cell_actions,
        }},
        "state": {{
            "phase": game_state.get("phase", "playing"),
        }},
        "footer": {{
            "actions": [
                {{"label": "🔄 Refresh", "callback": "refresh", "visible": True}},
            ],
        }},
    }}


def handle_callback(context: dict) -> dict:
    """Process a callback action and return updated state."""
    action = context.get("action", "")
    user_id = context.get("user_id")
    game_state = context.get("game_state", {{}})

    # Handle cell clicks
    if action.startswith("cell:"):
        parts = action.split(":")
        if len(parts) == 3:
            r, c = int(parts[1]), int(parts[2])
            cells = game_state.get("cells", [])
            if 0 <= r < len(cells) and 0 <= c < len(cells[r]):
                current = cells[r][c]
                cells[r][c] = "X" if not current else ""
                game_state["cells"] = cells

    return {{
        "game_state": game_state,
        "advance_turn": True,
    }}


def check_win(game_state: dict) -> Any:
    """Check if the game has a winner. Return winner info or None/False."""
    # TODO: implement actual win-check logic
    return None


def serialize_state(game_state: dict) -> str:
    """Serialize game state to a JSON string."""
    return json.dumps(game_state)


def deserialize_state(data: str) -> dict:
    """Deserialize game state from a JSON string."""
    return json.loads(data)
'''


# ═══════════════════════════════════════════════════════════════════════
# Schema bootstrap – ensure builder_drafts table exists
# ═══════════════════════════════════════════════════════════════════════

_BUILDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS game_drafts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    session_id  TEXT    NOT NULL DEFAULT '',
    config_json TEXT    NOT NULL DEFAULT '{}',
    current_step TEXT   NOT NULL DEFAULT 'HOME',
    status      TEXT    NOT NULL DEFAULT 'in_progress',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_game_drafts_user_id ON game_drafts (user_id);
"""

_schema_initialized = False


async def _ensure_schema() -> None:
    global _schema_initialized
    if _schema_initialized:
        return
    try:
        await async_execute("SELECT 1 FROM game_drafts LIMIT 1")
    except Exception:
        from database.db import get_db
        get_db().executescript(_BUILDER_SCHEMA)
        logger.info("game_drafts table created")
    _schema_initialized = True


# ═══════════════════════════════════════════════════════════════════════
# Handler: Entry Point
# ═══════════════════════════════════════════════════════════════════════

async def builder_entry_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Entry point for the Game Builder.

    Checks if the user has a Game Creation License (owned_features table).
    If yes, creates a BuilderEngine session, sends the initial message,
    and stores the message_id in context.user_data.
    If no, shows a purchase prompt.
    """
    await _ensure_schema()

    query = update.callback_query
    if query:
        await query.answer()

    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or ""

    # Check for Game Creation License
    license_row = await async_fetchone(
        "SELECT id FROM owned_features WHERE user_id = ? AND feature_type = 'game_creation_license'",
        (user_id,),
    )

    if not license_row:
        text = (
            "🔐 <b>Game Creation License Required</b>\n\n"
            "You need a Game Creation License to build and publish games.\n\n"
            f"🎫 Purchase one from the Marketplace to unlock:\n"
            "  • Step-by-step game builder\n"
            "  • Custom board & button configuration\n"
            "  • Economy & reward settings\n"
            "  • Publish to the game library\n"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Get License", callback_data="marketplace_view")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")],
        ])
        if query:
            try:
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
            except Exception:
                await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    # User has a license – create builder session
    engine = BuilderEngine(user_id=user_id, user_name=user_name)
    renderer = BuilderRenderer()
    text, reply_markup = renderer.render(engine)

    # Store engine and renderer in bot_data for this session
    if "builder_sessions" not in context.bot_data:
        context.bot_data["builder_sessions"] = {}
    context.bot_data["builder_sessions"][engine.session_id] = engine

    # Store session id and clear any editing state
    context.user_data["builder_session_id"] = engine.session_id
    context.user_data.pop("builder_editing_field", None)

    # Send the initial builder message
    if query:
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            msg = await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
            context.user_data["builder_message_id"] = msg.message_id
            context.user_data["builder_chat_id"] = msg.chat_id
            return
        context.user_data["builder_message_id"] = query.message.message_id
        context.user_data["builder_chat_id"] = query.message.chat_id
    else:
        msg = await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        context.user_data["builder_message_id"] = msg.message_id
        context.user_data["builder_chat_id"] = msg.chat_id


# ═══════════════════════════════════════════════════════════════════════
# Handler: Callback Router
# ═══════════════════════════════════════════════════════════════════════

async def builder_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Main callback router for all builder:* callbacks.

    Parses the callback data, dispatches to the appropriate engine method,
    then re-renders the builder message via edit_message_text.
    """
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id

    # Retrieve the engine for this user's session
    engine = _get_engine(context)
    renderer = BuilderRenderer()

    # ── Special callbacks that don't require an active engine ──────
    if data == "builder_start":
        await builder_entry_handler(update, context)
        return

    if data == "builder_drafts":
        await builder_drafts_handler(update, context)
        return

    if data.startswith("builder_load_draft:"):
        draft_id = int(data.split(":")[1])
        await _handle_load_draft(query, context, engine, renderer, draft_id)
        return

    if data.startswith("builder_delete_draft:"):
        draft_id = int(data.split(":")[1])
        await _handle_delete_draft(query, context, engine, renderer, draft_id)
        return

    if data.startswith("builder_edit_field:"):
        field_name = data.split(":", 1)[1]
        await _handle_edit_field(query, context, engine, renderer, field_name)
        return

    if data == "builder_confirm_field":
        await query.answer("Please type the value first.", show_alert=True)
        return

    if data == "builder_cancel_edit":
        context.user_data.pop("builder_editing_field", None)
        if engine:
            await _re_render(query, context, engine, renderer)
        else:
            await query.answer("No active session.", show_alert=True)
        return

    # ── All builder:* callbacks require an active engine ──────────
    if engine is None:
        await query.answer("No active builder session. Start a new one.", show_alert=True)
        return

    # Parse the callback
    parts = data.split(":")

    try:
        if data == "builder:noop":
            await query.answer()

        elif data == "builder:next":
            if engine.next_step():
                await _re_render(query, context, engine, renderer)
                await query.answer(f"→ {STEP_TITLES.get(engine.step_name(), engine.step_name())}")
            else:
                await query.answer("Already at the last step.")

        elif data == "builder:prev":
            if engine.prev_step():
                await _re_render(query, context, engine, renderer)
                await query.answer(f"← {STEP_TITLES.get(engine.step_name(), engine.step_name())}")
            else:
                await query.answer("Already at the first step.")

        elif data.startswith("builder:goto:"):
            step = parts[2]
            if engine.goto_step(step):
                await _re_render(query, context, engine, renderer)
                await query.answer(f"→ {STEP_TITLES.get(step, step)}")
            else:
                await query.answer("Unknown step.")

        elif data == "builder:save":
            draft_id = engine.save_draft()
            await query.answer(f"💾 Draft saved! (ID: {draft_id})")

        elif data == "builder:publish":
            game_id = engine.publish()
            if game_id is not None:
                text = (
                    f"🎉 <b>Game Published!</b>\n\n"
                    f"🏷 {engine.config['name']}\n"
                    f"🔖 <code>{engine.config['slug']}</code>\n"
                    f"🆔 Game ID: <code>{game_id}</code>\n\n"
                    f"Your game is now pending admin approval.\n"
                    f"Once approved, it will appear in the game library!"
                )
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎮 Browse Games", callback_data="browse_games")],
                    [InlineKeyboardButton("🔨 Build Another", callback_data="builder_start")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
                ])
                try:
                    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
                except Exception:
                    await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
                # Clean up session
                _remove_engine(context)
                context.user_data.pop("builder_session_id", None)
                context.user_data.pop("builder_editing_field", None)
                return
            else:
                errors = engine.validate()
                err_text = "\n".join(f"❌ {e}" for e in errors)
                await query.answer(f"Cannot publish:\n{err_text}", show_alert=True)

        elif data == "builder:cancel":
            _remove_engine(context)
            context.user_data.pop("builder_session_id", None)
            context.user_data.pop("builder_editing_field", None)
            text = "🚫 <b>Builder session cancelled.</b>"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔨 New Game", callback_data="builder_start")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
            ])
            try:
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
            except Exception:
                await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

        elif data.startswith("builder:set:"):
            # builder:set:FIELD:VALUE
            if len(parts) >= 4:
                field = parts[2]
                value = ":".join(parts[3:])
                engine.set_field(field, value)
                await _re_render(query, context, engine, renderer)
                await query.answer(f"Set {field} = {value}")

        elif data.startswith("builder:game_type:"):
            game_type = parts[2]
            engine.set_game_type(game_type)
            await _re_render(query, context, engine, renderer)
            await query.answer(f"Game type: {game_type}")

        elif data.startswith("builder:button:add"):
            idx = engine.add_button()
            await _re_render(query, context, engine, renderer)
            await query.answer(f"Button {idx + 1} added")

        elif data.startswith("builder:button:edit:"):
            idx = int(parts[3])
            buttons = engine.config.get("buttons", [])
            if 0 <= idx < len(buttons):
                # Enter edit mode for button label
                context.user_data["builder_editing_field"] = f"button_label_{idx}"
                text, reply_markup = renderer.render(engine)
                text += f"\n\n✏️ <b>Editing Button {idx + 1} label</b>\nType the new label:"
                keyboard = list(reply_markup.inline_keyboard)
                keyboard.append([
                    InlineKeyboardButton("❌ Cancel", callback_data="builder_cancel_edit"),
                ])
                try:
                    await query.edit_message_text(text, parse_mode="HTML",
                                                  reply_markup=InlineKeyboardMarkup(keyboard))
                except Exception:
                    pass
                await query.answer(f"Editing button {idx + 1} label")
            else:
                await query.answer("Button not found.", show_alert=True)

        elif data.startswith("builder:button:delete:"):
            idx = int(parts[3])
            if engine.delete_button(idx):
                await _re_render(query, context, engine, renderer)
                await query.answer("Button deleted")
            else:
                await query.answer("Cannot delete button.", show_alert=True)

        elif data.startswith("builder:button:"):
            # builder:button:FIELD:IDX:VALUE
            if len(parts) >= 5:
                field = parts[2]
                idx = int(parts[3])
                value = ":".join(parts[4:])
                if engine.edit_button(idx, field, value):
                    await _re_render(query, context, engine, renderer)
                    await query.answer(f"Button {idx} {field} updated")
                else:
                    await query.answer("Failed to update button.", show_alert=True)

        elif data.startswith("builder:board:"):
            # builder:board:rows:VALUE  or  builder:board:cols:VALUE
            dim = parts[2]  # "rows" or "cols"
            delta = int(parts[3])
            rows = engine.config["board_rows"]
            cols = engine.config["board_cols"]
            if dim == "rows":
                engine.set_board_dims(rows + delta, cols)
            elif dim == "cols":
                engine.set_board_dims(rows, cols + delta)
            await _re_render(query, context, engine, renderer)
            await query.answer(f"Board: {engine.config['board_rows']}×{engine.config['board_cols']}")

        elif data.startswith("builder:cell:"):
            # builder:cell:R:C:FIELD  – prompt user to type new label
            r, c, field = int(parts[2]), int(parts[3]), parts[4]
            context.user_data["builder_editing_field"] = f"cell_{r}_{c}_{field}"
            text, reply_markup = renderer.render(engine)
            current = engine.config["cells"][r][c].get(field, "·")
            text += f"\n\n✏️ <b>Editing cell [{r},{c}] {field}</b>\nCurrent: <code>{current}</code>\nType the new value:"
            keyboard = list(reply_markup.inline_keyboard)
            keyboard.append([
                InlineKeyboardButton("❌ Cancel", callback_data="builder_cancel_edit"),
            ])
            try:
                await query.edit_message_text(text, parse_mode="HTML",
                                              reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception:
                pass
            await query.answer(f"Editing cell [{r},{c}] {field}")

        elif data.startswith("builder:win_type:"):
            win_type = parts[2]
            engine.set_win_type(win_type)
            await _re_render(query, context, engine, renderer)
            await query.answer(f"Win type: {win_type}")

        elif data.startswith("builder:econ:"):
            # builder:econ:FIELD:VALUE  or builder:econ:FIELD:DELTA
            field = parts[2]
            raw_val = parts[3]
            if field in ("entry_fee", "win_reward"):
                try:
                    delta = float(raw_val)
                    current = engine.config.get(field, 0)
                    engine.set_economy(field, max(0, current + delta))
                except ValueError:
                    engine.set_economy(field, raw_val)
            elif field == "turn_based":
                engine.set_economy("turn_based", raw_val == "true")
            await _re_render(query, context, engine, renderer)
            await query.answer("Economy updated")

        else:
            await query.answer("Unknown builder action.")

    except Exception as exc:
        logger.error("Builder callback error for %s: %s", data, exc, exc_info=True)
        await query.answer("An error occurred. Please try again.", show_alert=True)


# ═══════════════════════════════════════════════════════════════════════
# Handler: Text Input
# ═══════════════════════════════════════════════════════════════════════

async def builder_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles text input for fields that require typing
    (game name, description, slug, cell labels, button labels, etc.).

    Uses context.user_data["builder_editing_field"] to track which field
    is being edited.  Updates config via engine, then re-renders.
    """
    editing_field = context.user_data.get("builder_editing_field")
    if not editing_field:
        # Not in text-edit mode – ignore (let other handlers process)
        return

    engine = _get_engine(context)
    if engine is None:
        context.user_data.pop("builder_editing_field", None)
        return

    text_input = update.message.text.strip()
    if not text_input:
        await update.message.reply_text("⚠️ Empty input. Please type a value or tap Cancel.")
        return

    renderer = BuilderRenderer()

    # ── Simple text fields ────────────────────────────────────────
    if editing_field in TEXT_FIELDS:
        engine.set_field(editing_field, text_input)

    # ── Win condition description ─────────────────────────────────
    elif editing_field == "win_condition_desc":
        engine.config["win_condition_desc"] = text_input

    # ── Entry fee / win reward (numeric) ──────────────────────────
    elif editing_field in ("entry_fee", "win_reward"):
        try:
            value = float(text_input)
            if value < 0:
                raise ValueError("Must be non-negative")
            engine.set_economy(editing_field, value)
        except ValueError:
            await update.message.reply_text("⚠️ Please enter a valid non-negative number.")
            return

    # ── Button label: button_label_IDX ────────────────────────────
    elif editing_field.startswith("button_label_"):
        try:
            idx = int(editing_field.split("_")[-1])
            engine.edit_button(idx, "label", text_input)
        except (ValueError, IndexError):
            await update.message.reply_text("⚠️ Invalid button index.")
            return

    # ── Button callback: button_callback_IDX ──────────────────────
    elif editing_field.startswith("button_callback_"):
        try:
            idx = int(editing_field.split("_")[-1])
            engine.edit_button(idx, "callback", text_input)
        except (ValueError, IndexError):
            await update.message.reply_text("⚠️ Invalid button index.")
            return

    # ── Cell field: cell_R_C_FIELD ────────────────────────────────
    elif editing_field.startswith("cell_"):
        parts = editing_field.split("_")
        if len(parts) >= 4:
            try:
                r, c = int(parts[1]), int(parts[2])
                field_name = "_".join(parts[3:])
                if not engine.set_cell(r, c, field_name, text_input):
                    await update.message.reply_text("⚠️ Invalid cell coordinates.")
                    return
            except (ValueError, IndexError):
                await update.message.reply_text("⚠️ Invalid cell reference.")
                return

    # ── Min/Max players ───────────────────────────────────────────
    elif editing_field in ("min_players", "max_players"):
        try:
            value = int(text_input)
            if value < 1:
                raise ValueError
            engine.set_field(editing_field, value)
        except ValueError:
            await update.message.reply_text("⚠️ Please enter a positive integer.")
            return

    else:
        await update.message.reply_text(f"⚠️ Unknown field: {editing_field}")
        context.user_data.pop("builder_editing_field", None)
        return

    # Clear editing state
    context.user_data.pop("builder_editing_field", None)

    # Re-render the builder message
    msg_id = context.user_data.get("builder_message_id")
    chat_id = context.user_data.get("builder_chat_id")
    if msg_id and chat_id:
        render_text, reply_markup = renderer.render(engine)
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=render_text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        except Exception:
            logger.debug("Failed to edit builder message after text input")
    else:
        # Fallback: send a new message
        render_text, reply_markup = renderer.render(engine)
        msg = await update.message.reply_text(render_text, parse_mode="HTML", reply_markup=reply_markup)
        context.user_data["builder_message_id"] = msg.message_id
        context.user_data["builder_chat_id"] = msg.chat_id

    # Delete the user's input message to keep the chat clean
    try:
        await update.message.delete()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════
# Handler: Drafts List
# ═══════════════════════════════════════════════════════════════════════

async def builder_drafts_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows list of user's saved drafts with load/delete buttons."""
    await _ensure_schema()

    query = update.callback_query
    if query:
        await query.answer()

    user_id = update.effective_user.id
    renderer = BuilderRenderer()
    text, reply_markup = renderer.render_drafts(user_id)

    if query:
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)


# ═══════════════════════════════════════════════════════════════════════
# Private helpers
# ═══════════════════════════════════════════════════════════════════════

def _get_engine(context: ContextTypes.DEFAULT_TYPE) -> Optional[BuilderEngine]:
    """Retrieve the BuilderEngine for the current user's session."""
    session_id = context.user_data.get("builder_session_id")
    if not session_id:
        return None
    sessions = context.bot_data.get("builder_sessions", {})
    return sessions.get(session_id)


def _remove_engine(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove the BuilderEngine from bot_data."""
    session_id = context.user_data.get("builder_session_id")
    if session_id:
        sessions = context.bot_data.get("builder_sessions", {})
        sessions.pop(session_id, None)


async def _re_render(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    engine: BuilderEngine,
    renderer: BuilderRenderer,
) -> None:
    """Re-render the builder message and edit it in place."""
    text, reply_markup = renderer.render(engine)
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        logger.debug("Builder re-render edit failed (content may be unchanged)")
    # Update stored message id
    context.user_data["builder_message_id"] = query.message.message_id
    context.user_data["builder_chat_id"] = query.message.chat_id


async def _handle_edit_field(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    engine: BuilderEngine,
    renderer: BuilderRenderer,
    field_name: str,
) -> None:
    """Enter text-editing mode for a specific field."""
    if engine is None:
        await query.answer("No active builder session.", show_alert=True)
        return

    context.user_data["builder_editing_field"] = field_name

    # Determine prompt
    field_prompts = {
        "name": "game name",
        "description": "game description",
        "slug": "URL slug (lowercase, underscores)",
        "win_condition_desc": "win condition description",
        "entry_fee": f"entry fee in {CURRENCY_NAME}",
        "win_reward": f"win reward in {CURRENCY_NAME}",
        "min_players": "minimum players",
        "max_players": "maximum players",
    }

    prompt = field_prompts.get(field_name, field_name)
    text, reply_markup = renderer.render(engine)
    text += (
        f"\n\n✏️ <b>Editing: {prompt}</b>\n"
        f"Type the new value below, or tap Cancel to abort."
    )

    keyboard = list(reply_markup.inline_keyboard)
    keyboard.append([
        InlineKeyboardButton("✅ Confirm", callback_data="builder_confirm_field"),
        InlineKeyboardButton("❌ Cancel", callback_data="builder_cancel_edit"),
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML",
                                      reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        pass
    await query.answer(f"✏️ Type the {prompt}")


async def _handle_load_draft(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    engine: Optional[BuilderEngine],
    renderer: BuilderRenderer,
    draft_id: int,
) -> None:
    """Load a draft into the current (or new) builder session."""
    user_id = query.from_user.id
    user_name = query.from_user.first_name or ""

    # Create engine if needed
    if engine is None:
        engine = BuilderEngine(user_id=user_id, user_name=user_name)
        if "builder_sessions" not in context.bot_data:
            context.bot_data["builder_sessions"] = {}
        context.bot_data["builder_sessions"][engine.session_id] = engine
        context.user_data["builder_session_id"] = engine.session_id
        context.user_data["builder_message_id"] = query.message.message_id
        context.user_data["builder_chat_id"] = query.message.chat_id

    if engine.load_draft(draft_id):
        await _re_render(query, context, engine, renderer)
        await query.answer("📂 Draft loaded!")
    else:
        await query.answer("Failed to load draft.", show_alert=True)


async def _handle_delete_draft(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    engine: Optional[BuilderEngine],
    renderer: BuilderRenderer,
    draft_id: int,
) -> None:
    """Delete a draft and refresh the drafts list."""
    user_id = query.from_user.id
    BuilderEngine.delete_draft(draft_id, user_id)

    # Re-render drafts view
    text, reply_markup = renderer.render_drafts(user_id)
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    await query.answer("🗑 Draft deleted")
