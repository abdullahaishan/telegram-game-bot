"""
Dynamic Game Plugin Loader

Scans the ``/games/`` directory for subdirectories, each representing a game
plugin.  Every plugin must contain:

- ``manifest.json`` – metadata describing the game
- ``logic.py``       – Python module with required entry-point methods

The loader validates both files, registers valid games, and provides lookup /
reload capabilities.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Required top-level keys in manifest.json
MANIFEST_REQUIRED_KEYS = {
    "slug",
    "name",
    "version",
    "description",
    "min_players",
    "max_players",
    "mode",
    "board",
    "rewards",
}

# Required methods that logic.py must expose as module-level callables
LOGIC_REQUIRED_METHODS = (
    "init_game",
    "render",
    "handle_callback",
    "check_win",
    "serialize_state",
    "deserialize_state",
)

# Telegram inline-keyboard limit
MAX_BUTTONS_PER_ROW = 8

# Maximum board dimensions
MAX_BOARD_ROWS = 20
MAX_BOARD_COLS = 8  # Telegram limit – 8 buttons per row


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GamePlugin:
    """Validated game plugin ready for use."""

    slug: str
    name: str
    version: str
    description: str
    min_players: int
    max_players: int
    mode: str
    board: dict
    rewards: dict
    manifest: dict
    logic_module: Any  # loaded Python module
    path: Path

    # Derived / optional
    fee: float = 0.0
    categories: list[str] = field(default_factory=list)
    author: str = ""
    icon: str = "🎮"

    # Entry-point convenience wrappers
    def init_game(self, *args, **kwargs):
        return self.logic_module.init_game(*args, **kwargs)

    def render(self, *args, **kwargs):
        return self.logic_module.render(*args, **kwargs)

    def handle_callback(self, *args, **kwargs):
        return self.logic_module.handle_callback(*args, **kwargs)

    def check_win(self, *args, **kwargs):
        return self.logic_module.check_win(*args, **kwargs)

    def serialize_state(self, *args, **kwargs):
        return self.logic_module.serialize_state(*args, **kwargs)

    def deserialize_state(self, *args, **kwargs):
        return self.logic_module.deserialize_state(*args, **kwargs)


@dataclass
class ValidationError:
    """Single validation error entry."""

    slug: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"[{self.slug}] {self.field}: {self.message}"


# ---------------------------------------------------------------------------
# PluginLoader
# ---------------------------------------------------------------------------

class PluginLoader:
    """
    Discovers, validates, and loads game plugins from the games directory.

    Usage::

        loader = PluginLoader("/path/to/games")
        loader.discover_all()
        game = loader.get_game("tic-tac-toe")
    """

    def __init__(self, games_dir: str | Path | None = None):
        if games_dir is None:
            # Default: <project_root>/games/
            games_dir = Path(__file__).resolve().parent.parent.parent / "games"
        self.games_dir = Path(games_dir)
        self._registry: dict[str, GamePlugin] = {}
        self._errors: list[ValidationError] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover_all(self) -> dict[str, GamePlugin]:
        """
        Scan the games directory, validate and load every plugin found.

        Returns the registry dict (slug -> GamePlugin).
        """
        self._errors.clear()

        if not self.games_dir.is_dir():
            logger.error("Games directory does not exist: %s", self.games_dir)
            return self._registry

        for entry in sorted(self.games_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("_") or entry.name.startswith("."):
                continue
            try:
                plugin = self._load_plugin(entry)
            except PluginLoadError as exc:
                logger.warning("Skipping game '%s': %s", entry.name, exc)
                self._errors.append(
                    ValidationError(slug=entry.name, field="load", message=str(exc))
                )
                continue
            if plugin is not None:
                self._registry[plugin.slug] = plugin
                logger.info(
                    "Loaded game plugin: %s v%s (%s)",
                    plugin.name,
                    plugin.version,
                    plugin.slug,
                )

        return self._registry

    def get_game(self, slug: str) -> Optional[GamePlugin]:
        """Return a loaded game plugin by slug, or ``None``."""
        return self._registry.get(slug)

    def list_games(self) -> list[GamePlugin]:
        """Return all loaded game plugins."""
        return list(self._registry.values())

    def reload_game(self, slug: str) -> Optional[GamePlugin]:
        """
        Reload a single game plugin from disk (useful during development).

        Returns the reloaded plugin, or ``None`` if not found / invalid.
        """
        existing = self._registry.get(slug)
        if existing is None:
            logger.warning("Cannot reload '%s': not in registry", slug)
            return None

        # Remove old module from sys.modules so importlib picks up changes
        mod_name = existing.logic_module.__name__
        if mod_name in sys.modules:
            del sys.modules[mod_name]

        game_dir = existing.path
        try:
            plugin = self._load_plugin(game_dir)
        except PluginLoadError as exc:
            logger.error("Reload failed for '%s': %s", slug, exc)
            self._errors.append(
                ValidationError(slug=slug, field="reload", message=str(exc))
            )
            return None

        if plugin is not None:
            self._registry[slug] = plugin
            logger.info("Reloaded game plugin: %s", slug)
        return plugin

    def get_errors(self) -> list[ValidationError]:
        """Return validation errors from the last ``discover_all`` call."""
        return list(self._errors)

    # ------------------------------------------------------------------
    # Internal – loading pipeline
    # ------------------------------------------------------------------

    def _load_plugin(self, game_dir: Path) -> Optional[GamePlugin]:
        """
        Full load pipeline for a single game directory.

        Raises ``PluginLoadError`` on fatal problems.
        """
        slug = game_dir.name

        # 1) manifest.json must exist
        manifest_path = game_dir / "manifest.json"
        if not manifest_path.is_file():
            raise PluginLoadError(f"manifest.json not found in {game_dir}")

        # 2) Load & validate manifest
        manifest = self._load_manifest(manifest_path)
        errors = self._validate_manifest(manifest, slug)
        if errors:
            for e in errors:
                self._errors.append(e)
            raise PluginLoadError(
                f"Manifest validation failed: {'; '.join(str(e) for e in errors)}"
            )

        # 3) logic.py must exist
        logic_path = game_dir / "logic.py"
        if not logic_path.is_file():
            raise PluginLoadError(f"logic.py not found in {game_dir}")

        # 4) Dynamically import logic.py
        module_name = f"games.{slug}.logic"
        logic_module = self._import_module(logic_path, module_name)

        # 5) Validate required methods
        method_errors = self._validate_logic_methods(logic_module, slug)
        if method_errors:
            for e in method_errors:
                self._errors.append(e)
            raise PluginLoadError(
                f"Logic validation failed: {'; '.join(str(e) for e in method_errors)}"
            )

        # 6) Extra validations (button count, board dims, rewards)
        extra_errors = self._validate_manifest_rules(manifest, slug)
        if extra_errors:
            for e in extra_errors:
                self._errors.append(e)
            raise PluginLoadError(
                f"Rule validation failed: {'; '.join(str(e) for e in extra_errors)}"
            )

        # 7) Assemble GamePlugin
        rewards = manifest.get("rewards", {})
        board = manifest.get("board", {})

        plugin = GamePlugin(
            slug=manifest["slug"],
            name=manifest["name"],
            version=manifest["version"],
            description=manifest["description"],
            min_players=int(manifest["min_players"]),
            max_players=int(manifest["max_players"]),
            mode=manifest["mode"],
            board=board,
            rewards=rewards,
            manifest=manifest,
            logic_module=logic_module,
            path=game_dir,
            fee=float(rewards.get("entry_fee", 0)),
            categories=manifest.get("categories", []),
            author=manifest.get("author", ""),
            icon=manifest.get("icon", "🎮"),
        )

        return plugin

    # ------------------------------------------------------------------
    # Manifest handling
    # ------------------------------------------------------------------

    @staticmethod
    def _load_manifest(path: Path) -> dict:
        """Load and parse manifest.json."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except json.JSONDecodeError as exc:
            raise PluginLoadError(f"Invalid JSON in {path}: {exc}") from exc
        except OSError as exc:
            raise PluginLoadError(f"Cannot read {path}: {exc}") from exc

    @staticmethod
    def _validate_manifest(manifest: dict, slug: str) -> list[ValidationError]:
        """Check that all required keys are present and have valid values."""
        errors: list[ValidationError] = []

        # Required keys
        for key in MANIFEST_REQUIRED_KEYS:
            if key not in manifest:
                errors.append(
                    ValidationError(slug=slug, field=key, message="Missing required key")
                )

        if errors:
            return errors  # stop further checks if keys missing

        # slug must match directory name
        if manifest["slug"] != slug:
            errors.append(
                ValidationError(
                    slug=slug,
                    field="slug",
                    message=f"Manifest slug '{manifest['slug']}' does not match directory '{slug}'",
                )
            )

        # name must be non-empty
        if not manifest.get("name"):
            errors.append(
                ValidationError(slug=slug, field="name", message="Name must not be empty")
            )

        # version should look like semver-ish
        version = manifest.get("version", "")
        if not isinstance(version, str) or not version.strip():
            errors.append(
                ValidationError(slug=slug, field="version", message="Version must be a non-empty string")
            )

        # min/max players
        min_p = manifest.get("min_players")
        max_p = manifest.get("max_players")
        if not isinstance(min_p, int) or min_p < 1:
            errors.append(
                ValidationError(
                    slug=slug, field="min_players", message="Must be an integer >= 1"
                )
            )
        if not isinstance(max_p, int) or max_p < 1:
            errors.append(
                ValidationError(
                    slug=slug, field="max_players", message="Must be an integer >= 1"
                )
            )
        if isinstance(min_p, int) and isinstance(max_p, int) and min_p > max_p:
            errors.append(
                ValidationError(
                    slug=slug,
                    field="min_players",
                    message=f"min_players ({min_p}) > max_players ({max_p})",
                )
            )

        # mode
        if not isinstance(manifest.get("mode"), str) or not manifest["mode"].strip():
            errors.append(
                ValidationError(slug=slug, field="mode", message="Mode must be a non-empty string")
            )

        # board
        board = manifest.get("board")
        if not isinstance(board, dict):
            errors.append(
                ValidationError(slug=slug, field="board", message="Board must be a dict")
            )

        # rewards
        rewards = manifest.get("rewards")
        if not isinstance(rewards, dict):
            errors.append(
                ValidationError(slug=slug, field="rewards", message="Rewards must be a dict")
            )

        return errors

    # ------------------------------------------------------------------
    # Logic module handling
    # ------------------------------------------------------------------

    @staticmethod
    def _import_module(path: Path, module_name: str):
        """Dynamically import a Python file as a module."""
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None:
            raise PluginLoadError(f"Cannot create module spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception as exc:
            # Clean up broken module
            sys.modules.pop(module_name, None)
            tb = traceback.format_exc()
            raise PluginLoadError(
                f"Error executing {path}:\n{tb}"
            ) from exc
        return module

    @staticmethod
    def _validate_logic_methods(module, slug: str) -> list[ValidationError]:
        """Check that the module exposes all required callables."""
        errors: list[ValidationError] = []
        for method_name in LOGIC_REQUIRED_METHODS:
            obj = getattr(module, method_name, None)
            if obj is None:
                errors.append(
                    ValidationError(
                        slug=slug,
                        field=f"logic.{method_name}",
                        message="Required method is missing",
                    )
                )
            elif not callable(obj):
                errors.append(
                    ValidationError(
                        slug=slug,
                        field=f"logic.{method_name}",
                        message="Must be callable",
                    )
                )
        return errors

    # ------------------------------------------------------------------
    # Extra rule validations
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_manifest_rules(manifest: dict, slug: str) -> list[ValidationError]:
        """
        Validate button count, board dimensions, and reward/fee rules.
        """
        errors: list[ValidationError] = []

        # --- Board dimensions ---
        board = manifest.get("board", {})
        if isinstance(board, dict):
            rows = board.get("rows")
            cols = board.get("cols")

            if rows is not None:
                if not isinstance(rows, int) or rows < 1:
                    errors.append(
                        ValidationError(
                            slug=slug,
                            field="board.rows",
                            message="Must be a positive integer",
                        )
                    )
                elif rows > MAX_BOARD_ROWS:
                    errors.append(
                        ValidationError(
                            slug=slug,
                            field="board.rows",
                            message=f"Exceeds maximum ({MAX_BOARD_ROWS})",
                        )
                    )

            if cols is not None:
                if not isinstance(cols, int) or cols < 1:
                    errors.append(
                        ValidationError(
                            slug=slug,
                            field="board.cols",
                            message="Must be a positive integer",
                        )
                    )
                elif cols > MAX_BOARD_COLS:
                    errors.append(
                        ValidationError(
                            slug=slug,
                            field="board.cols",
                            message=f"Exceeds Telegram inline-keyboard limit ({MAX_BOARD_COLS})",
                        )
                    )

            # If both rows and cols are valid, check total button count
            if (
                isinstance(rows, int)
                and isinstance(cols, int)
                and 1 <= rows <= MAX_BOARD_ROWS
                and 1 <= cols <= MAX_BOARD_COLS
            ):
                total_buttons = rows * cols
                # Telegram allows up to 100 buttons total in a message
                if total_buttons > 100:
                    errors.append(
                        ValidationError(
                            slug=slug,
                            field="board",
                            message=f"Total buttons ({total_buttons}) exceeds Telegram limit (100)",
                        )
                    )

        # --- Rewards / fees ---
        rewards = manifest.get("rewards", {})
        if isinstance(rewards, dict):
            entry_fee = rewards.get("entry_fee")
            if entry_fee is not None:
                if not isinstance(entry_fee, (int, float)) or entry_fee < 0:
                    errors.append(
                        ValidationError(
                            slug=slug,
                            field="rewards.entry_fee",
                            message="Must be a non-negative number",
                        )
                    )

            win_reward = rewards.get("win_reward")
            if win_reward is not None:
                if not isinstance(win_reward, (int, float)) or win_reward < 0:
                    errors.append(
                        ValidationError(
                            slug=slug,
                            field="rewards.win_reward",
                            message="Must be a non-negative number",
                        )
                    )

            # If both fee and reward exist, reward should be >= fee (otherwise nobody profits)
            if (
                isinstance(entry_fee, (int, float))
                and isinstance(win_reward, (int, float))
                and win_reward < entry_fee
            ):
                errors.append(
                    ValidationError(
                        slug=slug,
                        field="rewards",
                        message=f"win_reward ({win_reward}) < entry_fee ({entry_fee}) – players cannot profit",
                    )
                )

        return errors


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class PluginLoadError(Exception):
    """Raised when a game plugin cannot be loaded or validated."""
