"""
Builder Validator

Validates a game configuration dictionary before publishing. Runs a
comprehensive suite of checks covering metadata, player constraints,
button definitions, board setup, win logic, economy, slug generation,
manifest compilation, logic generation, and UI renderer compatibility.
"""

from __future__ import annotations

import json
import logging
import re
import textwrap
from typing import Any, Dict, List, Optional

from .steps import GAME_TYPES, WIN_TYPES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Check result helper
# ---------------------------------------------------------------------------

def _check(field: str, passed: bool, message: str, suggestion: str = "") -> Dict[str, Any]:
    """Build a single check-result dict."""
    return {
        "field": field,
        "passed": passed,
        "message": message,
        "suggestion": suggestion,
    }


# ---------------------------------------------------------------------------
# BuilderValidator
# ---------------------------------------------------------------------------

class BuilderValidator:
    """
    Validates a game configuration before publishing.

    Usage::

        v = BuilderValidator()
        result = v.validate(config_dict)
        if result["valid"]:
            # safe to export
        else:
            # show result["errors"] to user
    """

    # Types that require a board
    BOARD_REQUIRED_TYPES = {
        gt for gt, meta in GAME_TYPES.items()
        if meta.get("requires_board", False) or meta.get("board_required", False)
    }

    # Effect types that buttons may use
    VALID_EFFECT_TYPES = {
        "MOVE", "SCORE", "ATTACK", "DEFEND", "HEAL",
        "REVEAL", "HIDE", "PLACE", "SKIP", "CUSTOM",
        "VOTE", "CHALLENGE", "DRAW_CARD", "USE_POWER",
        "BLOCK", "RANDOM", "PASS_TURN", "TOGGLE",
        "SWITCH", "LOCK", "UNLOCK",
    }

    def validate(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run all validations on *config*.

        Returns::

            {
                "valid": bool,
                "errors": [str, ...],
                "warnings": [str, ...],
                "checks": {
                    "check_key": {"field": str, "passed": bool, "message": str, "suggestion": str},
                    ...
                }
            }
        """
        checks: Dict[str, Dict[str, Any]] = {}
        errors: List[str] = []
        warnings: List[str] = []

        # Run each validation check
        checks["game_name"] = self._check_game_name(config)
        checks["description"] = self._check_description(config)
        checks["creator_name"] = self._check_creator_name(config)
        checks["game_type"] = self._check_game_type(config)
        checks["player_counts"] = self._check_player_counts(config)
        checks["buttons_exist"] = self._check_buttons_exist(config)
        checks["button_count"] = self._check_button_count(config)
        checks["button_fields"] = self._check_button_fields(config)
        checks["board_config"] = self._check_board_config(config)
        checks["board_cells"] = self._check_board_cells(config)
        checks["win_logic_type"] = self._check_win_logic_type(config)
        checks["win_logic_fields"] = self._check_win_logic_fields(config)
        checks["economy"] = self._check_economy(config)
        checks["exploit_conditions"] = self._check_exploit_conditions(config)
        checks["slug_generation"] = self._check_slug_generation(config)
        checks["manifest_compilation"] = self._check_manifest_compilation(config)
        checks["logic_generation"] = self._check_logic_generation(config)
        checks["ui_renderer_compat"] = self._check_ui_renderer_compat(config)

        # Aggregate results
        for key, result in checks.items():
            if not result["passed"]:
                if result.get("severity") == "warning":
                    warnings.append(f"[{result['field']}] {result['message']}")
                else:
                    errors.append(f"[{result['field']}] {result['message']}")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "checks": checks,
        }

    # ------------------------------------------------------------------
    # 1. Game name
    # ------------------------------------------------------------------

    def _check_game_name(self, config: Dict[str, Any]) -> Dict[str, Any]:
        name = config.get("game_name", "")
        if not name or not isinstance(name, str):
            return _check("game_name", False, "Game name is required.", "Provide a name between 2 and 50 characters.")
        stripped = name.strip()
        if len(stripped) < 2:
            return _check("game_name", False, "Game name must be at least 2 characters.", "Use a longer, more descriptive name.")
        if len(stripped) > 50:
            return _check("game_name", False, "Game name must be at most 50 characters.", "Shorten the name to 50 chars or less.")
        return _check("game_name", True, f"Game name '{stripped}' is valid.")

    # ------------------------------------------------------------------
    # 2. Description
    # ------------------------------------------------------------------

    def _check_description(self, config: Dict[str, Any]) -> Dict[str, Any]:
        desc = config.get("description", "")
        if not desc or not isinstance(desc, str):
            return _check("description", False, "Description is required.", "Provide a description between 5 and 200 characters.")
        stripped = desc.strip()
        if len(stripped) < 5:
            return _check("description", False, "Description must be at least 5 characters.", "Add more detail to your description.")
        if len(stripped) > 200:
            return _check("description", False, "Description must be at most 200 characters.", "Shorten the description to 200 chars or less.")
        return _check("description", True, "Description length is valid.")

    # ------------------------------------------------------------------
    # 3. Creator name
    # ------------------------------------------------------------------

    def _check_creator_name(self, config: Dict[str, Any]) -> Dict[str, Any]:
        creator = config.get("creator_name", "")
        if not creator or not isinstance(creator, str) or not creator.strip():
            return _check("creator_name", False, "Creator name is required.", "Enter the name of the game creator.")
        return _check("creator_name", True, f"Creator name '{creator.strip()}' is valid.")

    # ------------------------------------------------------------------
    # 4. Game type
    # ------------------------------------------------------------------

    def _check_game_type(self, config: Dict[str, Any]) -> Dict[str, Any]:
        gt = config.get("game_type", "")
        if not gt or not isinstance(gt, str):
            return _check("game_type", False, "Game type is required.", f"Choose one of: {', '.join(GAME_TYPES.keys())}")
        if gt not in GAME_TYPES:
            return _check("game_type", False, f"Invalid game type '{gt}'.", f"Choose one of: {', '.join(GAME_TYPES.keys())}")
        label = GAME_TYPES[gt].get("label", GAME_TYPES[gt].get("name", gt))
        return _check("game_type", True, f"Game type '{label}' is valid.")

    # ------------------------------------------------------------------
    # 5. Player counts
    # ------------------------------------------------------------------

    def _check_player_counts(self, config: Dict[str, Any]) -> Dict[str, Any]:
        min_p = config.get("min_players")
        max_p = config.get("max_players")

        if min_p is None:
            return _check("min_players", False, "Minimum player count is required.", "Set min_players to 1 or more.")
        try:
            min_p = int(min_p)
        except (ValueError, TypeError):
            return _check("min_players", False, "min_players must be an integer.", "Set min_players to a whole number >= 1.")

        if max_p is None:
            return _check("max_players", False, "Maximum player count is required.", "Set max_players to a number >= min_players.")
        try:
            max_p = int(max_p)
        except (ValueError, TypeError):
            return _check("max_players", False, "max_players must be an integer.", "Set max_players to a whole number <= 20.")

        if min_p < 1:
            return _check("player_counts", False, "min_players must be >= 1.", "Set min_players to at least 1.")
        if max_p < min_p:
            return _check("player_counts", False, f"max_players ({max_p}) must be >= min_players ({min_p}).", "Increase max_players or decrease min_players.")
        if max_p > 20:
            return _check("player_counts", False, f"max_players ({max_p}) must be <= 20.", "Reduce max_players to 20 or less for Telegram compatibility.")
        return _check("player_counts", True, f"Player range {min_p}-{max_p} is valid.")

    # ------------------------------------------------------------------
    # 6. At least 1 button with label and action_id
    # ------------------------------------------------------------------

    def _check_buttons_exist(self, config: Dict[str, Any]) -> Dict[str, Any]:
        buttons = config.get("buttons", [])
        if not buttons or not isinstance(buttons, list):
            return _check("buttons", False, "At least 1 button is required.", "Add at least one button with a label and action_id.")
        valid_buttons = [b for b in buttons if isinstance(b, dict) and b.get("label") and b.get("action_id")]
        if not valid_buttons:
            return _check("buttons", False, "At least 1 button must have both label and action_id.", "Ensure every button has a 'label' and 'action_id'.")
        return _check("buttons_exist", True, f"Found {len(valid_buttons)} valid button(s).")

    # ------------------------------------------------------------------
    # 7. Button count <= 12
    # ------------------------------------------------------------------

    def _check_button_count(self, config: Dict[str, Any]) -> Dict[str, Any]:
        buttons = config.get("buttons", [])
        if not isinstance(buttons, list):
            return _check("button_count", False, "Buttons must be a list.", "Provide buttons as a JSON array.")
        count = len(buttons)
        if count > 12:
            return _check("button_count", False, f"Too many buttons ({count}). Maximum is 12.", "Reduce the number of buttons to 12 or fewer.")
        if count == 0:
            return _check("button_count", False, "No buttons defined.", "Add at least 1 button.")
        return _check("button_count", True, f"Button count ({count}) is within limit (1-12).")

    # ------------------------------------------------------------------
    # 8. Each button has label, action_id, effect_type
    # ------------------------------------------------------------------

    def _check_button_fields(self, config: Dict[str, Any]) -> Dict[str, Any]:
        buttons = config.get("buttons", [])
        if not isinstance(buttons, list) or not buttons:
            return _check("button_fields", False, "No buttons to validate.", "Add buttons first.")

        missing: List[str] = []
        for i, btn in enumerate(buttons):
            if not isinstance(btn, dict):
                missing.append(f"Button #{i + 1}: not a valid object")
                continue
            if not btn.get("label"):
                missing.append(f"Button #{i + 1}: missing 'label'")
            if not btn.get("action_id"):
                missing.append(f"Button #{i + 1}: missing 'action_id'")
            if not btn.get("effect_type"):
                missing.append(f"Button #{i + 1}: missing 'effect_type'")

        if missing:
            msg = "Missing required fields: " + "; ".join(missing[:5])
            if len(missing) > 5:
                msg += f" ... and {len(missing) - 5} more"
            return _check("button_fields", False, msg, "Each button must have 'label', 'action_id', and 'effect_type'.")
        return _check("button_fields", True, "All buttons have required fields (label, action_id, effect_type).")

    # ------------------------------------------------------------------
    # 9. Board config (if game type requires board)
    # ------------------------------------------------------------------

    def _check_board_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        gt = config.get("game_type", "")
        board_enabled = config.get("board_enabled", False)

        # Also check nested board config format
        board = config.get("board", {})
        if isinstance(board, dict) and board.get("enabled", False):
            board_enabled = True

        # Determine if board is required
        requires_board = gt in self.BOARD_REQUIRED_TYPES

        if not requires_board and not board_enabled:
            return _check("board_config", True, "Board not required for this game type.")

        if requires_board and not board_enabled:
            return _check("board_config", True, "Board will be auto-enabled (required for this game type).",
                          "Board is required for this game type and will be enabled automatically.")

        # Get rows/cols from either flat or nested config
        rows = config.get("board_rows")
        cols = config.get("board_cols")
        if rows is None and isinstance(board, dict):
            rows = board.get("rows")
        if cols is None and isinstance(board, dict):
            cols = board.get("cols")

        if rows is None:
            return _check("board_config", False, "Board rows is required when board is enabled.", "Set board_rows between 1 and 20.")
        if cols is None:
            return _check("board_config", False, "Board columns is required when board is enabled.", "Set board_cols between 1 and 8.")

        try:
            rows = int(rows)
        except (ValueError, TypeError):
            return _check("board_config", False, "Board rows must be an integer.", "Set board_rows to a whole number.")

        try:
            cols = int(cols)
        except (ValueError, TypeError):
            return _check("board_config", False, "Board columns must be an integer.", "Set board_cols to a whole number.")

        if rows < 1:
            return _check("board_config", False, "Board rows must be >= 1.", "Set board_rows to at least 1.")
        if rows > 20:
            return _check("board_config", False, f"Board rows ({rows}) must be <= 20.", "Reduce rows to 20 or less.")
        if cols < 1:
            return _check("board_config", False, "Board columns must be >= 1.", "Set board_cols to at least 1.")
        if cols > 8:
            return _check("board_config", False, f"Board columns ({cols}) must be <= 8 (Telegram inline-keyboard limit).", "Reduce columns to 8 or less.")

        return _check("board_config", True, f"Board dimensions {rows}x{cols} are valid.")

    # ------------------------------------------------------------------
    # 10. Board cells valid if board enabled
    # ------------------------------------------------------------------

    def _check_board_cells(self, config: Dict[str, Any]) -> Dict[str, Any]:
        board_enabled = config.get("board_enabled", False)
        gt = config.get("game_type", "")
        requires_board = gt in self.BOARD_REQUIRED_TYPES
        board = config.get("board", {})
        if isinstance(board, dict) and board.get("enabled", False):
            board_enabled = True

        if not board_enabled and not requires_board:
            return _check("board_cells", True, "Board not enabled — cell check skipped.")

        rows = config.get("board_rows", 0)
        cols = config.get("board_cols", 0)
        if isinstance(board, dict):
            rows = board.get("rows", rows)
            cols = board.get("cols", cols)
        cells = config.get("board_cells")

        # Also check nested cells
        if cells is None and isinstance(board, dict):
            cells = board.get("cells")

        if cells is None or (isinstance(cells, list) and len(cells) == 0):
            return _check("board_cells", True, "Board cells will be auto-generated.")

        if not isinstance(cells, list):
            return _check("board_cells", False, "board_cells must be a 2D list.", "Provide cells as [[row0], [row1], ...].")

        try:
            rows = int(rows)
            cols = int(cols)
        except (ValueError, TypeError):
            return _check("board_cells", True, "Board cells skipped — invalid dimensions.")

        if len(cells) != rows:
            return _check("board_cells", False,
                          f"board_cells has {len(cells)} rows but board_rows={rows}.",
                          "Ensure the number of cell rows matches board_rows.")

        for i, row in enumerate(cells):
            if not isinstance(row, list):
                return _check("board_cells", False, f"Row {i} is not a list.", "Each row must be a list of cell values.")
            if len(row) != cols:
                return _check("board_cells", False,
                              f"Row {i} has {len(row)} cells but board_cols={cols}.",
                              "Ensure each row has exactly board_cols cells.")

        return _check("board_cells", True, f"Board cells ({rows}x{cols}) are valid.")

    # ------------------------------------------------------------------
    # 11. Win logic type
    # ------------------------------------------------------------------

    def _check_win_logic_type(self, config: Dict[str, Any]) -> Dict[str, Any]:
        # Support both flat and nested win_logic config
        wt = config.get("win_type", "")
        if not wt:
            win_logic = config.get("win_logic", {})
            if isinstance(win_logic, dict):
                wt = win_logic.get("type", "")

        if not wt or not isinstance(wt, str):
            return _check("win_type", False, "Win condition type is required.", f"Choose one of: {', '.join(WIN_TYPES.keys())}")
        if wt not in WIN_TYPES:
            return _check("win_type", False, f"Invalid win type '{wt}'.", f"Choose one of: {', '.join(WIN_TYPES.keys())}")
        win_entry = WIN_TYPES[wt]
        if isinstance(win_entry, dict):
            label = win_entry.get("label", win_entry.get("name", wt))
        else:
            label = str(win_entry) if win_entry else wt
        return _check("win_logic_type", True, f"Win type '{label}' is valid.")

    # ------------------------------------------------------------------
    # 12. Win logic has required fields for its type
    # ------------------------------------------------------------------

    def _check_win_logic_fields(self, config: Dict[str, Any]) -> Dict[str, Any]:
        wt = config.get("win_type", "")
        if not wt:
            win_logic = config.get("win_logic", {})
            if isinstance(win_logic, dict):
                wt = win_logic.get("type", "")
        if wt not in WIN_TYPES:
            return _check("win_logic_fields", False, "Cannot check win fields: invalid win type.", "Fix win_type first.")

        win_entry = WIN_TYPES[wt]
        required = win_entry.get("required_fields", []) if isinstance(win_entry, dict) else []

        # Support both flat win_config and nested win_logic
        win_config = config.get("win_config", {})
        win_logic = config.get("win_logic", {})
        if isinstance(win_logic, dict) and not isinstance(win_config, dict):
            win_config = win_logic
        if not isinstance(win_config, dict):
            win_config = {}
        # Merge nested win_logic fields
        if isinstance(win_logic, dict):
            for k, v in win_logic.items():
                if k not in win_config and k != "type":
                    win_config[k] = v

        missing: List[str] = []
        for field_name in required:
            if field_name not in win_config or win_config[field_name] is None:
                missing.append(field_name)

        if missing:
            return _check("win_logic_fields", False,
                          f"Win type '{wt}' requires fields: {', '.join(missing)}.",
                          f"Add these fields to win_config: {', '.join(missing)}.")

        applies_to = win_entry.get("applies_to", []) if isinstance(win_entry, dict) else []
        gt = config.get("game_type", "")
        if applies_to and gt not in applies_to:
            result = _check("win_logic_fields", True,
                            f"Win type '{wt}' is typically used with {applies_to}, but game type is '{gt}'.",
                            f"Consider using a win type that matches your game type.")
            result["severity"] = "warning"
            return result

        return _check("win_logic_fields", True, f"All required win config fields present for '{wt}'.")

    # ------------------------------------------------------------------
    # 13. Economy
    # ------------------------------------------------------------------

    def _check_economy(self, config: Dict[str, Any]) -> Dict[str, Any]:
        # Support both flat and nested economy config
        reward = config.get("reward_per_win")
        fee = config.get("entry_fee")
        economy = config.get("economy", {})
        if isinstance(economy, dict):
            if reward is None:
                reward = economy.get("reward_per_win", 0)
            if fee is None:
                fee = economy.get("entry_fee", 0)

        if reward is None:
            reward = 0
        if fee is None:
            fee = 0

        try:
            reward = float(reward)
        except (ValueError, TypeError):
            return _check("economy", False, "reward_per_win must be a number.", "Set reward_per_win to a numeric value >= 0.")

        try:
            fee = float(fee)
        except (ValueError, TypeError):
            return _check("economy", False, "entry_fee must be a number.", "Set entry_fee to a numeric value >= 0.")

        if reward < 0:
            return _check("economy", False, "reward_per_win must be >= 0.", "Set a non-negative reward value.")
        if fee < 0:
            return _check("economy", False, "entry_fee must be >= 0.", "Set a non-negative entry fee.")

        if reward > 0 and fee > 0 and reward < fee:
            result = _check("economy", True,
                            f"Win reward ({reward}) is less than entry fee ({fee}). Players cannot profit.",
                            "Consider making the win reward >= entry fee.")
            result["severity"] = "warning"
            return result

        return _check("economy", True, f"Economy valid: fee={fee}, reward={reward}.")

    # ------------------------------------------------------------------
    # 14. Exploit conditions
    # ------------------------------------------------------------------

    def _check_exploit_conditions(self, config: Dict[str, Any]) -> Dict[str, Any]:
        reward = 0
        fee = 0
        economy = config.get("economy", {})
        try:
            reward = float(config.get("reward_per_win", economy.get("reward_per_win", 0) if isinstance(economy, dict) else 0))
        except (ValueError, TypeError):
            pass
        try:
            fee = float(config.get("entry_fee", economy.get("entry_fee", 0) if isinstance(economy, dict) else 0))
        except (ValueError, TypeError):
            pass

        issues: List[str] = []

        if reward > 100:
            issues.append(f"Win reward ({reward}) exceeds 100 SAR — potential exploit.")
        if fee > 50:
            issues.append(f"Entry fee ({fee}) exceeds 50 SAR — may deter players.")
        if reward > 0 and fee == 0 and reward > 10:
            issues.append(f"Free entry with high reward ({reward}) — potential farming exploit.")

        wt = config.get("win_type", "")
        if not wt:
            win_logic = config.get("win_logic", {})
            if isinstance(win_logic, dict):
                wt = win_logic.get("type", "")
        if wt == "highest_score":
            win_config = config.get("win_config", config.get("win_logic", {}))
            if not isinstance(win_config, dict) or "max_turns" not in win_config:
                issues.append("highest_score win type without max_turns — game may never end.")

        if issues:
            result = _check("exploit_conditions", True, "Potential exploit conditions detected: " + " ".join(issues),
                            "Review economy settings for balance and fairness.")
            result["severity"] = "warning"
            return result

        return _check("exploit_conditions", True, "No exploit conditions detected.")

    # ------------------------------------------------------------------
    # 15. Slug generation
    # ------------------------------------------------------------------

    def _check_slug_generation(self, config: Dict[str, Any]) -> Dict[str, Any]:
        name = config.get("game_name", "")
        if not name or not isinstance(name, str) or not name.strip():
            return _check("slug", False, "Cannot generate slug: game name is missing.", "Provide a game name first.")

        slug = self._generate_slug(name)

        if not slug:
            return _check("slug", False, "Cannot generate a valid slug from the game name.",
                          "Use a name with at least one alphanumeric character.")

        if not re.match(r'^[a-z0-9_]+$', slug):
            return _check("slug", False, f"Generated slug '{slug}' contains invalid characters.",
                          "Game name must contain at least some alphanumeric characters.")

        if len(slug) < 2:
            return _check("slug", False, f"Generated slug '{slug}' is too short (min 2 chars).",
                          "Use a longer game name.")

        if len(slug) > 50:
            return _check("slug", False, f"Generated slug '{slug}' is too long (max 50 chars).",
                          "Use a shorter game name.")

        return _check("slug_generation", True, f"Slug '{slug}' can be generated successfully.")

    @staticmethod
    def _generate_slug(name: str) -> str:
        """Convert a game name to a URL-safe slug (matches exporter logic)."""
        slug = name.lower().strip()
        slug = re.sub(r'[\s\-]+', '_', slug)
        slug = re.sub(r'[^a-z0-9_]', '', slug)
        slug = re.sub(r'_+', '_', slug)
        slug = slug.strip('_')
        return slug

    # ------------------------------------------------------------------
    # 16. Manifest compilation
    # ------------------------------------------------------------------

    def _check_manifest_compilation(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Verify that a manifest.json can be compiled from this config."""
        try:
            manifest = self._build_test_manifest(config)
            json_str = json.dumps(manifest, ensure_ascii=False)
            required_keys = {"slug", "name", "version", "description", "min_players",
                             "max_players", "mode", "board", "rewards"}
            missing_keys = required_keys - set(manifest.keys())
            if missing_keys:
                return _check("manifest_compilation", False,
                              f"Manifest missing required keys: {', '.join(missing_keys)}.",
                              "Check that all required fields are in the config.")
            if not isinstance(manifest.get("min_players"), int):
                return _check("manifest_compilation", False, "min_players must be an integer in manifest.", "Fix player count configuration.")
            if not isinstance(manifest.get("max_players"), int):
                return _check("manifest_compilation", False, "max_players must be an integer in manifest.", "Fix player count configuration.")

            return _check("manifest_compilation", True, f"Manifest compiled successfully ({len(json_str)} bytes).")

        except Exception as exc:
            return _check("manifest_compilation", False,
                          f"Failed to compile manifest: {exc}",
                          "Fix the configuration and try again.")

    def _build_test_manifest(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Build a manifest dict for testing (without writing to disk)."""
        name = config.get("game_name", "").strip()
        slug = self._generate_slug(name) if name else ""
        gt = config.get("game_type", "button_logic")
        board_enabled = config.get("board_enabled", False) or gt in self.BOARD_REQUIRED_TYPES
        board = config.get("board", {})
        if isinstance(board, dict) and board.get("enabled", False):
            board_enabled = True

        rows = config.get("board_rows", GAME_TYPES.get(gt, {}).get("default_rows", 3) if isinstance(GAME_TYPES.get(gt, {}), dict) else 3)
        cols = config.get("board_cols", GAME_TYPES.get(gt, {}).get("default_cols", 3) if isinstance(GAME_TYPES.get(gt, {}), dict) else 3)
        if isinstance(board, dict):
            rows = board.get("rows", rows)
            cols = board.get("cols", cols)

        try:
            rows = int(rows)
        except (ValueError, TypeError):
            rows = 3
        try:
            cols = int(cols)
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
        economy = config.get("economy", {})
        if isinstance(economy, dict):
            if reward == 0 and economy.get("reward_per_win"):
                try:
                    reward = float(economy["reward_per_win"])
                except (ValueError, TypeError):
                    pass
            if fee == 0 and economy.get("entry_fee"):
                try:
                    fee = float(economy["entry_fee"])
                except (ValueError, TypeError):
                    pass

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
            "win_condition": config.get("win_type", config.get("win_logic", {}).get("type", "") if isinstance(config.get("win_logic"), dict) else ""),
            "reward_sar": reward,
            "entry_fee_sar": fee,
            "allowed_chat_types": ["group", "private"],
            "required_files": ["logic.py"],
            "ui_template": {
                "header_enabled": True,
                "player_hud_enabled": True,
                "board_enabled": board_enabled,
                "status_enabled": True,
                "footer_enabled": True,
            },
            "hud_fields": ["name", "badge", "score", "role", "turn"],
            "buttons": config.get("buttons", []),
        }
        return manifest

    # ------------------------------------------------------------------
    # 17. Logic generation
    # ------------------------------------------------------------------

    def _check_logic_generation(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Verify that logic.py can be generated from this config."""
        try:
            logic_code = self._generate_test_logic(config)
            compile(logic_code, "<generated_logic>", "exec")
            return _check("logic_generation", True, f"Logic code compiled successfully ({len(logic_code)} bytes).")
        except SyntaxError as exc:
            return _check("logic_generation", False,
                          f"Generated logic has syntax error: {exc}",
                          "This may be a bug in the code generator. Please report it.")
        except Exception as exc:
            return _check("logic_generation", False,
                          f"Failed to generate logic: {exc}",
                          "Check the configuration for incompatible values.")

    def _generate_test_logic(self, config: Dict[str, Any]) -> str:
        """Generate a test logic.py string for compilation check."""
        name = config.get("game_name", "Untitled").strip()
        gt = config.get("game_type", "button_logic")
        board_enabled = config.get("board_enabled", False) or gt in self.BOARD_REQUIRED_TYPES
        board = config.get("board", {})
        if isinstance(board, dict) and board.get("enabled", False):
            board_enabled = True

        try:
            rows = int(config.get("board_rows", board.get("rows", 3) if isinstance(board, dict) else 3))
        except (ValueError, TypeError):
            rows = 3
        try:
            cols = int(config.get("board_cols", board.get("cols", 3) if isinstance(board, dict) else 3))
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

        code = textwrap.dedent(f'''\
            """Auto-generated game plugin for {name}."""
            import json

            EMPTY_CELL = "  "
            GAME_NAME = {repr(name)}
            BOARD_ROWS = {rows}
            BOARD_COLS = {cols}
            MIN_PLAYERS = {min_p}
            MAX_PLAYERS = {max_p}
            GAME_TYPE = {repr(gt)}
            BOARD_ENABLED = {board_enabled}

            def init_game(session):
                state = {{
                    "board": [[EMPTY_CELL for _ in range(BOARD_COLS)] for _ in range(BOARD_ROWS)] if BOARD_ENABLED else [],
                    "moves": [],
                    "winner": None,
                    "scores": {{}},
                }}
                session["state"] = state
                session["current_turn_index"] = 0
                session["current_phase"] = "playing"
                return session

            def render(session):
                return {{}}

            def handle_callback(session, user_id, action):
                return session

            def check_win(session):
                return None

            def serialize_state(session):
                return session.get("state", {{}})

            def deserialize_state(data):
                if isinstance(data, str):
                    data = json.loads(data)
                return data
        ''')
        return code

    # ------------------------------------------------------------------
    # 18. UI renderer compatibility
    # ------------------------------------------------------------------

    def _check_ui_renderer_compat(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Check that the configuration is compatible with the UI rendering engine."""
        issues: List[str] = []

        gt = config.get("game_type", "")
        board_enabled = config.get("board_enabled", False) or gt in self.BOARD_REQUIRED_TYPES
        board = config.get("board", {})
        if isinstance(board, dict) and board.get("enabled", False):
            board_enabled = True

        if board_enabled:
            rows = 0
            cols = 0
            try:
                rows = int(config.get("board_rows", board.get("rows", 0) if isinstance(board, dict) else 0))
            except (ValueError, TypeError):
                pass
            try:
                cols = int(config.get("board_cols", board.get("cols", 0) if isinstance(board, dict) else 0))
            except (ValueError, TypeError):
                pass

            if rows > 0 and cols > 0:
                total_cells = rows * cols
                if total_cells > 100:
                    issues.append(f"Total board cells ({total_cells}) exceeds Telegram button limit (100).")
                if cols > 8:
                    issues.append(f"Board columns ({cols}) exceed Telegram's 8-buttons-per-row limit.")

        buttons = config.get("buttons", [])
        if isinstance(buttons, list):
            if len(buttons) > 12:
                issues.append(f"Action buttons ({len(buttons)}) may exceed combined Telegram button limit with board cells.")

        if gt not in GAME_TYPES:
            issues.append(f"Game type '{gt}' is not recognized — renderer may not support it.")

        wt = config.get("win_type", "")
        if not wt and isinstance(config.get("win_logic"), dict):
            wt = config.get("win_logic", {}).get("type", "")
        if wt and wt in WIN_TYPES:
            win_entry = WIN_TYPES[wt]
            applies_to = win_entry.get("applies_to", []) if isinstance(win_entry, dict) else []
            if applies_to and gt not in applies_to:
                issues.append(f"Win type '{wt}' is not designed for game type '{gt}'.")

        if issues:
            result = _check("ui_renderer_compat", True,
                            "UI compatibility warnings: " + " ".join(issues),
                            "Review board dimensions and button count for Telegram compatibility.")
            result["severity"] = "warning"
            return result

        return _check("ui_renderer_compat", True, "Configuration is compatible with the UI renderer.")
