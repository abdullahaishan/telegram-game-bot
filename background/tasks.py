"""
Background task system for the Telegram Multiplayer Game Platform.

Handles:
- Promotion rotation
- Session cleanup
- Reward distribution
- Expired game removal
- Queue management
- Log cleanup
- Stale room cleanup
- Expired reward cleanup
- Builder draft cleanup
- Builder session cleanup
- Invalid registry cleanup
- Plugin cache refresh
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta

from database import async_execute, async_fetchall, async_fetchone, async_transaction
from config import (
    PROMOTION_MAX_ACTIVE,
    BACKGROUND_INTERVAL_SECONDS,
    STALE_ROOM_TIMEOUT_SECONDS,
    SESSION_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


class BackgroundTaskManager:
    """Manages all background tasks for the platform."""

    def __init__(self):
        self._running = False
        self._tasks = []
        self._game_bot = None
        self._admin_bot = None

    async def set_bots(self, game_bot, admin_bot):
        """Set bot instances for sending notifications."""
        self._game_bot = game_bot
        self._admin_bot = admin_bot

    async def start(self):
        """Start all background tasks."""
        if self._running:
            return
        self._running = True
        logger.info("BackgroundTaskManager starting...")

        self._tasks = [
            asyncio.create_task(self._promotion_rotation_loop(), name="promotion_rotation"),
            asyncio.create_task(self._session_cleanup_loop(), name="session_cleanup"),
            asyncio.create_task(self._stale_room_cleanup_loop(), name="stale_room_cleanup"),
            asyncio.create_task(self._expired_game_cleanup_loop(), name="expired_game_cleanup"),
            asyncio.create_task(self._queue_management_loop(), name="queue_management"),
            asyncio.create_task(self._log_cleanup_loop(), name="log_cleanup"),
            asyncio.create_task(self._expired_reward_cleanup_loop(), name="expired_reward_cleanup"),
            asyncio.create_task(self._promotion_expiry_loop(), name="promotion_expiry"),
            asyncio.create_task(self._featured_slot_expiry_loop(), name="featured_slot_expiry"),
            asyncio.create_task(self._builder_session_cleanup_loop(), name="builder_session_cleanup"),
            asyncio.create_task(self._draft_cleanup_loop(), name="draft_cleanup"),
            asyncio.create_task(self._registry_cleanup_loop(), name="registry_cleanup"),
        ]

        logger.info(f"Started {len(self._tasks)} background tasks")

    async def stop(self):
        """Stop all background tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("BackgroundTaskManager stopped")

    # ──────────────────────────────────────────────
    # PROMOTION ROTATION
    # ──────────────────────────────────────────────

    async def _promotion_rotation_loop(self):
        """Rotate promotions: activate queued ones when slots are available."""
        while self._running:
            try:
                await self._rotate_promotions()
            except Exception as e:
                logger.error(f"Promotion rotation error: {e}")
            await asyncio.sleep(BACKGROUND_INTERVAL_SECONDS * 2)

    async def _rotate_promotions(self):
        """Check for available promotion slots and activate queued promotions."""
        now = datetime.utcnow().isoformat()

        # Count currently active promotions
        active_count = len(await async_fetchall(
            "SELECT id FROM promotions WHERE status = 'active' AND expires_at > ?",
            (now,)
        ))

        if active_count >= PROMOTION_MAX_ACTIVE:
            return

        # Get next queued promotion(s) ordered by position
        slots_available = PROMOTION_MAX_ACTIVE - active_count
        queued = await async_fetchall(
            """SELECT pq.promotion_id, p.user_id, p.channel_link, p.price_sar,
                      p.duration_hours, p.created_at
               FROM promotion_queue pq
               JOIN promotions p ON p.id = pq.promotion_id
               WHERE pq.status = 'queued'
               ORDER BY pq.position ASC
               LIMIT ?""",
            (slots_available,)
        )

        for promo in queued:
            promo_id = promo["promotion_id"]
            duration_hours = promo["duration_hours"]
            expires_at = (datetime.utcnow() + timedelta(hours=duration_hours)).isoformat()

            async with async_transaction():
                await async_execute(
                    "UPDATE promotions SET status = 'active', started_at = ?, expires_at = ? WHERE id = ?",
                    (now, expires_at, promo_id)
                )
                await async_execute(
                    "UPDATE promotion_queue SET status = 'active' WHERE promotion_id = ?",
                    (promo_id,)
                )

            logger.info(f"Promotion {promo_id} activated, expires at {expires_at}")

    # ──────────────────────────────────────────────
    # PROMOTION EXPIRY
    # ──────────────────────────────────────────────

    async def _promotion_expiry_loop(self):
        """Auto-expire promotions that have passed their expiration time."""
        while self._running:
            try:
                await self._expire_promotions()
            except Exception as e:
                logger.error(f"Promotion expiry error: {e}")
            await asyncio.sleep(BACKGROUND_INTERVAL_SECONDS * 3)

    async def _expire_promotions(self):
        """Mark expired promotions and remove from queue."""
        now = datetime.utcnow().isoformat()

        expired = await async_fetchall(
            "SELECT id FROM promotions WHERE status = 'active' AND expires_at <= ?",
            (now,)
        )

        for promo in expired:
            promo_id = promo["id"]
            async with async_transaction():
                await async_execute(
                    "UPDATE promotions SET status = 'expired' WHERE id = ?",
                    (promo_id,)
                )
                await async_execute(
                    "UPDATE promotion_queue SET status = 'expired' WHERE promotion_id = ?",
                    (promo_id,)
                )
            logger.info(f"Promotion {promo_id} expired")

    # ──────────────────────────────────────────────
    # SESSION CLEANUP
    # ──────────────────────────────────────────────

    async def _session_cleanup_loop(self):
        """Clean up sessions that have timed out."""
        while self._running:
            try:
                await self._cleanup_sessions()
            except Exception as e:
                logger.error(f"Session cleanup error: {e}")
            await asyncio.sleep(BACKGROUND_INTERVAL_SECONDS)

    async def _cleanup_sessions(self):
        """End sessions that exceeded the timeout."""
        timeout = (datetime.utcnow() - timedelta(seconds=SESSION_TIMEOUT_SECONDS)).isoformat()

        stale = await async_fetchall(
            """SELECT id FROM game_sessions
               WHERE status = 'active'
               AND started_at < ?
               AND started_at IS NOT NULL""",
            (timeout,)
        )

        for session in stale:
            session_id = session["id"]
            async with async_transaction():
                await async_execute(
                    "UPDATE game_sessions SET status = 'completed', ended_at = ? WHERE id = ?",
                    (datetime.utcnow().isoformat(), session_id)
                )
                await async_execute(
                    "UPDATE game_players SET is_alive = 0 WHERE session_id = ?",
                    (session_id,)
                )
            logger.info(f"Session {session_id} cleaned up (timeout)")

    # ──────────────────────────────────────────────
    # STALE ROOM CLEANUP
    # ──────────────────────────────────────────────

    async def _stale_room_cleanup_loop(self):
        """Clean up waiting rooms that have been idle too long."""
        while self._running:
            try:
                await self._cleanup_stale_rooms()
            except Exception as e:
                logger.error(f"Stale room cleanup error: {e}")
            await asyncio.sleep(BACKGROUND_INTERVAL_SECONDS)

    async def _cleanup_stale_rooms(self):
        """Cancel waiting rooms older than STALE_ROOM_TIMEOUT."""
        cutoff = (datetime.utcnow() - timedelta(seconds=STALE_ROOM_TIMEOUT_SECONDS)).isoformat()

        stale_rooms = await async_fetchall(
            """SELECT id FROM game_sessions
               WHERE status = 'waiting'
               AND created_at < ?""",
            (cutoff,)
        )

        for room in stale_rooms:
            session_id = room["id"]

            # Refund entry fees if any
            players = await async_fetchall(
                "SELECT user_id FROM game_players WHERE session_id = ?",
                (session_id,)
            )

            session_data = await async_fetchone(
                "SELECT entry_fee FROM game_sessions WHERE id = ?",
                (session_id,)
            )

            entry_fee = session_data["entry_fee"] if session_data else 0

            async with async_transaction():
                # Refund each player
                if entry_fee and entry_fee > 0:
                    for player in players:
                        user_id = player["user_id"]
                        await async_execute(
                            "UPDATE wallets SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
                            (entry_fee, datetime.utcnow().isoformat(), user_id)
                        )
                        await async_execute(
                            """INSERT INTO transactions (user_id, type, amount, description, reference_id, created_at)
                               VALUES (?, 'refund', ?, 'Stale room refund', ?, ?)""",
                            (user_id, entry_fee, str(session_id), datetime.utcnow().isoformat())
                        )

                await async_execute(
                    "UPDATE game_sessions SET status = 'cancelled', ended_at = ? WHERE id = ?",
                    (datetime.utcnow().isoformat(), session_id)
                )

            logger.info(f"Stale room {session_id} cancelled with refunds")

    # ──────────────────────────────────────────────
    # EXPIRED GAME CLEANUP
    # ──────────────────────────────────────────────

    async def _expired_game_cleanup_loop(self):
        """Remove very old completed/cancelled sessions from active tracking."""
        while self._running:
            try:
                await self._cleanup_expired_games()
            except Exception as e:
                logger.error(f"Expired game cleanup error: {e}")
            await asyncio.sleep(BACKGROUND_INTERVAL_SECONDS * 10)

    async def _cleanup_expired_games(self):
        """Purge completed/cancelled sessions older than 7 days."""
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()

        result = await async_execute(
            """DELETE FROM game_actions
               WHERE session_id IN (
                   SELECT id FROM game_sessions
                   WHERE status IN ('completed', 'cancelled')
                   AND ended_at < ?
               )""",
            (cutoff,)
        )

        result = await async_execute(
            """DELETE FROM game_players
               WHERE session_id IN (
                   SELECT id FROM game_sessions
                   WHERE status IN ('completed', 'cancelled')
                   AND ended_at < ?
               )""",
            (cutoff,)
        )

        result = await async_execute(
            """DELETE FROM game_sessions
               WHERE status IN ('completed', 'cancelled')
               AND ended_at < ?""",
            (cutoff,)
        )

        if result and result.rowcount > 0:
            logger.info(f"Purged {result.rowcount} old game sessions")

    # ──────────────────────────────────────────────
    # QUEUE MANAGEMENT
    # ──────────────────────────────────────────────

    async def _queue_management_loop(self):
        """Maintain promotion queue ordering and cleanup."""
        while self._running:
            try:
                await self._manage_queue()
            except Exception as e:
                logger.error(f"Queue management error: {e}")
            await asyncio.sleep(BACKGROUND_INTERVAL_SECONDS * 2)

    async def _manage_queue(self):
        """Re-index queue positions and remove orphaned entries."""
        # Get all queued entries ordered by current position
        queued = await async_fetchall(
            """SELECT pq.id, pq.promotion_id, pq.position
               FROM promotion_queue pq
               JOIN promotions p ON p.id = pq.promotion_id
               WHERE pq.status = 'queued'
               ORDER BY pq.position ASC"""
        )

        # Re-index positions to be sequential
        async with async_transaction():
            for idx, entry in enumerate(queued):
                new_position = idx + 1
                if entry["position"] != new_position:
                    await async_execute(
                        "UPDATE promotion_queue SET position = ? WHERE id = ?",
                        (new_position, entry["id"])
                    )

        # Remove queue entries for promotions that no longer exist or are expired/cancelled
        await async_execute(
            """DELETE FROM promotion_queue
               WHERE promotion_id NOT IN (
                   SELECT id FROM promotions WHERE status IN ('active', 'pending', 'queued')
               )"""
        )

        # Assign queue positions to promotions that are pending but have no queue entry
        pending = await async_fetchall(
            """SELECT id FROM promotions
               WHERE status = 'pending'
               AND id NOT IN (SELECT promotion_id FROM promotion_queue)"""
        )

        if pending:
            max_pos = await async_fetchone(
                "SELECT COALESCE(MAX(position), 0) as max_pos FROM promotion_queue WHERE status = 'queued'"
            )
            max_pos = max_pos["max_pos"] if max_pos else 0

            async with async_transaction():
                for promo in pending:
                    max_pos += 1
                    await async_execute(
                        "UPDATE promotions SET status = 'queued' WHERE id = ?",
                        (promo["id"],)
                    )
                    await async_execute(
                        "INSERT INTO promotion_queue (promotion_id, position, status, created_at) VALUES (?, ?, 'queued', ?)",
                        (promo["id"], max_pos, datetime.utcnow().isoformat())
                    )

    # ──────────────────────────────────────────────
    # LOG CLEANUP
    # ──────────────────────────────────────────────

    async def _log_cleanup_loop(self):
        """Clean up old admin logs and expired sessions."""
        while self._running:
            try:
                await self._cleanup_logs()
            except Exception as e:
                logger.error(f"Log cleanup error: {e}")
            await asyncio.sleep(BACKGROUND_INTERVAL_SECONDS * 60)  # Every hour

    async def _cleanup_logs(self):
        """Remove admin logs older than 90 days."""
        cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()

        result = await async_execute(
            "DELETE FROM admin_logs WHERE created_at < ?",
            (cutoff,)
        )

        if result and result.rowcount > 0:
            logger.info(f"Purged {result.rowcount} old admin logs")

        # Also clean up expired user sessions
        now = datetime.utcnow().isoformat()
        result = await async_execute(
            "DELETE FROM sessions WHERE expires_at < ?",
            (now,)
        )

        if result and result.rowcount > 0:
            logger.info(f"Purged {result.rowcount} expired user sessions")

    # ──────────────────────────────────────────────
    # EXPIRED REWARD CLEANUP
    # ──────────────────────────────────────────────

    async def _expired_reward_cleanup_loop(self):
        """Clean up old reward claim records."""
        while self._running:
            try:
                await self._cleanup_expired_rewards()
            except Exception as e:
                logger.error(f"Expired reward cleanup error: {e}")
            await asyncio.sleep(BACKGROUND_INTERVAL_SECONDS * 30)

    async def _cleanup_expired_rewards(self):
        """Remove reward claims older than 30 days."""
        cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()

        result = await async_execute(
            "DELETE FROM reward_claims WHERE created_at < ?",
            (cutoff,)
        )

        if result and result.rowcount > 0:
            logger.info(f"Purged {result.rowcount} old reward claims")

    # ──────────────────────────────────────────────
    # FEATURED SLOT EXPIRY
    # ──────────────────────────────────────────────

    async def _featured_slot_expiry_loop(self):
        """Expire featured slots that have passed their duration."""
        while self._running:
            try:
                await self._expire_featured_slots()
            except Exception as e:
                logger.error(f"Featured slot expiry error: {e}")
            await asyncio.sleep(BACKGROUND_INTERVAL_SECONDS * 5)

    async def _expire_featured_slots(self):
        """Expire featured slots and profile premium status."""
        now = datetime.utcnow().isoformat()

        # Expire featured slots
        featured = await async_fetchall(
            """SELECT id, user_id FROM owned_features
               WHERE feature_type = 'featured_slot'
               AND expires_at IS NOT NULL
               AND expires_at <= ?""",
            (now,)
        )

        for feat in featured:
            async with async_transaction():
                await async_execute(
                    "DELETE FROM owned_features WHERE id = ?",
                    (feat["id"],)
                )
                # Reset profile featured status
                await async_execute(
                    "UPDATE profiles SET featured_until = NULL WHERE user_id = ? AND featured_until <= ?",
                    (feat["user_id"], now)
                )
            logger.info(f"Featured slot expired for user {feat['user_id']}")

        # Expire premium profiles
        premium = await async_fetchall(
            """SELECT user_id FROM profiles
               WHERE is_premium = 1
               AND featured_until IS NOT NULL
               AND featured_until <= ?""",
            (now,)
        )

        for p in premium:
            await async_execute(
                "UPDATE profiles SET is_premium = 0, featured_until = NULL WHERE user_id = ?",
                (p["user_id"],)
            )
            logger.info(f"Premium profile expired for user {p['user_id']}")

        # Expire private room features
        private_rooms = await async_fetchall(
            """SELECT id, user_id FROM owned_features
               WHERE feature_type = 'private_room'
               AND expires_at IS NOT NULL
               AND expires_at <= ?""",
            (now,)
        )

        for pr in private_rooms:
            await async_execute(
                "DELETE FROM owned_features WHERE id = ?",
                (pr["id"],)
            )
            logger.info(f"Private room feature expired for user {pr['user_id']}")

    # ──────────────────────────────────────────────
    # BUILDER SESSION CLEANUP
    # ──────────────────────────────────────────────

    async def _builder_session_cleanup_loop(self):
        """Clean up abandoned builder sessions."""
        while self._running:
            try:
                await self._cleanup_builder_sessions()
            except Exception as e:
                logger.error(f"Builder session cleanup error: {e}")
            await asyncio.sleep(BACKGROUND_INTERVAL_SECONDS * 5)

    async def _cleanup_builder_sessions(self):
        """Mark builder sessions as abandoned if older than 24 hours."""
        cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()

        result = await async_execute(
            "UPDATE builder_sessions SET status = 'abandoned' WHERE status = 'active' AND updated_at < ?",
            (cutoff,)
        )

        if result and result.rowcount > 0:
            logger.info(f"Marked {result.rowcount} builder sessions as abandoned")

    # ──────────────────────────────────────────────
    # DRAFT CLEANUP
    # ──────────────────────────────────────────────

    async def _draft_cleanup_loop(self):
        """Clean up old game drafts."""
        while self._running:
            try:
                await self._cleanup_drafts()
            except Exception as e:
                logger.error(f"Draft cleanup error: {e}")
            await asyncio.sleep(BACKGROUND_INTERVAL_SECONDS * 30)

    async def _cleanup_drafts(self):
        """Remove drafts older than 30 days."""
        cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()

        result = await async_execute(
            "DELETE FROM game_drafts WHERE status = 'in_progress' AND updated_at < ?",
            (cutoff,)
        )

        if result and result.rowcount > 0:
            logger.info(f"Purged {result.rowcount} old game drafts")

    # ──────────────────────────────────────────────
    # REGISTRY CLEANUP
    # ──────────────────────────────────────────────

    async def _registry_cleanup_loop(self):
        """Clean up invalid registry entries."""
        while self._running:
            try:
                await self._cleanup_registry()
            except Exception as e:
                logger.error(f"Registry cleanup error: {e}")
            await asyncio.sleep(BACKGROUND_INTERVAL_SECONDS * 10)

    async def _cleanup_registry(self):
        """Remove registry entries for games that no longer have files."""
        import os
        from config import GAMES_DIR

        registry_entries = await async_fetchall(
            "SELECT id, slug, file_path FROM games WHERE is_active = 1"
        )

        cleaned = 0
        for entry in registry_entries:
            slug = entry["slug"]
            game_dir = os.path.join(str(GAMES_DIR), slug)
            manifest_path = os.path.join(game_dir, "manifest.json")
            logic_path = os.path.join(game_dir, "logic.py")

            if not os.path.isdir(game_dir) or not os.path.isfile(manifest_path) or not os.path.isfile(logic_path):
                await async_execute(
                    "UPDATE games SET is_active = 0 WHERE id = ?",
                    (entry["id"],)
                )
                cleaned += 1
                logger.warning(f"Registry entry '{slug}' marked invalid - files missing")

        if cleaned > 0:
            logger.info(f"Marked {cleaned} invalid registry entries")
