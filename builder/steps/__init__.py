"""
Builder step constants and enumerations.

Defines the state machine steps, game types, effect types, and win types
used throughout the Game Builder system.
"""

HOME = "HOME"
GAME_INFO = "GAME_INFO"
GAME_TYPE = "GAME_TYPE"
PLAYER_CONFIG = "PLAYER_CONFIG"
BUTTON_DESIGN = "BUTTON_DESIGN"
BOARD_DESIGN = "BOARD_DESIGN"
WIN_LOGIC = "WIN_LOGIC"
ECONOMY_SETUP = "ECONOMY_SETUP"
PREVIEW = "PREVIEW"
VALIDATION = "VALIDATION"
PUBLISH = "PUBLISH"

STATE_ORDER = [
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
]

GAME_TYPES = {
    "grid_strategy": {
        "name": "Grid Strategy",
        "icon": "\u265f\ufe0f",
        "board_required": True,
        "description": "Strategic grid-based games like chess, XO",
    },
    "turn_based": {
        "name": "Turn Based",
        "icon": "\U0001f504",
        "board_required": False,
        "description": "Classic turn-based games",
    },
    "button_logic": {
        "name": "Button Logic",
        "icon": "\U0001f518",
        "board_required": False,
        "description": "Logic and decision games using buttons",
    },
    "hidden_role": {
        "name": "Hidden Role",
        "icon": "\U0001f3ad",
        "board_required": False,
        "description": "Secret role and deduction games",
    },
    "path_building": {
        "name": "Path Building",
        "icon": "\U0001f6e4\ufe0f",
        "board_required": True,
        "description": "Build paths and routes to win",
    },
    "elimination": {
        "name": "Elimination",
        "icon": "\U0001f4a5",
        "board_required": False,
        "description": "Last player standing wins",
    },
    "custom": {
        "name": "Custom Template",
        "icon": "\u2728",
        "board_required": True,
        "description": "Fully customizable game",
    },
}

EFFECT_TYPES = {
    "MOVE": "Move piece or player",
    "SCORE": "Add or modify score",
    "ATTACK": "Attack another player",
    "DEFEND": "Defend against attack",
    "REVEAL": "Reveal hidden information",
    "BLOCK": "Block an action or cell",
    "RANDOM": "Random outcome",
    "PASS_TURN": "Pass the turn",
    "TOGGLE": "Toggle a state",
    "SWITCH": "Switch position or role",
    "LOCK": "Lock an element",
    "UNLOCK": "Unlock an element",
}

WIN_TYPES = {
    "target_score": {
        "name": "Target Score",
        "label": "Target Score",
        "description": "First to reach target score",
        "required_fields": ["target_score"],
        "applies_to": ["score_attack", "button_logic", "trivia", "card_game", "turn_based"],
    },
    "elimination": {
        "name": "Elimination",
        "label": "Elimination",
        "description": "Last player standing",
        "required_fields": ["initial_health"],
        "applies_to": ["elimination"],
    },
    "path_completion": {
        "name": "Path Completion",
        "label": "Path Completion",
        "description": "Complete a path first",
        "required_fields": [],
        "applies_to": ["path_building"],
    },
    "last_survivor": {
        "name": "Last Survivor",
        "label": "Last Survivor",
        "description": "Survive until end",
        "required_fields": ["initial_health"],
        "applies_to": ["elimination"],
    },
    "highest_score": {
        "name": "Highest Score",
        "label": "Highest Score",
        "description": "Highest score when game ends",
        "required_fields": ["max_turns"],
        "applies_to": ["score_attack", "button_logic", "trivia", "card_game", "turn_based"],
    },
    "first_to_target": {
        "name": "First to Target",
        "label": "First to Target",
        "description": "First to achieve target condition",
        "required_fields": ["target_score"],
        "applies_to": ["score_attack", "button_logic", "trivia", "card_game"],
    },
    "line_match": {
        "name": "Line Match",
        "label": "Line Match",
        "description": "First to place N in a row/column/diagonal wins",
        "required_fields": ["line_length"],
        "applies_to": ["grid_strategy"],
    },
    "board_full": {
        "name": "Board Full",
        "label": "Board Full",
        "description": "Game ends when board is full; best placement wins",
        "required_fields": [],
        "applies_to": ["grid_strategy"],
    },
    "majority_control": {
        "name": "Majority Control",
        "label": "Majority Control",
        "description": "Control more than half the board cells to win",
        "required_fields": [],
        "applies_to": ["grid_strategy", "path_building"],
    },
    "role_reveal": {
        "name": "Role Reveal",
        "label": "Role Reveal",
        "description": "Identify or eliminate specific hidden roles",
        "required_fields": ["winning_roles"],
        "applies_to": ["hidden_role"],
    },
    "custom": {
        "name": "Custom Win Rules",
        "label": "Custom Win Rules",
        "description": "Custom win rules",
        "required_fields": [],
        "applies_to": ["custom"],
    },
}
