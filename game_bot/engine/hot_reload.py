"""
Hot Reload System

Safely reloads game plugins without restarting the bot process.

The HotReloader:
  1. Saves a snapshot of the current game state in the registry before
     each reload attempt.
  2. Calls ``plugin_loader.reload_game(slug)`` to load the new version.
  3. If loading fails, rolls back to the previous version automatically.
  4. Logs every reload attempt with outcome.
  5. Tracks full reload history in memory for diagnostics.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from game_bot.engine.registry import GameRegistry

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════

class ReloadStatus(str, Enum):
    """Outcome of a reload attempt."""
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    VALIDATION_FAILED = "validation_failed"
    NOT_FOUND = "not_found"


@dataclass
class ReloadRecord:
    """Single reload history entry."""
    slug: str
    status: ReloadStatus
    timestamp: float = field(default_factory=time.time)
    error_message: str = ""
    previous_version: str = ""
    new_version: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "status": self.status.value,
            "timestamp": self.timestamp,
            "timestamp_iso": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
            "error_message": self.error_message,
            "previous_version": self.previous_version,
            "new_version": self.new_version,
            "duration_ms": self.duration_ms,
        }


# ═══════════════════════════════════════════════════════════════════════
# HotReloader
# ═══════════════════════════════════════════════════════════════════════

class HotReloader:
    """
    Safely reloads game plugins with pre-validation, rollback on failure,
    and full history tracking.

    Parameters
    ----------
    plugin_loader : PluginLoader
        The running plugin loader instance.
    max_history : int
        Maximum number of reload records to keep in memory per game.
        Oldest entries are pruned when the limit is exceeded.
    """

    def __init__(self, plugin_loader: Any, max_history: int = 100):
        self.plugin_loader = plugin_loader
        self.registry = GameRegistry()
        self.max_history = max_history

        # Reload history: slug -> list[ReloadRecord]
        self._history: dict[str, list[ReloadRecord]] = {}

        # Last-known-good state: slug -> manifest dict snapshot
        self._last_good: dict[str, dict[str, Any]] = {}

        # Currently-loading guard: slug -> bool (prevents concurrent reloads)
        self._loading: dict[str, bool] = {}

        # Thread safety
        self._lock = threading.RLock()

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    def reload_game(self, slug: str) -> bool:
        """
        Reload a single game, with error handling and rollback.

        Steps:
          1. Check if the game exists in the plugin loader.
          2. Save a version snapshot in the registry (for rollback).
          3. Pre-validate the new game files.
          4. Call ``plugin_loader.reload_game(slug)``.
          5. If the reload fails, attempt rollback to the previous version.
          6. Log the result and record it in history.

        Returns ``True`` on success, ``False`` otherwise.
        """
        with self._lock:
            # Guard against concurrent reloads of the same slug
            if self._loading.get(slug):
                logger.warning("Concurrent reload attempt for '%s' – skipping", slug)
                return False
            self._loading[slug] = True

        try:
            return self._do_reload(slug)
        finally:
            with self._lock:
                self._loading[slug] = False

    def reload_all(self) -> int:
        """
        Reload all registered and approved games.

        Returns the count of games that were successfully loaded.
        """
        stats = self.registry.get_game_stats()
        logger.info("Starting full hot-reload (approved=%d)", stats.get("approved", 0))

        games = self.registry.list_games(status="approved")
        success_count = 0

        for game in games:
            slug = game["slug"]
            if self.reload_game(slug):
                success_count += 1

        logger.info(
            "Full hot-reload complete: %d/%d successful",
            success_count, len(games),
        )
        return success_count

    def validate_before_reload(self, slug: str) -> bool:
        """
        Pre-validate a game before reloading.

        Checks that:
          - The game exists in the registry.
          - The game directory and required files exist on disk.
          - The manifest.json is valid JSON with required keys.

        Returns ``True`` if validation passes, ``False`` otherwise.
        """
        # 1. Exists in registry?
        game = self.registry.get_game_by_slug(slug)
        if not game:
            logger.warning("validate_before_reload: '%s' not in registry", slug)
            return False

        # 2. Exists in plugin loader?
        current_plugin = self.plugin_loader.get_game(slug)
        if current_plugin is None:
            logger.warning("validate_before_reload: '%s' not in plugin loader", slug)
            return False

        # 3. Game directory and manifest exist?
        from pathlib import Path
        game_dir = current_plugin.path
        if not game_dir or not Path(game_dir).is_dir():
            logger.warning("validate_before_reload: directory missing for '%s'", slug)
            return False

        manifest_path = Path(game_dir) / "manifest.json"
        logic_path = Path(game_dir) / "logic.py"

        if not manifest_path.is_file():
            logger.warning("validate_before_reload: manifest.json missing for '%s'", slug)
            return False

        if not logic_path.is_file():
            logger.warning("validate_before_reload: logic.py missing for '%s'", slug)
            return False

        # 4. Manifest is valid JSON with required keys
        import json
        from game_bot.engine.plugin_loader import MANIFEST_REQUIRED_KEYS

        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except json.JSONDecodeError as exc:
            logger.warning("validate_before_reload: invalid manifest for '%s': %s", slug, exc)
            return False

        missing = MANIFEST_REQUIRED_KEYS - set(manifest.keys())
        if missing:
            logger.warning(
                "validate_before_reload: missing keys in manifest for '%s': %s",
                slug, missing,
            )
            return False

        # 5. logic.py has required methods
        from game_bot.engine.plugin_loader import LOGIC_REQUIRED_METHODS
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                f"_validate_{slug}", str(logic_path)
            )
            if spec is None:
                logger.warning("validate_before_reload: cannot create spec for '%s'", slug)
                return False
            temp_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(temp_module)  # type: ignore[union-attr]

            for method_name in LOGIC_REQUIRED_METHODS:
                obj = getattr(temp_module, method_name, None)
                if obj is None or not callable(obj):
                    logger.warning(
                        "validate_before_reload: '%s' missing method '%s'",
                        slug, method_name,
                    )
                    return False
        except Exception as exc:
            logger.warning("validate_before_reload: logic.py validation failed for '%s': %s", slug, exc)
            return False

        return True

    def get_load_status(self) -> dict[str, Any]:
        """
        Get the current load status of all games.

        Returns a dict with:
          - ``loaded``: list of slugs currently loaded in the plugin loader
          - ``failed``: list of slugs that failed the last reload
          - ``details``: per-slug status information
        """
        loaded: list[str] = []
        failed: list[str] = []
        details: dict[str, dict[str, Any]] = {}

        # Get all games from the registry
        all_games = self.registry.list_games()

        # Get currently loaded plugins
        loaded_plugins = {
            p.slug for p in self.plugin_loader.list_games()
        }

        for game in all_games:
            slug = game["slug"]
            is_loaded = slug in loaded_plugins
            is_active = game.get("is_active", 0) == 1
            is_approved = game.get("is_approved", 0) == 1

            last_record = self._get_last_record(slug)

            status = "loaded" if is_loaded else "not_loaded"
            if last_record and last_record.status == ReloadStatus.FAILED:
                status = "failed"
                failed.append(slug)
            elif is_loaded:
                loaded.append(slug)

            details[slug] = {
                "status": status,
                "is_active": is_active,
                "is_approved": is_approved,
                "version": game.get("version", "unknown"),
                "last_reload": last_record.to_dict() if last_record else None,
            }

        return {
            "loaded": loaded,
            "failed": failed,
            "total_registered": len(all_games),
            "total_loaded": len(loaded),
            "details": details,
        }

    def rollback(self, slug: str) -> bool:
        """
        Revert to the last known good version of a game.

        This attempts:
          1. Registry rollback (restore previous version metadata).
          2. Plugin loader reload of the previous version.

        Returns ``True`` on success, ``False`` otherwise.
        """
        start = time.time()
        logger.info("Attempting rollback for '%s'", slug)

        # Try registry rollback
        registry_ok = self.registry.rollback_game(slug)

        if not registry_ok:
            logger.warning("Registry rollback failed for '%s' – no previous version", slug)
            # Still try to reload from disk as a fallback
            try:
                result = self.plugin_loader.reload_game(slug)
                if result is not None:
                    duration = (time.time() - start) * 1000
                    self._record(
                        slug, ReloadStatus.ROLLED_BACK,
                        duration_ms=duration,
                    )
                    logger.info("Fallback reload succeeded for '%s'", slug)
                    return True
            except Exception:
                pass

            self._record(
                slug, ReloadStatus.FAILED,
                error_message="Rollback failed: no previous version and reload failed",
                duration_ms=(time.time() - start) * 1000,
            )
            return False

        # Reload in the plugin loader
        try:
            result = self.plugin_loader.reload_game(slug)
            if result is not None:
                duration = (time.time() - start) * 1000
                self._record(
                    slug, ReloadStatus.ROLLED_BACK,
                    duration_ms=duration,
                )
                logger.info("Rollback successful for '%s' (%.1f ms)", slug, duration)
                return True
            else:
                duration = (time.time() - start) * 1000
                self._record(
                    slug, ReloadStatus.FAILED,
                    error_message="Rollback: registry ok but plugin reload returned None",
                    duration_ms=duration,
                )
                logger.error("Rollback plugin reload failed for '%s'", slug)
                return False
        except Exception as exc:
            duration = (time.time() - start) * 1000
            self._record(
                slug, ReloadStatus.FAILED,
                error_message=f"Rollback plugin reload exception: {exc}",
                duration_ms=duration,
            )
            logger.error("Rollback plugin reload exception for '%s': %s", slug, exc)
            return False

    # ──────────────────────────────────────────────────────────────
    # History
    # ──────────────────────────────────────────────────────────────

    def get_history(self, slug: Optional[str] = None, limit: int = 20) -> list[dict[str, Any]]:
        """
        Get reload history.

        Parameters
        ----------
        slug : str or None
            If provided, return history for this game only.
            Otherwise return the most recent records across all games.
        limit : int
            Maximum number of records to return.
        """
        with self._lock:
            if slug:
                records = list(self._history.get(slug, []))
                records.reverse()
                return [r.to_dict() for r in records[:limit]]
            else:
                # Flatten and sort by timestamp descending
                all_records: list[ReloadRecord] = []
                for recs in self._history.values():
                    all_records.extend(recs)
                all_records.sort(key=lambda r: r.timestamp, reverse=True)
                return [r.to_dict() for r in all_records[:limit]]

    def clear_history(self, slug: Optional[str] = None) -> None:
        """Clear reload history, optionally for a specific slug only."""
        with self._lock:
            if slug:
                self._history.pop(slug, None)
            else:
                self._history.clear()

    # ──────────────────────────────────────────────────────────────
    # Internal implementation
    # ──────────────────────────────────────────────────────────────

    def _do_reload(self, slug: str) -> bool:
        """Core reload logic (called while holding _loading guard)."""
        start = time.time()
        logger.info("Hot-reloading game '%s'", slug)

        # Check that the game exists in the plugin loader
        current_plugin = self.plugin_loader.get_game(slug)
        if current_plugin is None:
            # Game might not be loaded yet – try to discover it
            logger.warning("Game '%s' not currently loaded – attempting discovery", slug)
            try:
                self.plugin_loader.discover_all()
                current_plugin = self.plugin_loader.get_game(slug)
            except Exception:
                pass

            if current_plugin is None:
                duration = (time.time() - start) * 1000
                self._record(
                    slug, ReloadStatus.NOT_FOUND,
                    error_message=f"Game '{slug}' not found in plugin loader",
                    duration_ms=duration,
                )
                logger.error("Game '%s' not found – cannot reload", slug)
                return False

        # Capture current version info before reload
        previous_version = getattr(current_plugin, "version", "unknown")
        previous_manifest = {}
        if hasattr(current_plugin, "manifest"):
            previous_manifest = dict(current_plugin.manifest) if isinstance(current_plugin.manifest, dict) else {}

        # Save last-known-good state
        with self._lock:
            self._last_good[slug] = {
                "version": previous_version,
                "manifest": previous_manifest,
                "name": getattr(current_plugin, "name", slug),
            }

        # Save version snapshot in the registry (for rollback)
        self.registry.save_version_snapshot(slug)

        # Pre-validate
        if not self.validate_before_reload(slug):
            duration = (time.time() - start) * 1000
            self._record(
                slug, ReloadStatus.VALIDATION_FAILED,
                error_message="Pre-validation failed",
                previous_version=previous_version,
                duration_ms=duration,
            )
            logger.warning("Pre-validation failed for '%s' – aborting reload", slug)
            return False

        # Attempt the reload
        try:
            result = self.plugin_loader.reload_game(slug)
            duration = (time.time() - start) * 1000

            if result is not None:
                new_version = getattr(result, "version", "unknown")
                self._record(
                    slug, ReloadStatus.SUCCESS,
                    previous_version=previous_version,
                    new_version=new_version,
                    duration_ms=duration,
                )
                logger.info(
                    "Hot-reload successful for '%s' (%s → %s, %.1f ms)",
                    slug, previous_version, new_version, duration,
                )
                return True
            else:
                # Reload returned None – this means the plugin failed validation
                logger.error("Hot-reload returned None for '%s' – attempting rollback", slug)
                rollback_ok = self._attempt_rollback(slug, previous_version, duration)
                if rollback_ok:
                    self._record(
                        slug, ReloadStatus.ROLLED_BACK,
                        error_message="Reload returned None; rolled back to previous version",
                        previous_version=previous_version,
                        duration_ms=duration,
                    )
                else:
                    self._record(
                        slug, ReloadStatus.FAILED,
                        error_message="Reload returned None and rollback also failed",
                        previous_version=previous_version,
                        duration_ms=duration,
                    )
                return False

        except Exception as exc:
            duration = (time.time() - start) * 1000
            logger.error(
                "Hot-reload exception for '%s': %s – attempting rollback",
                slug, exc, exc_info=True,
            )
            rollback_ok = self._attempt_rollback(slug, previous_version, duration)
            if rollback_ok:
                self._record(
                    slug, ReloadStatus.ROLLED_BACK,
                    error_message=f"Reload exception: {exc}; rolled back",
                    previous_version=previous_version,
                    duration_ms=duration,
                )
            else:
                self._record(
                    slug, ReloadStatus.FAILED,
                    error_message=f"Reload exception: {exc}; rollback also failed",
                    previous_version=previous_version,
                    duration_ms=duration,
                )
            return False

    def _attempt_rollback(self, slug: str, previous_version: str, elapsed_ms: float) -> bool:
        """
        Attempt to rollback a game after a failed reload.

        Tries to restore the last-known-good state by:
          1. Restoring the previous version in the registry.
          2. Reloading the previous version from disk.
        """
        logger.info("Attempting rollback for '%s' to version %s", slug, previous_version)

        # Try registry rollback
        try:
            self.registry.rollback_game(slug)
        except Exception as exc:
            logger.error("Registry rollback failed for '%s': %s", slug, exc)

        # Try to reload whatever is on disk (should be the old version after rollback)
        try:
            result = self.plugin_loader.reload_game(slug)
            if result is not None:
                logger.info("Rollback reload successful for '%s'", slug)
                return True
        except Exception as exc:
            logger.error("Rollback reload failed for '%s': %s", slug, exc)

        # Last resort: re-discover all games
        try:
            self.plugin_loader.discover_all()
            result = self.plugin_loader.get_game(slug)
            if result is not None:
                logger.info("Rollback via discover_all successful for '%s'", slug)
                return True
        except Exception as exc:
            logger.error("Rollback discover_all failed for '%s': %s", slug, exc)

        return False

    def _record(
        self,
        slug: str,
        status: ReloadStatus,
        error_message: str = "",
        previous_version: str = "",
        new_version: str = "",
        duration_ms: float = 0.0,
    ) -> None:
        """Record a reload attempt in history."""
        record = ReloadRecord(
            slug=slug,
            status=status,
            error_message=error_message,
            previous_version=previous_version,
            new_version=new_version,
            duration_ms=duration_ms,
        )

        with self._lock:
            if slug not in self._history:
                self._history[slug] = []
            self._history[slug].append(record)

            # Prune old records
            if len(self._history[slug]) > self.max_history:
                self._history[slug] = self._history[slug][-self.max_history:]

        # Log summary
        if status == ReloadStatus.SUCCESS:
            logger.info(
                "Reload record: [%s] %s → %s (%.1f ms)",
                status.value, previous_version, new_version, duration_ms,
            )
        else:
            logger.warning(
                "Reload record: [%s] %s – %s (%.1f ms)",
                status.value, slug, error_message, duration_ms,
            )

    def _get_last_record(self, slug: str) -> Optional[ReloadRecord]:
        """Return the most recent reload record for a slug, or None."""
        with self._lock:
            records = self._history.get(slug, [])
            return records[-1] if records else None
