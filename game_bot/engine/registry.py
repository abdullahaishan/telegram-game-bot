"""
Game Registry

Manages game registration, versioning, approval workflows, and
integration with the PluginLoader for hot reload.

All database operations go through the ``database.db`` module.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from database import async_execute, async_fetchone, async_fetchall, async_transaction

logger = logging.getLogger(__name__)


class GameRegistry:
    """
    Central registry for games in the platform.

    Provides CRUD operations, approval workflows, soft-delete,
    ownership validation, and hot-reload integration.

    Usage::

        registry = GameRegistry()
        game_id = registry.register_game(
            slug="tic-tac-toe",
            name="Tic Tac Toe",
            creator="alice",
            game_type="board",
            version="1.0.0",
            manifest={...},
            user_id=42,
            file_path="/games/tic-tac-toe",
        )
    """

    # ──────────────────────────────────────────────────────────────
    # Registration
    # ──────────────────────────────────────────────────────────────

    async def register_game(
        self,
        slug: str,
        name: str,
        creator: str,
        game_type: str,
        version: str,
        manifest: dict,
        user_id: int,
        file_path: str,
    ) -> int:
        """
        Register a new game in the database.

        Returns the auto-generated game id.
        Raises ``ValueError`` if a game with the same slug already exists.
        """
        existing = await async_fetchone("SELECT id FROM games WHERE slug = ?", (slug,))
        if existing:
            raise ValueError(f"Game with slug '{slug}' already exists (id={existing['id']})")

        manifest_json = json.dumps(manifest, ensure_ascii=False) if isinstance(manifest, dict) else "{}"
        now = datetime.utcnow().isoformat()

        board_rows = None
        board_cols = None
        board = manifest.get("board", {})
        if isinstance(board, dict):
            board_rows = board.get("rows")
            board_cols = board.get("cols")

        rewards = manifest.get("rewards", {})
        entry_fee = float(rewards.get("entry_fee", 0)) if isinstance(rewards, dict) else 0.0
        win_reward = float(rewards.get("win_reward", 0)) if isinstance(rewards, dict) else 0.0

        min_players = int(manifest.get("min_players", 2))
        max_players = int(manifest.get("max_players", 10))

        cursor = await async_execute(
            """
            INSERT INTO games
                (slug, name, creator, description, version, game_type,
                 min_players, max_players, board_rows, board_cols,
                 turn_based, single_message_only, win_condition,
                 reward_sar, entry_fee_sar, is_approved, is_active,
                 manifest_json, created_at, updated_at)
            VALUES
                (?, ?, ?, ?, ?, ?,
                 ?, ?, ?, ?,
                 ?, ?, ?,
                 ?, ?, ?, ?,
                 ?, ?, ?)
            """,
            (
                slug,
                name,
                creator,
                manifest.get("description", ""),
                version,
                game_type,
                min_players,
                max_players,
                board_rows,
                board_cols,
                1 if manifest.get("mode", "turn_based") != "realtime" else 0,
                0,
                manifest.get("win_type", ""),
                win_reward,
                entry_fee,
                0,  # not approved by default
                1,  # active
                manifest_json,
                now,
                now,
            ),
        )

        game_id = cursor.lastrowid

        # Record ownership
        await async_execute(
            """
            INSERT INTO game_ownership (owner_user_id, game_slug, creator_name, rights_status, created_at, updated_at)
            VALUES (?, ?, ?, 'owned', ?, ?)
            """,
            (user_id, slug, creator, now, now),
        )

        logger.info(
            "Registered game: %s (id=%s, slug=%s, type=%s) by user %s",
            name, game_id, slug, game_type, user_id,
        )
        return game_id

    # ──────────────────────────────────────────────────────────────
    # Update
    # ──────────────────────────────────────────────────────────────

    async def update_game(self, game_id: int, **kwargs: Any) -> bool:
        """
        Update one or more fields on a game record.

        Accepts arbitrary keyword arguments corresponding to column
        names in the ``games`` table.  Returns ``True`` if a row was
        updated, ``False`` otherwise.
        """
        if not kwargs:
            return False

        # Whitelist of allowed update fields
        allowed = {
            "name", "description", "version", "game_type",
            "min_players", "max_players", "board_rows", "board_cols",
            "turn_based", "single_message_only", "win_condition",
            "reward_sar", "entry_fee_sar", "is_approved", "is_active",
            "manifest_json", "creator",
        }

        filtered = {k: v for k, v in kwargs.items() if k in allowed}
        if not filtered:
            logger.warning("update_game: no allowed fields in kwargs")
            return False

        filtered["updated_at"] = datetime.utcnow().isoformat()

        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [game_id]

        cursor = await async_execute(
            f"UPDATE games SET {set_clause} WHERE id = ?",
            tuple(values),
        )

        updated = cursor.rowcount > 0
        if updated:
            logger.info("Updated game id=%s: %s", game_id, list(filtered.keys()))
        else:
            logger.warning("update_game: game id=%s not found", game_id)
        return updated

    # ──────────────────────────────────────────────────────────────
    # Queries
    # ──────────────────────────────────────────────────────────────

    async def get_game_by_slug(self, slug: str) -> Optional[dict]:
        """Return a game dict by slug, or ``None`` if not found."""
        row = await async_fetchone("SELECT * FROM games WHERE slug = ?", (slug,))
        return dict(row) if row else None

    async def get_game_by_id(self, game_id: int) -> Optional[dict]:
        """Return a game dict by id, or ``None`` if not found."""
        row = await async_fetchone("SELECT * FROM games WHERE id = ?", (game_id,))
        return dict(row) if row else None

    async def list_games(self, status: Optional[str] = None) -> list[dict]:
        """
        List all games, optionally filtered by a status.

        Parameters
        ----------
        status : str or None
            One of 'approved', 'pending', 'active', 'inactive', 'deleted',
            or ``None`` for all non-deleted games.
        """
        if status == "approved":
            rows = await async_fetchall(
                "SELECT * FROM games WHERE is_approved = 1 AND is_active = 1 ORDER BY name ASC"
            )
        elif status == "pending":
            rows = await async_fetchall(
                "SELECT * FROM games WHERE is_approved = 0 AND is_active = 1 ORDER BY created_at DESC"
            )
        elif status == "active":
            rows = await async_fetchall(
                "SELECT * FROM games WHERE is_active = 1 ORDER BY name ASC"
            )
        elif status == "inactive":
            rows = await async_fetchall(
                "SELECT * FROM games WHERE is_active = 0 AND is_approved = 1 ORDER BY name ASC"
            )
        elif status == "deleted":
            # We track deletion via is_active = -1 (soft delete marker)
            rows = await async_fetchall(
                "SELECT * FROM games WHERE is_active = -1 ORDER BY updated_at DESC"
            )
        else:
            rows = await async_fetchall(
                "SELECT * FROM games WHERE is_active >= 0 ORDER BY name ASC"
            )
        return [dict(r) for r in rows] if rows else []

    # ──────────────────────────────────────────────────────────────
    # Approval workflow
    # ──────────────────────────────────────────────────────────────

    async def approve_game(self, game_id: int) -> bool:
        """Mark a game as approved."""
        now = datetime.utcnow().isoformat()
        cursor = await async_execute(
            "UPDATE games SET is_approved = 1, updated_at = ? WHERE id = ?",
            (now, game_id),
        )
        updated = cursor.rowcount > 0
        if updated:
            logger.info("Game id=%s approved", game_id)
        return updated

    async def reject_game(self, game_id: int) -> bool:
        """Mark a game as rejected (unapproved + inactive)."""
        now = datetime.utcnow().isoformat()
        cursor = await async_execute(
            "UPDATE games SET is_approved = 0, is_active = 0, updated_at = ? WHERE id = ?",
            (now, game_id),
        )
        updated = cursor.rowcount > 0
        if updated:
            logger.info("Game id=%s rejected", game_id)
        return updated

    async def disable_game(self, game_id: int) -> bool:
        """Mark a game as inactive."""
        now = datetime.utcnow().isoformat()
        cursor = await async_execute(
            "UPDATE games SET is_active = 0, updated_at = ? WHERE id = ?",
            (now, game_id),
        )
        updated = cursor.rowcount > 0
        if updated:
            logger.info("Game id=%s disabled", game_id)
        return updated

    async def enable_game(self, game_id: int) -> bool:
        """Mark a game as active."""
        now = datetime.utcnow().isoformat()
        cursor = await async_execute(
            "UPDATE games SET is_active = 1, updated_at = ? WHERE id = ?",
            (now, game_id),
        )
        updated = cursor.rowcount > 0
        if updated:
            logger.info("Game id=%s enabled", game_id)
        return updated

    # ──────────────────────────────────────────────────────────────
    # Soft delete
    # ──────────────────────────────────────────────────────────────

    async def delete_game(self, game_id: int) -> bool:
        """
        Soft-delete a game (sets is_active = -1).

        The game record is preserved for auditing but won't appear in
        normal queries.
        """
        now = datetime.utcnow().isoformat()
        cursor = await async_execute(
            "UPDATE games SET is_active = -1, updated_at = ? WHERE id = ?",
            (now, game_id),
        )
        updated = cursor.rowcount > 0
        if updated:
            logger.info("Game id=%s soft-deleted", game_id)
        return updated

    # ──────────────────────────────────────────────────────────────
    # Hot reload integration
    # ──────────────────────────────────────────────────────────────

    def hot_reload_game(self, slug: str, plugin_loader: Any) -> bool:
        """
        Reload a specific game in the plugin loader.

        Returns ``True`` on success, ``False`` if the game was not
        found in the loader or the reload failed.
        """
        try:
            result = plugin_loader.reload_game(slug)
            if result is not None:
                logger.info("Hot-reloaded game '%s' in plugin loader", slug)
                return True
            else:
                logger.warning("Hot-reload returned None for '%s'", slug)
                return False
        except Exception as exc:
            logger.error("Hot-reload failed for '%s': %s", slug, exc, exc_info=True)
            return False

    def hot_reload_all(self, plugin_loader: Any) -> int:
        """
        Reload all registered and approved games.

        Returns the count of successfully loaded games.
        """
        games = self.list_games(status="approved")
        count = 0
        for game in games:
            slug = game["slug"]
            try:
                result = plugin_loader.reload_game(slug)
                if result is not None:
                    count += 1
                else:
                    logger.warning("Hot-reload returned None for '%s'", slug)
            except Exception as exc:
                logger.error("Hot-reload failed for '%s': %s", slug, exc)
        logger.info("Hot-reloaded %d/%d games", count, len(games))
        return count

    # ──────────────────────────────────────────────────────────────
    # Statistics
    # ──────────────────────────────────────────────────────────────

    async def get_game_stats(self) -> dict[str, Any]:
        """
        Return registry statistics.

        Keys: total, approved, pending, rejected, active, inactive, deleted.
        """
        total = await async_fetchone("SELECT COUNT(*) as cnt FROM games WHERE is_active >= 0")
        approved = await async_fetchone("SELECT COUNT(*) as cnt FROM games WHERE is_approved = 1 AND is_active >= 0")
        pending = await async_fetchone("SELECT COUNT(*) as cnt FROM games WHERE is_approved = 0 AND is_active = 1")
        rejected = await async_fetchone("SELECT COUNT(*) as cnt FROM games WHERE is_approved = 0 AND is_active = 0")
        active = await async_fetchone("SELECT COUNT(*) as cnt FROM games WHERE is_active = 1")
        inactive = await async_fetchone("SELECT COUNT(*) as cnt FROM games WHERE is_active = 0 AND is_approved = 1")
        deleted = await async_fetchone("SELECT COUNT(*) as cnt FROM games WHERE is_active = -1")

        return {
            "total": total["cnt"] if total else 0,
            "approved": approved["cnt"] if approved else 0,
            "pending": pending["cnt"] if pending else 0,
            "rejected": rejected["cnt"] if rejected else 0,
            "active": active["cnt"] if active else 0,
            "inactive": inactive["cnt"] if inactive else 0,
            "deleted": deleted["cnt"] if deleted else 0,
        }

    # ──────────────────────────────────────────────────────────────
    # Ownership
    # ──────────────────────────────────────────────────────────────

    async def validate_ownership(self, user_id: int, slug: str) -> bool:
        """
        Check if a user owns a game.

        Returns ``True`` if an ownership record exists with an
        ``owned`` or ``licensed`` status.
        """
        row = await async_fetchone(
            "SELECT id FROM game_ownership WHERE owner_user_id = ? AND game_slug = ? AND rights_status IN ('owned', 'licensed')",
            (user_id, slug),
        )
        return row is not None

    async def get_games_by_owner(self, user_id: int) -> list[dict]:
        """Return all games owned by a given user."""
        rows = await async_fetchall(
            """
            SELECT g.*, go.rights_status, go.creator_name
            FROM games g
            JOIN game_ownership go ON g.slug = go.game_slug
            WHERE go.owner_user_id = ? AND g.is_active >= 0
            ORDER BY g.created_at DESC
            """,
            (user_id,),
        )
        return [dict(r) for r in rows] if rows else []

    # ──────────────────────────────────────────────────────────────
    # Versioning & Rollback
    # ──────────────────────────────────────────────────────────────

    async def rollback_game(self, slug: str) -> bool:
        """
        Rollback a game to its previous version if available.

        The registry stores version history in the ``game_versions``
        table.  If a previous version exists, the manifest and metadata
        are restored and the game is hot-reloaded.

        Returns ``True`` on successful rollback, ``False`` otherwise.
        """
        game = self.get_game_by_slug(slug)
        if not game:
            logger.warning("rollback_game: slug '%s' not found", slug)
            return False

        current_version = game.get("version", "")

        # Fetch the most recent previous version
        prev = await async_fetchone(
            """
            SELECT version, manifest_json, name, description, game_type,
                   min_players, max_players, board_rows, board_cols,
                   turn_based, win_condition, reward_sar, entry_fee_sar
            FROM game_versions
            WHERE game_slug = ? AND version != ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (slug, current_version),
        )

        if not prev:
            logger.warning("rollback_game: no previous version found for '%s'", slug)
            return False

        now = datetime.utcnow().isoformat()
        try:
            async with async_transaction():
                await async_execute(
                    """
                    UPDATE games SET
                        version = ?,
                        manifest_json = ?,
                        name = ?,
                        description = ?,
                        game_type = ?,
                        min_players = ?,
                        max_players = ?,
                        board_rows = ?,
                        board_cols = ?,
                        turn_based = ?,
                        win_condition = ?,
                        reward_sar = ?,
                        entry_fee_sar = ?,
                        updated_at = ?
                    WHERE slug = ?
                    """,
                    (
                        prev["version"],
                        prev["manifest_json"],
                        prev["name"],
                        prev["description"],
                        prev["game_type"],
                        prev["min_players"],
                        prev["max_players"],
                        prev["board_rows"],
                        prev["board_cols"],
                        prev["turn_based"],
                        prev["win_condition"],
                        prev["reward_sar"],
                        prev["entry_fee_sar"],
                        now,
                        slug,
                    ),
                )

            logger.info(
                "Rolled back game '%s' from %s to %s",
                slug, current_version, prev["version"],
            )
            return True

        except Exception as exc:
            logger.error("rollback_game failed for '%s': %s", slug, exc, exc_info=True)
            return False

    # ──────────────────────────────────────────────────────────────
    # Version snapshot (called before updates)
    # ──────────────────────────────────────────────────────────────

    async def save_version_snapshot(self, slug: str) -> bool:
        """
        Save the current state of a game as a version snapshot.

        This should be called *before* applying updates so that
        ``rollback_game`` can restore the previous state.
        """
        game = self.get_game_by_slug(slug)
        if not game:
            return False

        now = datetime.utcnow().isoformat()
        try:
            await async_execute(
                """
                INSERT INTO game_versions
                    (game_slug, version, manifest_json, name, description,
                     game_type, min_players, max_players, board_rows, board_cols,
                     turn_based, win_condition, reward_sar, entry_fee_sar,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slug,
                    game["version"],
                    game.get("manifest_json", "{}"),
                    game["name"],
                    game.get("description", ""),
                    game["game_type"],
                    game.get("min_players", 2),
                    game.get("max_players", 10),
                    game.get("board_rows"),
                    game.get("board_cols"),
                    game.get("turn_based", 1),
                    game.get("win_condition", ""),
                    game.get("reward_sar", 0),
                    game.get("entry_fee_sar", 0),
                    now,
                ),
            )
            return True
        except Exception as exc:
            # Table may not exist yet – create it lazily
            if "no such table" in str(exc).lower():
                self._ensure_versions_table()
                return self.save_version_snapshot(slug)
            logger.error("save_version_snapshot failed for '%s': %s", slug, exc)
            return False

    @staticmethod
    async def _ensure_versions_table() -> None:
        """Create the game_versions table if it doesn't exist."""
        await async_execute("""
            CREATE TABLE IF NOT EXISTS game_versions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                game_slug       TEXT    NOT NULL,
                version         TEXT    NOT NULL,
                manifest_json   TEXT    DEFAULT '{}',
                name            TEXT    NOT NULL DEFAULT '',
                description     TEXT    DEFAULT '',
                game_type       TEXT    NOT NULL DEFAULT 'board',
                min_players     INTEGER NOT NULL DEFAULT 2,
                max_players     INTEGER NOT NULL DEFAULT 10,
                board_rows      INTEGER DEFAULT NULL,
                board_cols      INTEGER DEFAULT NULL,
                turn_based      INTEGER NOT NULL DEFAULT 1,
                win_condition   TEXT    DEFAULT '',
                reward_sar      REAL    NOT NULL DEFAULT 0.0,
                entry_fee_sar   REAL    NOT NULL DEFAULT 0.0,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await async_execute("""
            CREATE INDEX IF NOT EXISTS idx_game_versions_slug
            ON game_versions (game_slug)
        """)
        await async_execute("""
            CREATE INDEX IF NOT EXISTS idx_game_versions_slug_version
            ON game_versions (game_slug, version)
        """)
        logger.info("game_versions table ensured")
