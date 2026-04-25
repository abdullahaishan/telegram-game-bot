"""
Draft Manager

Saves, loads, lists, and deletes builder drafts. Drafts represent
work-in-progress game configurations that a user can resume later.

Drafts are stored in the ``game_drafts`` table with columns:
    id, user_id, session_id, config_json, current_step, status, created_at, updated_at
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from database import async_execute, async_fetchone, async_fetchall, async_transaction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

DRAFTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS game_drafts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    session_id      TEXT    NOT NULL DEFAULT '',
    config_json     TEXT    NOT NULL DEFAULT '{}',
    current_step    TEXT    NOT NULL DEFAULT 'HOME',
    status          TEXT    NOT NULL DEFAULT 'in_progress',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_game_drafts_user_id    ON game_drafts (user_id);
CREATE INDEX IF NOT EXISTS idx_game_drafts_session_id ON game_drafts (session_id);
CREATE INDEX IF NOT EXISTS idx_game_drafts_status     ON game_drafts (status);
"""

# Track whether the table has been ensured
_table_ensured = False


async def _ensure_table() -> None:
    """Create the game_drafts table if it does not exist."""
    global _table_ensured
    if _table_ensured:
        return
    try:
        from database.db import get_db
        get_db().executescript(DRAFTS_TABLE_SQL)
        _table_ensured = True
        logger.debug("Ensured game_drafts table exists.")
    except Exception:
        logger.exception("Failed to create game_drafts table")
        raise


# ---------------------------------------------------------------------------
# DraftManager
# ---------------------------------------------------------------------------

class DraftManager:
    """
    Manages builder draft persistence.

    Drafts allow users to save their work-in-progress game configurations
    and resume them later. Each draft is associated with a user and
    identified by a unique auto-incremented ID.

    Usage::

        dm = DraftManager()
        draft_id = dm.save_draft(session_data)
        draft = dm.load_draft(draft_id, user_id=42)
        drafts = dm.list_drafts(user_id=42)
        dm.delete_draft(draft_id, user_id=42)
    """

    async def save_draft(self, session_data: Dict[str, Any]) -> int:
        """
        Save or overwrite a draft. Returns the draft ID.

        If ``session_data`` contains an ``id`` key, the existing draft
        with that ID is updated (provided the user_id matches).
        Otherwise, a new draft is created.

        Parameters
        ----------
        session_data : dict
            Must include ``user_id``. May include ``session_id``,
            ``config`` (dict), ``current_step``, ``status``, and ``id``
            (for updates).

        Returns
        -------
        int
            The draft ID (new or existing).
        """
        _ensure_table()

        user_id = session_data.get("user_id")
        if user_id is None:
            raise ValueError("session_data must include 'user_id'")

        draft_id = session_data.get("id")
        session_id = session_data.get("session_id", "")
        config = session_data.get("config", {})
        if not isinstance(config, dict):
            config = {}
        config_json = json.dumps(config, ensure_ascii=False)
        current_step = session_data.get("current_step", "basics")
        status = session_data.get("status", "in_progress")

        if draft_id is not None:
            # Try to update existing draft
            try:
                draft_id = int(draft_id)
            except (ValueError, TypeError):
                draft_id = None

        if draft_id is not None:
            # Verify ownership
            existing = await async_fetchone(
                "SELECT user_id FROM game_drafts WHERE id = ?",
                (draft_id,),
            )
            if existing is not None and existing["user_id"] == user_id:
                await async_execute(
                    """UPDATE game_drafts
                       SET session_id = ?, config_json = ?, current_step = ?,
                           status = ?, updated_at = datetime('now')
                       WHERE id = ?""",
                    (session_id, config_json, current_step, status, draft_id),
                )
                logger.info("Updated draft id=%d for user %d", draft_id, user_id)
                return draft_id
            elif existing is not None:
                # Ownership mismatch — create a new draft instead
                logger.warning(
                    "User %d attempted to update draft id=%d owned by user %d. Creating new draft.",
                    user_id, draft_id, existing["user_id"],
                )
                draft_id = None

        # Insert new draft
        cursor = await async_execute(
            """INSERT INTO game_drafts
               (user_id, session_id, config_json, current_step, status)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, session_id, config_json, current_step, status),
        )
        new_id = cursor.lastrowid
        logger.info("Created draft id=%d for user %d", new_id, user_id)
        return new_id

    async def load_draft(self, draft_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Load a draft by ID, validating ownership.

        Parameters
        ----------
        draft_id : int
            The draft ID to load.
        user_id : int
            The user requesting the draft (ownership check).

        Returns
        -------
        dict or None
            The draft data, or ``None`` if not found or ownership mismatch.
        """
        _ensure_table()

        row = await async_fetchone(
            "SELECT * FROM game_drafts WHERE id = ?",
            (draft_id,),
        )
        if row is None:
            logger.debug("Draft id=%d not found.", draft_id)
            return None

        if row["user_id"] != user_id:
            logger.warning(
                "User %d attempted to load draft id=%d owned by user %d.",
                user_id, draft_id, row["user_id"],
            )
            return None

        config = {}
        try:
            config = json.loads(row["config_json"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid config_json in draft id=%d", draft_id)

        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "session_id": row["session_id"],
            "config": config,
            "current_step": row["current_step"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def list_drafts(self, user_id: int) -> List[Dict[str, Any]]:
        """
        List all drafts for a user.

        Parameters
        ----------
        user_id : int
            The user whose drafts to list.

        Returns
        -------
        list[dict]
            List of draft data dicts, ordered by most recently updated.
        """
        _ensure_table()

        rows = await async_fetchall(
            "SELECT * FROM game_drafts WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )

        drafts = []
        for row in rows:
            config = {}
            try:
                config = json.loads(row["config_json"])
            except (json.JSONDecodeError, TypeError):
                pass

            drafts.append({
                "id": row["id"],
                "user_id": row["user_id"],
                "session_id": row["session_id"],
                "config": config,
                "current_step": row["current_step"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })

        return drafts

    async def delete_draft(self, draft_id: int, user_id: int) -> bool:
        """
        Delete a draft, validating ownership first.

        Parameters
        ----------
        draft_id : int
            The draft ID to delete.
        user_id : int
            The user requesting deletion (ownership check).

        Returns
        -------
        bool
            ``True`` if the draft was deleted, ``False`` if not found or
            ownership mismatch.
        """
        _ensure_table()

        existing = await async_fetchone(
            "SELECT user_id FROM game_drafts WHERE id = ?",
            (draft_id,),
        )
        if existing is None:
            logger.debug("Draft id=%d not found for deletion.", draft_id)
            return False

        if existing["user_id"] != user_id:
            logger.warning(
                "User %d attempted to delete draft id=%d owned by user %d.",
                user_id, draft_id, existing["user_id"],
            )
            return False

        await async_execute(
            "DELETE FROM game_drafts WHERE id = ?",
            (draft_id,),
        )
        logger.info("Deleted draft id=%d (user %d)", draft_id, user_id)
        return True

    async def update_draft(self, draft_id: int, updates: Dict[str, Any]) -> bool:
        """
        Update specific fields of a draft.

        Parameters
        ----------
        draft_id : int
            The draft ID to update.
        updates : dict
            Fields to update. Supported keys: ``session_id``, ``config``,
            ``current_step``, ``status``. The ``config`` dict will be
            serialized to JSON. Must include ``user_id`` for ownership check.

        Returns
        -------
        bool
            ``True`` if the draft was updated, ``False`` if not found or
            ownership mismatch.
        """
        _ensure_table()

        user_id = updates.get("user_id")
        if user_id is None:
            raise ValueError("updates must include 'user_id' for ownership check")

        # Verify ownership
        existing = await async_fetchone(
            "SELECT user_id FROM game_drafts WHERE id = ?",
            (draft_id,),
        )
        if existing is None:
            logger.debug("Draft id=%d not found for update.", draft_id)
            return False

        if existing["user_id"] != user_id:
            logger.warning(
                "User %d attempted to update draft id=%d owned by user %d.",
                user_id, draft_id, existing["user_id"],
            )
            return False

        # Build SET clause dynamically
        set_parts = []
        params = []

        if "session_id" in updates:
            set_parts.append("session_id = ?")
            params.append(updates["session_id"])

        if "config" in updates:
            config = updates["config"]
            if not isinstance(config, dict):
                config = {}
            set_parts.append("config_json = ?")
            params.append(json.dumps(config, ensure_ascii=False))

        if "current_step" in updates:
            set_parts.append("current_step = ?")
            params.append(updates["current_step"])

        if "status" in updates:
            set_parts.append("status = ?")
            params.append(updates["status"])

        if not set_parts:
            logger.debug("No fields to update for draft id=%d", draft_id)
            return True  # Nothing to update, but not an error

        # Always update the timestamp
        set_parts.append("updated_at = datetime('now')")

        params.append(draft_id)

        sql = f"UPDATE game_drafts SET {', '.join(set_parts)} WHERE id = ?"
        await async_execute(sql, tuple(params))

        logger.info("Updated draft id=%d (fields: %s)", draft_id, ", ".join(updates.keys()))
        return True
