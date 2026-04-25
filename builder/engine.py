"""
Builder Engine

The core state-machine-based, live-message game creation tool.
All interactions happen in a single Telegram message that gets edited
via editMessageText. The engine manages builder sessions, handles
callback actions, and coordinates rendering, validation, and exporting.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from telegram import InlineKeyboardMarkup

from database import async_execute, async_fetchone, async_fetchall, async_transaction

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
from .renderer import BuilderRenderer
from .validator import BuilderValidator
from .exporter import GameExporter
from .draft import DraftManager

logger = logging.getLogger(__name__)


class BuilderSessionError(Exception):
    """Raised when a builder session operation fails."""
    pass


class BuilderEngine:
    """
    State machine that manages builder sessions for creating Telegram games.

    Sessions are persisted in the ``builder_sessions`` database table.
    Drafts are managed via the ``DraftManager`` in ``game_drafts``.
    """

    STATE_ORDER = STATE_ORDER

    # Default config template for new sessions
    DEFAULT_CONFIG = {
        "game_name": "",
        "description": "",
        "creator_name": "",
        "visibility": "public",
        "tags": [],
        "summary": "",
        "game_type": "",
        "min_players": 2,
        "max_players": 4,
        "join_rules": "open",
        "entry_fee": 0,
        "spectator_mode": False,
        "private_room_support": False,
        "buttons": [],
        "board": {
            "enabled": True,
            "rows": 3,
            "cols": 3,
            "density": "normal",
            "cells": [],
            "hidden_cells": [],
            "trap_cells": [],
            "reward_cells": [],
            "blocked_cells": [],
            "teleport_cells": [],
            "reveal_cells": [],
        },
        "win_logic": {
            "type": "",
            "target_score": 0,
            "elimination_rules": "",
            "path_completion_rules": "",
            "custom_rules": "",
        },
        "economy": {
            "reward_per_win": 2,
            "entry_fee": 0,
            "participation_reward": 0,
            "bonus_reward": 0,
            "anti_abuse": True,
            "free_access": True,
        },
    }

    def __init__(
        self,
        renderer: Optional[BuilderRenderer] = None,
        validator: Optional[BuilderValidator] = None,
        exporter: Optional[GameExporter] = None,
        draft_manager: Optional[DraftManager] = None,
    ):
        self.renderer = renderer or BuilderRenderer()
        self.validator = validator or BuilderValidator()
        self.exporter = exporter or GameExporter()
        self.draft_manager = draft_manager or DraftManager()

        # Ensure DB tables exist
        self._ensure_tables()
        # Ensure base schema is also initialized (games, users, etc.)
        try:
            from database import init_db
            init_db()
        except Exception:
            logger.debug("Base DB schema init skipped or already done")

    # ------------------------------------------------------------------
    # Database initialization
    # ------------------------------------------------------------------

    def _ensure_tables(self) -> None:
        """Create builder_sessions and game_drafts tables if they don't exist."""
        # Use sync DB for table creation (called from __init__)
        from database.db import get_db
        conn = get_db()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS builder_sessions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      TEXT    NOT NULL UNIQUE,
                    user_id         INTEGER NOT NULL,
                    chat_id         INTEGER NOT NULL,
                    message_id      INTEGER DEFAULT NULL,
                    current_step    TEXT    NOT NULL DEFAULT 'HOME',
                    config_json     TEXT    NOT NULL DEFAULT '{}',
                    status          TEXT    NOT NULL DEFAULT 'active',
                    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
                )
            """)
        except Exception:
            logger.debug("builder_sessions table may already exist")

        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_builder_sessions_session_id ON builder_sessions (session_id)")
        except Exception:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_builder_sessions_user_id ON builder_sessions (user_id)")
        except Exception:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_builder_sessions_status ON builder_sessions (status)")
        except Exception:
            pass

        # Ensure drafts table too - create without FK constraint since
        # user_id here is telegram_id, not internal users.id
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS game_drafts (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL,
                    session_id      TEXT    NOT NULL DEFAULT '',
                    config_json     TEXT    NOT NULL DEFAULT '{}',
                    current_step    TEXT    NOT NULL DEFAULT 'HOME',
                    status          TEXT    NOT NULL DEFAULT 'in_progress',
                    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
                )
            """)
        except Exception:
            logger.debug("game_drafts table may already exist")

        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_game_drafts_user_id ON game_drafts (user_id)")
        except Exception:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_game_drafts_session_id ON game_drafts (session_id)")
        except Exception:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_game_drafts_status ON game_drafts (status)")
        except Exception:
            pass

        if hasattr(self.draft_manager, 'ensure_table'):
            self.draft_manager.ensure_table()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def create_session(self, user_id: int, chat_id: int) -> str:
        """
        Create a new builder session.

        Parameters
        ----------
        user_id : int
            Telegram user ID of the creator.
        chat_id : int
            Telegram chat ID where the builder message lives.

        Returns
        -------
        str
            The new session_id.
        """
        session_id = f"bld_{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow().isoformat()
        config_json = json.dumps(self.DEFAULT_CONFIG, ensure_ascii=False)

        await async_execute(
            """
            INSERT INTO builder_sessions
                (session_id, user_id, chat_id, message_id, current_step, config_json, status, created_at, updated_at)
            VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (session_id, user_id, chat_id, HOME, config_json, "active", now, now),
        )

        logger.info("Builder session created: %s for user %d in chat %d", session_id, user_id, chat_id)
        return session_id

    def get_session(self, session_id: str) -> dict:
        """
        Retrieve a builder session by ID.

        Parameters
        ----------
        session_id : str
            The builder session ID.

        Returns
        -------
        dict
            The full session data.

        Raises
        ------
        BuilderSessionError
            If the session is not found.
        """
        return self._get_session_or_raise(session_id)

    async def set_step(self, session_id: str, step: str) -> dict:
        """
        Move a session to a specific step.

        Parameters
        ----------
        session_id : str
            The builder session ID.
        step : str
            Target step name (must be in STATE_ORDER).

        Returns
        -------
        dict
            Updated session data.

        Raises
        ------
        BuilderSessionError
            If the session is not found or step is invalid.
        """
        if step not in STATE_ORDER:
            raise BuilderSessionError(f"Invalid step: {step}")

        session = self._get_session_or_raise(session_id)
        now = datetime.utcnow().isoformat()

        await async_execute(
            "UPDATE builder_sessions SET current_step = ?, updated_at = ? WHERE session_id = ?",
            (step, now, session_id),
        )

        session["current_step"] = step
        session["updated_at"] = now
        logger.debug("Session %s moved to step %s", session_id, step)
        return session

    def update_config(self, session_id: str, key: str, value: Any) -> dict:
        """
        Update a top-level config field in the session.

        Parameters
        ----------
        session_id : str
            The builder session ID.
        key : str
            Config field name.
        value : Any
            New value (must be JSON-serializable).

        Returns
        -------
        dict
            Updated session data.
        """
        session = self._get_session_or_raise(session_id)
        config = session["config"]
        config[key] = value
        self._save_config(session_id, config)

        session["config"] = config
        return session

    def update_nested_config(self, session_id: str, *path_and_value) -> dict:
        """
        Update a nested config field.

        The last argument is the value; all preceding arguments form the path.

        Example: update_nested_config(sid, "board", "rows", 5)
        sets config["board"]["rows"] = 5

        Parameters
        ----------
        session_id : str
            The builder session ID.
        *path_and_value
            Path components followed by the value to set.

        Returns
        -------
        dict
            Updated session data.
        """
        if len(path_and_value) < 2:
            raise BuilderSessionError("Need at least a path key and a value")

        session = self._get_session_or_raise(session_id)
        config = session["config"]

        path = list(path_and_value[:-1])
        value = path_and_value[-1]

        # Navigate to the parent object
        obj = config
        for key in path[:-1]:
            if key not in obj or not isinstance(obj[key], dict):
                obj[key] = {}
            obj = obj[key]

        # Set the final key
        obj[path[-1]] = value

        self._save_config(session_id, config)
        session["config"] = config
        return session

    def next_step(self, session_id: str) -> dict:
        """
        Move to the next step in STATE_ORDER.

        Returns
        -------
        dict
            Updated session data.
        """
        session = self._get_session_or_raise(session_id)
        current = session["current_step"]

        try:
            idx = STATE_ORDER.index(current)
        except ValueError:
            idx = 0

        next_idx = min(idx + 1, len(STATE_ORDER) - 1)
        return self.set_step(session_id, STATE_ORDER[next_idx])

    def prev_step(self, session_id: str) -> dict:
        """
        Move to the previous step in STATE_ORDER.

        Returns
        -------
        dict
            Updated session data.
        """
        session = self._get_session_or_raise(session_id)
        current = session["current_step"]

        try:
            idx = STATE_ORDER.index(current)
        except ValueError:
            idx = 0

        prev_idx = max(idx - 1, 0)
        return self.set_step(session_id, STATE_ORDER[prev_idx])

    # ------------------------------------------------------------------
    # Draft management
    # ------------------------------------------------------------------

    def save_draft(self, session_id: str) -> str:
        """
        Save current session as a draft.

        Returns
        -------
        str
            The draft_id.
        """
        session = self._get_session_or_raise(session_id)
        config = session["config"]

        # Build session_data dict for DraftManager compatibility
        draft_data = {
            "user_id": session["user_id"],
            "session_id": session_id,
            "config": config,
            "current_step": session["current_step"],
            "status": "in_progress",
        }
        draft_id = self.draft_manager.save_draft(draft_data)
        logger.info("Session %s saved as draft %s", session_id, draft_id)
        return str(draft_id)

    async def load_draft(self, draft_id, user_id: int) -> dict:
        """
        Load a draft into a new session.

        Parameters
        ----------
        draft_id : str or int
            The draft ID to load.
        user_id : int
            The user ID (for ownership check).

        Returns
        -------
        dict
            The new session data with draft config loaded.
        """
        # Convert to int if needed (DraftManager uses int IDs)
        try:
            int_draft_id = int(draft_id)
        except (ValueError, TypeError):
            int_draft_id = draft_id

        draft = self.draft_manager.load_draft(int_draft_id, user_id)
        if draft is None:
            raise BuilderSessionError(f"Draft not found or not owned: {draft_id}")

        # Create a new session and apply draft config
        # We need chat_id – use 0 as placeholder; will be set when message is sent
        session_id = self.create_session(user_id, chat_id=0)

        # Apply draft config and step
        draft_step = draft.get("current_step", draft.get("step", HOME))
        await async_execute(
            "UPDATE builder_sessions SET config_json = ?, current_step = ? WHERE session_id = ?",
            (
                json.dumps(draft.get("config", {}), ensure_ascii=False),
                draft_step,
                session_id,
            ),
        )

        logger.info("Draft %s loaded into session %s for user %d", draft_id, session_id, user_id)
        return self._get_session_or_raise(session_id)

    def list_drafts(self, user_id: int) -> list:
        """
        List user's drafts.

        Returns
        -------
        list
            List of draft summaries.
        """
        return self.draft_manager.list_drafts(user_id)

    def delete_draft(self, draft_id, user_id: int) -> bool:
        """
        Delete a draft.

        Returns
        -------
        bool
            True if deleted.
        """
        # Convert to int if needed (DraftManager uses int IDs)
        try:
            int_draft_id = int(draft_id)
        except (ValueError, TypeError):
            int_draft_id = draft_id
        return self.draft_manager.delete_draft(int_draft_id, user_id)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render_session(self, session_id: str) -> tuple[str, InlineKeyboardMarkup]:
        """
        Render the current builder state as a Telegram message.

        Parameters
        ----------
        session_id : str
            The builder session ID.

        Returns
        -------
        tuple[str, InlineKeyboardMarkup]
            The message text and inline keyboard.
        """
        session = self._get_session_or_raise(session_id)
        return self.renderer.render(session)

    # ------------------------------------------------------------------
    # Callback handling
    # ------------------------------------------------------------------

    def handle_callback(self, session_id: str, user_id: int, action: str) -> dict:
        """
        Handle a builder callback action.

        Action strings follow these patterns:
        - "builder:goto:STEP_NAME"       – Navigate to step
        - "builder:next"                 – Next step
        - "builder:prev"                 – Previous step
        - "builder:save"                 – Save draft
        - "builder:publish"              – Publish game
        - "builder:cancel"               – Cancel session
        - "builder:set:field:value"      – Set config field
        - "builder:game_type:TYPE"       – Set game type
        - "builder:button:add"           – Add button
        - "builder:button:edit:INDEX"    – Edit button
        - "builder:button:delete:INDEX"  – Delete button
        - "builder:button:FIELD:INDEX:VALUE" – Set button field
        - "builder:board:rows:VALUE"     – Set board rows
        - "builder:board:cols:VALUE"     – Set board cols
        - "builder:cell:ROW:COL:FIELD:VALUE" – Set cell property
        - "builder:win_type:TYPE"        – Set win type
        - "builder:econ:FIELD:VALUE"     – Set economy field

        Parameters
        ----------
        session_id : str
            The builder session ID.
        user_id : int
            Telegram user ID of the callback sender.
        action : str
            The callback action string.

        Returns
        -------
        dict
            Updated session data after handling the action.
        """
        session = self._get_session_or_raise(session_id)

        # Verify ownership
        if session["user_id"] != user_id:
            raise BuilderSessionError("You do not own this builder session")

        parts = action.split(":")

        if len(parts) < 2:
            return session

        prefix = parts[0]
        command = parts[1]

        if prefix != "builder":
            return session

        # ---- Navigation ----
        if command == "goto" and len(parts) >= 3:
            step = parts[2]
            return self.set_step(session_id, step)

        elif command == "next":
            return self.next_step(session_id)

        elif command == "prev":
            return self.prev_step(session_id)

        # ---- Save / Publish / Cancel ----
        elif command == "save":
            self.save_draft(session_id)
            return self._get_session_or_raise(session_id)

        elif command == "publish":
            return self.publish_game(session_id)

        elif command == "cancel":
            return self._cancel_session(session_id)

        # ---- Set config field ----
        elif command == "set" and len(parts) >= 3:
            return self._handle_set(session_id, parts[2:])

        # ---- Game type ----
        elif command == "game_type" and len(parts) >= 3:
            game_type = parts[2]
            if game_type in GAME_TYPES:
                return self.update_config(session_id, "game_type", game_type)
            return session

        # ---- Button operations ----
        elif command == "button" and len(parts) >= 3:
            return self._handle_button(session_id, parts[2:])

        # ---- Board operations ----
        elif command == "board" and len(parts) >= 3:
            return self._handle_board(session_id, parts[2:])

        # ---- Cell operations ----
        elif command == "cell" and len(parts) >= 5:
            return self._handle_cell(session_id, parts[2:])

        # ---- Win type ----
        elif command == "win_type" and len(parts) >= 3:
            win_type = parts[2]
            if win_type in WIN_TYPES:
                return self.update_nested_config(session_id, "win_logic", "type", win_type)
            return session

        # ---- Economy operations ----
        elif command == "econ" and len(parts) >= 3:
            return self._handle_econ(session_id, parts[2:])

        return session

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish_game(self, session_id: str) -> dict:
        """
        Validate and publish the game.

        Parameters
        ----------
        session_id : str
            The builder session ID.

        Returns
        -------
        dict
            Result dict with keys: "success", "game_slug", "game_id", "error", "session_data"
        """
        session = self._get_session_or_raise(session_id)
        config = session["config"]

        # Validate first
        validation = self.validator.validate(config)
        if not validation.get("valid", False):
            logger.warning("Publish attempt for session %s failed validation", session_id)
            errors = validation.get("errors", [])
            error_messages = [e if isinstance(e, str) else e.get("message", str(e)) for e in errors]
            return {
                "success": False,
                "game_slug": None,
                "game_id": None,
                "error": "Validation failed: " + "; ".join(error_messages[:3]),
                "session_data": session,
            }

        # Export - pass config and user_id
        user_id = session["user_id"]
        result = self.exporter.export(config, user_id)

        if result.get("success"):
            # Mark session as completed
            now = datetime.utcnow().isoformat()
            await async_execute(
                "UPDATE builder_sessions SET status = ?, updated_at = ? WHERE session_id = ?",
                ("completed", now, session_id),
            )
            slug = result.get("slug", result.get("game_slug", ""))
            logger.info(
                "Game published from session %s: %s",
                session_id, slug,
            )

        result["session_data"] = self._get_session_or_raise(session_id)
        return result

    # ------------------------------------------------------------------
    # Progress calculation
    # ------------------------------------------------------------------

    def _calculate_progress(self, session_data: dict) -> int:
        """
        Calculate completion percentage (0-100) for a session.

        Parameters
        ----------
        session_data : dict
            Full session data with ``config`` key.

        Returns
        -------
        int
            Completion percentage 0-100.
        """
        config = session_data.get("config", {})
        return self.renderer._calculate_progress(config)

    # ------------------------------------------------------------------
    # Internal: session retrieval
    # ------------------------------------------------------------------

    async def _get_session_or_raise(self, session_id: str) -> dict:
        """
        Retrieve a session from the database or raise.

        Parameters
        ----------
        session_id : str
            The builder session ID.

        Returns
        -------
        dict
            Session data dict.

        Raises
        ------
        BuilderSessionError
            If session not found.
        """
        row = await async_fetchone(
            """
            SELECT session_id, user_id, chat_id, message_id, current_step,
                   config_json, status, created_at, updated_at
            FROM builder_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        )

        if row is None:
            raise BuilderSessionError(f"Session not found: {session_id}")

        try:
            config = json.loads(row["config_json"])
        except (json.JSONDecodeError, TypeError):
            config = dict(self.DEFAULT_CONFIG)

        return {
            "session_id": row["session_id"],
            "user_id": row["user_id"],
            "chat_id": row["chat_id"],
            "message_id": row["message_id"],
            "current_step": row["current_step"],
            "config": config,
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ------------------------------------------------------------------
    # Internal: config persistence
    # ------------------------------------------------------------------

    async def _save_config(self, session_id: str, config: dict) -> None:
        """Persist config JSON to the database."""
        now = datetime.utcnow().isoformat()
        config_json = json.dumps(config, ensure_ascii=False)
        await async_execute(
            "UPDATE builder_sessions SET config_json = ?, updated_at = ? WHERE session_id = ?",
            (config_json, now, session_id),
        )

    # ------------------------------------------------------------------
    # Internal: cancel session
    # ------------------------------------------------------------------

    async def _cancel_session(self, session_id: str) -> dict:
        """Mark a session as abandoned."""
        now = datetime.utcnow().isoformat()
        await async_execute(
            "UPDATE builder_sessions SET status = ?, updated_at = ? WHERE session_id = ?",
            ("abandoned", now, session_id),
        )
        logger.info("Session %s cancelled", session_id)
        return self._get_session_or_raise(session_id)

    # ------------------------------------------------------------------
    # Internal: handle "set" actions
    # ------------------------------------------------------------------

    def _handle_set(self, session_id: str, parts: list[str]) -> dict:
        """
        Handle builder:set:FIELD:VALUE actions.

        Special VALUE tokens:
        - "toggle": Toggle a boolean field
        - "inc" / "dec": Increment / decrement a numeric field
        - "prompt": Flag that user input is needed (no-op, waiting for text message)
        - "show": No-op, just refresh the current view
        """
        if len(parts) < 2:
            return self._get_session_or_raise(session_id)

        field = parts[0]
        raw_value = parts[1]
        session = self._get_session_or_raise(session_id)
        config = session["config"]

        # Determine current value for context
        current = config.get(field)

        # Handle special value tokens
        if raw_value == "toggle":
            if isinstance(current, bool):
                new_value = not current
            elif current in ("public", "private", "friends"):
                # Cycle visibility
                cycle = {"public": "private", "private": "friends", "friends": "public"}
                new_value = cycle.get(current, "public")
            elif current in ("open", "invite", "approval"):
                cycle = {"open": "invite", "invite": "approval", "approval": "open"}
                new_value = cycle.get(current, "open")
            elif current in ("normal", "sparse", "dense"):
                cycle = {"normal": "sparse", "sparse": "dense", "dense": "normal"}
                new_value = cycle.get(current, "normal")
            elif current in ("always", "own_turn", "any_turn", "never"):
                cycle = {"always": "own_turn", "own_turn": "any_turn", "any_turn": "never", "never": "always"}
                new_value = cycle.get(current, "always")
            else:
                new_value = not bool(current)
            return self.update_config(session_id, field, new_value)

        elif raw_value == "inc":
            if isinstance(current, int):
                return self.update_config(session_id, field, current + 1)
            elif isinstance(current, float):
                return self.update_config(session_id, field, current + 1.0)
            return session

        elif raw_value == "dec":
            if isinstance(current, int):
                new_val = max(0, current - 1)
                return self.update_config(session_id, field, new_val)
            elif isinstance(current, float):
                new_val = max(0.0, current - 1.0)
                return self.update_config(session_id, field, new_val)
            return session

        elif raw_value == "prompt":
            # Awaiting text input from user – no config change
            return session

        elif raw_value == "show":
            # No-op, just refresh
            return session

        # Try to parse the value into the correct type
        parsed = self._parse_value(field, raw_value, current)
        return self.update_config(session_id, field, parsed)

    def _parse_value(self, field: str, raw_value: str, current: Any) -> Any:
        """Parse a raw string value into the appropriate Python type."""
        # Numeric fields
        numeric_int_fields = {
            "min_players", "max_players", "entry_fee",
            "target_score",
        }
        numeric_float_fields = {
            "reward_per_win", "participation_reward", "bonus_reward",
        }

        if field in numeric_int_fields:
            try:
                return int(raw_value)
            except (ValueError, TypeError):
                return current

        if field in numeric_float_fields:
            try:
                return float(raw_value)
            except (ValueError, TypeError):
                return current

        # Boolean fields
        bool_fields = {
            "spectator_mode", "private_room_support", "anti_abuse",
            "free_access", "board_enabled",
        }
        if field in bool_fields:
            if raw_value.lower() in ("true", "1", "yes", "on"):
                return True
            elif raw_value.lower() in ("false", "0", "no", "off"):
                return False
            return current

        # String fields – return as-is
        return raw_value

    # ------------------------------------------------------------------
    # Internal: handle button actions
    # ------------------------------------------------------------------

    def _handle_button(self, session_id: str, parts: list[str]) -> dict:
        """
        Handle builder:button:* actions.

        Patterns:
        - "add"              – Add a new button
        - "edit:INDEX"       – Navigate to edit button at INDEX (no-op for now, sets context)
        - "delete:INDEX"     – Delete button at INDEX
        - "FIELD:INDEX:VALUE" – Set a field on a button
        """
        if not parts:
            return self._get_session_or_raise(session_id)

        sub = parts[0]
        session = self._get_session_or_raise(session_id)
        config = session["config"]
        buttons = config.get("buttons", [])

        if sub == "add":
            # Add a default button
            btn_index = len(buttons) + 1
            new_button = {
                "id": f"btn_{btn_index}",
                "label": f"Button {btn_index}",
                "emoji": "",
                "action_id": f"action_{btn_index}",
                "effect_type": "",
                "visibility_rule": "always",
                "condition": None,
                "target": None,
                "cooldown": 0,
            }
            buttons.append(new_button)
            config["buttons"] = buttons
            self._save_config(session_id, config)
            return self._get_session_or_raise(session_id)

        elif sub == "delete" and len(parts) >= 2:
            try:
                idx = int(parts[1])
            except (ValueError, IndexError):
                return session
            if 0 <= idx < len(buttons):
                buttons.pop(idx)
                # Re-index IDs
                for i, btn in enumerate(buttons):
                    btn["id"] = f"btn_{i + 1}"
                config["buttons"] = buttons
                self._save_config(session_id, config)
            return self._get_session_or_raise(session_id)

        elif sub == "edit" and len(parts) >= 2:
            # Edit navigation – just go to the step, no config change needed
            # The step renderer will show all buttons with edit options
            return self.set_step(session_id, BUTTON_DESIGN)

        elif sub in EFFECT_TYPES and len(parts) >= 3:
            # Pattern: button:FIELD:INDEX:VALUE  → parts = ["FIELD", "INDEX", "VALUE"]
            # But also: button:EFFECT_TYPE:INDEX:VALUE for effect type setting
            # Actually parts here is ["effect_type", "INDEX", "VALUE"] etc.
            try:
                idx = int(parts[1])
                value = parts[2]
            except (ValueError, IndexError):
                return session
            if 0 <= idx < len(buttons):
                if sub == "label":
                    buttons[idx]["label"] = value
                elif sub == "emoji":
                    buttons[idx]["emoji"] = value
                elif sub == "effect_type":
                    if value in EFFECT_TYPES:
                        buttons[idx]["effect_type"] = value
                elif sub == "cooldown":
                    try:
                        buttons[idx]["cooldown"] = int(value)
                    except ValueError:
                        pass
                elif sub == "visibility_rule":
                    buttons[idx]["visibility_rule"] = value
                config["buttons"] = buttons
                self._save_config(session_id, config)
            return self._get_session_or_raise(session_id)

        # Generic button field setter: "FIELD:INDEX:VALUE"
        if len(parts) >= 3:
            field_name = parts[0]
            try:
                idx = int(parts[1])
                value = parts[2]
            except (ValueError, IndexError):
                return session
            if 0 <= idx < len(buttons):
                if field_name in buttons[idx]:
                    # Type conversion
                    current_val = buttons[idx][field_name]
                    if isinstance(current_val, int):
                        try:
                            buttons[idx][field_name] = int(value)
                        except ValueError:
                            pass
                    elif isinstance(current_val, float):
                        try:
                            buttons[idx][field_name] = float(value)
                        except ValueError:
                            pass
                    else:
                        buttons[idx][field_name] = value
                    config["buttons"] = buttons
                    self._save_config(session_id, config)
            return self._get_session_or_raise(session_id)

        return session

    # ------------------------------------------------------------------
    # Internal: handle board actions
    # ------------------------------------------------------------------

    def _handle_board(self, session_id: str, parts: list[str]) -> dict:
        """
        Handle builder:board:* actions.

        Patterns:
        - "rows:VALUE"    – Set rows (VALUE can be int or "inc"/"dec")
        - "cols:VALUE"    – Set cols (VALUE can be int or "inc"/"dec")
        - "rows:inc" / "rows:dec"
        - "cols:inc" / "cols:dec"
        """
        if len(parts) < 2:
            return self._get_session_or_raise(session_id)

        field = parts[0]  # "rows" or "cols"
        raw_value = parts[1]

        session = self._get_session_or_raise(session_id)
        config = session["config"]
        board = config.get("board", {})

        if field not in ("rows", "cols"):
            return session

        current = board.get(field, 3)

        if raw_value == "inc":
            max_val = 20 if field == "rows" else 8
            new_val = min(current + 1, max_val)
        elif raw_value == "dec":
            new_val = max(current - 1, 1)
        elif raw_value == "show":
            return session
        else:
            try:
                new_val = int(raw_value)
                if field == "cols":
                    new_val = max(1, min(new_val, 8))
                else:
                    new_val = max(1, min(new_val, 20))
            except ValueError:
                return session

        board[field] = new_val

        # Rebuild cells array if size changed
        if field in ("rows", "cols"):
            rows = board.get("rows", 3)
            cols = board.get("cols", 3)
            cells = board.get("cells", [])
            # Resize cells grid
            new_cells = []
            for r in range(rows):
                row = []
                for c in range(cols):
                    if r < len(cells) and c < len(cells[r]):
                        row.append(cells[r][c])
                    else:
                        row.append({"type": "normal"})
                new_cells.append(row)
            board["cells"] = new_cells

        config["board"] = board
        self._save_config(session_id, config)
        return self._get_session_or_raise(session_id)

    # ------------------------------------------------------------------
    # Internal: handle cell actions
    # ------------------------------------------------------------------

    def _handle_cell(self, session_id: str, parts: list[str]) -> dict:
        """
        Handle builder:cell:ROW:COL:FIELD:VALUE actions.
        """
        if len(parts) < 4:
            return self._get_session_or_raise(session_id)

        try:
            row = int(parts[0])
            col = int(parts[1])
            field = parts[2]
            value = parts[3]
        except (ValueError, IndexError):
            return session

        session = self._get_session_or_raise(session_id)
        config = session["config"]
        board = config.get("board", {})
        cells = board.get("cells", [])

        # Ensure cells array is large enough
        rows = board.get("rows", 3)
        cols = board.get("cols", 3)

        while len(cells) < rows:
            cells.append([{"type": "normal"} for _ in range(cols)])
        for r in range(len(cells)):
            while len(cells[r]) < cols:
                cells[r].append({"type": "normal"})

        if 0 <= row < len(cells) and 0 <= col < len(cells[row]):
            cell = cells[row][col]
            if not isinstance(cell, dict):
                cell = {"type": "normal"}
                cells[row][col] = cell

            cell[field] = value

            # Also update the special cell lists
            if field == "type" and value != "normal":
                cell_list_key = f"{value}_cells"
                if cell_list_key in board:
                    special_cells = board[cell_list_key]
                    # Check if not already present
                    exists = any(
                        sc.get("row") == row and sc.get("col") == col
                        for sc in special_cells
                    )
                    if not exists:
                        special_cells.append({"row": row, "col": col})
            elif field == "type" and value == "normal":
                # Remove from all special cell lists
                for list_key in ("hidden_cells", "trap_cells", "reward_cells",
                                 "blocked_cells", "teleport_cells", "reveal_cells"):
                    if list_key in board:
                        board[list_key] = [
                            sc for sc in board[list_key]
                            if not (sc.get("row") == row and sc.get("col") == col)
                        ]

        board["cells"] = cells
        config["board"] = board
        self._save_config(session_id, config)
        return self._get_session_or_raise(session_id)

    # ------------------------------------------------------------------
    # Internal: handle economy actions
    # ------------------------------------------------------------------

    def _handle_econ(self, session_id: str, parts: list[str]) -> dict:
        """
        Handle builder:econ:FIELD:VALUE actions.

        Economy fields: reward_per_win, entry_fee, participation_reward,
                        bonus_reward, anti_abuse, free_access
        """
        if len(parts) < 2:
            return self._get_session_or_raise(session_id)

        field = parts[0]
        raw_value = parts[1]

        session = self._get_session_or_raise(session_id)
        config = session["config"]
        economy = config.get("economy", {})

        if field not in ("reward_per_win", "entry_fee", "participation_reward",
                         "bonus_reward", "anti_abuse", "free_access"):
            return session

        current = economy.get(field)

        if raw_value == "toggle":
            if isinstance(current, bool):
                economy[field] = not current
            else:
                economy[field] = not bool(current)
        elif raw_value == "inc":
            if isinstance(current, (int, float)):
                economy[field] = current + (1 if isinstance(current, int) else 1.0)
        elif raw_value == "dec":
            if isinstance(current, (int, float)):
                economy[field] = max(0, current - (1 if isinstance(current, int) else 1.0))
        elif raw_value == "show":
            return session
        else:
            # Try to parse
            if isinstance(current, bool):
                economy[field] = raw_value.lower() in ("true", "1", "yes", "on")
            elif isinstance(current, int):
                try:
                    economy[field] = int(raw_value)
                except ValueError:
                    pass
            elif isinstance(current, float):
                try:
                    economy[field] = float(raw_value)
                except ValueError:
                    pass
            else:
                economy[field] = raw_value

        config["economy"] = economy
        self._save_config(session_id, config)
        return self._get_session_or_raise(session_id)

    # ------------------------------------------------------------------
    # Message ID management
    # ------------------------------------------------------------------

    async def set_message_id(self, session_id: str, message_id: int) -> dict:
        """
        Store the Telegram message_id for the builder message.

        This is called after the initial message is sent, so subsequent
        edits can target the correct message.
        """
        session = self._get_session_or_raise(session_id)
        now = datetime.utcnow().isoformat()
        await async_execute(
            "UPDATE builder_sessions SET message_id = ?, updated_at = ? WHERE session_id = ?",
            (message_id, now, session_id),
        )
        session["message_id"] = message_id
        return session

    async def set_chat_id(self, session_id: str, chat_id: int) -> dict:
        """
        Update the chat_id for a session (useful after loading a draft).
        """
        session = self._get_session_or_raise(session_id)
        now = datetime.utcnow().isoformat()
        await async_execute(
            "UPDATE builder_sessions SET chat_id = ?, updated_at = ? WHERE session_id = ?",
            (chat_id, now, session_id),
        )
        session["chat_id"] = chat_id
        return session

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def get_active_session_for_user(self, user_id: int) -> Optional[dict]:
        """
        Get the active builder session for a user, if any.

        Returns
        -------
        dict or None
            Session data if an active session exists.
        """
        row = await async_fetchone(
            """
            SELECT session_id, user_id, chat_id, message_id, current_step,
                   config_json, status, created_at, updated_at
            FROM builder_sessions
            WHERE user_id = ? AND status = 'active'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id,),
        )

        if row is None:
            return None

        try:
            config = json.loads(row["config_json"])
        except (json.JSONDecodeError, TypeError):
            config = dict(self.DEFAULT_CONFIG)

        return {
            "session_id": row["session_id"],
            "user_id": row["user_id"],
            "chat_id": row["chat_id"],
            "message_id": row["message_id"],
            "current_step": row["current_step"],
            "config": config,
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def cleanup_abandoned(self, days: int = 7) -> int:
        """
        Mark sessions as abandoned if not updated for the given number of days.

        Returns
        -------
        int
            Number of sessions cleaned up.
        """
        cursor = await async_execute(
            """
            UPDATE builder_sessions
            SET status = 'abandoned', updated_at = datetime('now')
            WHERE status = 'active'
              AND updated_at < datetime('now', ?)
            """,
            (f"-{days} days",),
        )
        cleaned = cursor.rowcount
        if cleaned:
            logger.info("Cleaned up %d abandoned builder sessions", cleaned)
        return cleaned
