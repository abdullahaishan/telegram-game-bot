"""
Game Session Manager

Manages the full lifecycle of multiplayer game sessions inside Telegram:
creation, player management, turn tracking, live message updates,
callback routing, timeouts, and cleanup.

All public methods are thread-safe.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from telegram import Bot, InlineKeyboardMarkup

from .renderer import GameRenderer
from .plugin_loader import GamePlugin, PluginLoader

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Session states
class SessionState(str, Enum):
    WAITING = "waiting"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# Default timeouts (seconds)
DEFAULT_WAIT_TIMEOUT = 600  # 10 min waiting for players
DEFAULT_TURN_TIMEOUT = 120  # 2 min per turn
DEFAULT_STALE_THRESHOLD = 3600  # 1 hour of inactivity = stale

# Maximum players per session (safety cap)
MAX_PLAYERS_PER_SESSION = 50


# ---------------------------------------------------------------------------
# Player record
# ---------------------------------------------------------------------------

@dataclass
class Player:
    """Represents a player inside a session."""

    user_id: int
    name: str
    badge: str = ""
    balance: float = 0.0
    wins: int = 0
    role: str = ""
    is_turn: bool = False
    is_alive: bool = True
    score: int = 0
    color: str = ""

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "name": self.name,
            "badge": self.badge,
            "balance": self.balance,
            "wins": self.wins,
            "role": self.role,
            "is_turn": self.is_turn,
            "is_alive": self.is_alive,
            "score": self.score,
            "color": self.color,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Player":
        return cls(
            user_id=d["user_id"],
            name=d.get("name", "???"),
            badge=d.get("badge", ""),
            balance=d.get("balance", 0.0),
            wins=d.get("wins", 0),
            role=d.get("role", ""),
            is_turn=d.get("is_turn", False),
            is_alive=d.get("is_alive", True),
            score=d.get("score", 0),
            color=d.get("color", ""),
        )


# ---------------------------------------------------------------------------
# Game session
# ---------------------------------------------------------------------------

@dataclass
class GameSession:
    """Full state for a single game session."""

    session_id: str
    game_slug: str
    chat_id: int
    creator_id: int
    mode: str
    visibility: str
    state: SessionState = SessionState.WAITING
    players: list[Player] = field(default_factory=list)
    game_state: dict = field(default_factory=dict)
    message_id: Optional[int] = None  # Telegram message ID for edits
    turn_index: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    turn_started_at: float = 0.0
    winner_id: Optional[int] = None
    countdown: Optional[int] = None
    activity_log: list[str] = field(default_factory=list)

    # ---- Serialisation helpers ----

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "game_slug": self.game_slug,
            "chat_id": self.chat_id,
            "creator_id": self.creator_id,
            "mode": self.mode,
            "visibility": self.visibility,
            "state": self.state.value,
            "players": [p.to_dict() for p in self.players],
            "game_state": self.game_state,
            "message_id": self.message_id,
            "turn_index": self.turn_index,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turn_started_at": self.turn_started_at,
            "winner_id": self.winner_id,
            "countdown": self.countdown,
            "activity_log": self.activity_log,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GameSession":
        players = [Player.from_dict(p) for p in d.get("players", [])]
        return cls(
            session_id=d["session_id"],
            game_slug=d["game_slug"],
            chat_id=d["chat_id"],
            creator_id=d["creator_id"],
            mode=d.get("mode", "classic"),
            visibility=d.get("visibility", "public"),
            state=SessionState(d.get("state", "waiting")),
            players=players,
            game_state=d.get("game_state", {}),
            message_id=d.get("message_id"),
            turn_index=d.get("turn_index", 0),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            turn_started_at=d.get("turn_started_at", 0.0),
            winner_id=d.get("winner_id"),
            countdown=d.get("countdown"),
            activity_log=d.get("activity_log", []),
        )

    def touch(self) -> None:
        """Update ``updated_at`` to now."""
        self.updated_at = time.time()


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------

class SessionManager:
    """
    Thread-safe manager for all active game sessions.

    Parameters
    ----------
    bot : telegram.Bot
        The Telegram Bot instance for sending / editing messages.
    plugin_loader : PluginLoader
        Loaded game-plugin registry.
    renderer : GameRenderer | None
        Custom renderer; defaults to ``GameRenderer()``.
    db_path : str | None
        If provided, session state is persisted to this SQLite file.
        Pass ``None`` for in-memory only (default).
    wait_timeout : int
        Seconds before a WAITING session is considered stale.
    turn_timeout : int
        Seconds per turn before auto-skip.
    stale_threshold : int
        Seconds of inactivity before any session is considered stale.
    """

    def __init__(
        self,
        bot: Bot,
        plugin_loader: PluginLoader,
        renderer: Optional[GameRenderer] = None,
        db_path: Optional[str] = None,
        wait_timeout: int = DEFAULT_WAIT_TIMEOUT,
        turn_timeout: int = DEFAULT_TURN_TIMEOUT,
        stale_threshold: int = DEFAULT_STALE_THRESHOLD,
    ):
        self.bot = bot
        self.plugin_loader = plugin_loader
        self.renderer = renderer or GameRenderer()
        self.db_path = db_path

        self.wait_timeout = wait_timeout
        self.turn_timeout = turn_timeout
        self.stale_threshold = stale_threshold

        # In-memory session store: session_id -> GameSession
        self._sessions: dict[str, GameSession] = {}
        # Index: user_id -> set of session_ids the user is in
        self._user_sessions: dict[int, set[str]] = {}

        # Thread safety
        self._lock = threading.RLock()

        # Background cleanup thread
        self._cleanup_running = False
        self._cleanup_thread: Optional[threading.Thread] = None

        # Optional persistence
        self._db_conn = None
        if self.db_path:
            self._init_db()

    # ------------------------------------------------------------------
    # Database persistence (SQLite)
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Initialise the SQLite database for session persistence."""
        import sqlite3

        self._db_conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        self._db_conn.commit()

    def _db_save_session(self, session: GameSession) -> None:
        """Persist a single session to the database."""
        if self._db_conn is None:
            return
        data = json.dumps(session.to_dict())
        self._db_conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, data) VALUES (?, ?)",
            (session.session_id, data),
        )
        self._db_conn.commit()

    def _db_delete_session(self, session_id: str) -> None:
        """Remove a session from the database."""
        if self._db_conn is None:
            return
        self._db_conn.execute(
            "DELETE FROM sessions WHERE session_id = ?", (session_id,)
        )
        self._db_conn.commit()

    def _db_load_all_sessions(self) -> dict[str, GameSession]:
        """Load all persisted sessions from the database."""
        if self._db_conn is None:
            return {}
        cursor = self._db_conn.execute("SELECT session_id, data FROM sessions")
        sessions: dict[str, GameSession] = {}
        for row in cursor:
            try:
                sessions[row[0]] = GameSession.from_dict(json.loads(row[1]))
            except Exception:
                logger.exception("Failed to load session %s from DB", row[0])
        return sessions

    # ------------------------------------------------------------------
    # Lifecycle: start / stop cleanup daemon
    # ------------------------------------------------------------------

    def start_cleanup_daemon(self, interval: int = 60) -> None:
        """
        Start a background thread that periodically cleans up stale sessions.

        Parameters
        ----------
        interval : int
            Seconds between cleanup runs.
        """
        if self._cleanup_running:
            return
        self._cleanup_running = True
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            args=(interval,),
            daemon=True,
            name="session-cleanup",
        )
        self._cleanup_thread.start()
        logger.info("Session cleanup daemon started (interval=%ds)", interval)

    def stop_cleanup_daemon(self) -> None:
        """Signal the cleanup daemon to stop and wait for it."""
        self._cleanup_running = False
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5)
        logger.info("Session cleanup daemon stopped")

    def _cleanup_loop(self, interval: int) -> None:
        while self._cleanup_running:
            try:
                self.cleanup_stale_sessions()
            except Exception:
                logger.exception("Error during session cleanup")
            # Sleep in small increments so we can exit quickly
            for _ in range(interval):
                if not self._cleanup_running:
                    return
                time.sleep(1)

    # ------------------------------------------------------------------
    # Session creation
    # ------------------------------------------------------------------

    def create_session(
        self,
        game_slug: str,
        chat_id: int,
        creator_id: int,
        mode: str = "classic",
        visibility: str = "public",
    ) -> GameSession:
        """
        Create a new game session and return it.

        The session starts in ``WAITING`` state.  The creator is
        automatically added as the first player.

        Raises ``ValueError`` if the game slug is unknown.
        """
        plugin = self.plugin_loader.get_game(game_slug)
        if plugin is None:
            raise ValueError(f"Unknown game slug: {game_slug}")

        session_id = self._generate_room_id()

        session = GameSession(
            session_id=session_id,
            game_slug=game_slug,
            chat_id=chat_id,
            creator_id=creator_id,
            mode=mode,
            visibility=visibility,
            state=SessionState.WAITING,
        )

        # Add creator as first player
        creator_player = Player(
            user_id=creator_id,
            name=str(creator_id),  # will be enriched on first callback
        )
        session.players.append(creator_player)
        session.activity_log.append(f"Room created by {creator_id}")

        with self._lock:
            self._sessions[session_id] = session
            self._user_sessions.setdefault(creator_id, set()).add(session_id)
            self._db_save_session(session)

        logger.info(
            "Session created: %s (game=%s, chat=%d, creator=%d)",
            session_id,
            game_slug,
            chat_id,
            creator_id,
        )

        return session

    # ------------------------------------------------------------------
    # Player management
    # ------------------------------------------------------------------

    def join_session(self, session_id: str, user_id: int) -> GameSession:
        """
        Add a player to a WAITING session.

        Raises ``SessionError`` on various failure conditions.
        """
        with self._lock:
            session = self._get_session_or_raise(session_id)

            if session.state != SessionState.WAITING:
                raise SessionError("Cannot join: game is already active")

            # Check duplicate
            if any(p.user_id == user_id for p in session.players):
                raise SessionError("You are already in this session")

            plugin = self._get_plugin_or_raise(session.game_slug)

            # Player cap
            if len(session.players) >= plugin.max_players:
                raise SessionError(
                    f"Session is full (max {plugin.max_players} players)"
                )

            if len(session.players) >= MAX_PLAYERS_PER_SESSION:
                raise SessionError("Session player limit reached")

            player = Player(user_id=user_id, name=str(user_id))
            session.players.append(player)
            session.activity_log.append(f"Player {user_id} joined")
            session.touch()

            self._user_sessions.setdefault(user_id, set()).add(session_id)
            self._db_save_session(session)

            # Auto-start if minimum players met (optional – can be triggered manually)
            # We don't auto-start; the creator must call start_session().

            return session

    def leave_session(self, session_id: str, user_id: int) -> GameSession:
        """
        Remove a player from a session.

        If the creator leaves a WAITING session, the session is cancelled.
        If a player leaves an ACTIVE session, they are marked as not alive
        (but remain in the player list for accounting).
        """
        with self._lock:
            session = self._get_session_or_raise(session_id)

            player = self._find_player(session, user_id)
            if player is None:
                raise SessionError("You are not in this session")

            if session.state == SessionState.WAITING:
                session.players.remove(player)
                session.activity_log.append(f"Player {user_id} left")

                # Clean up user index
                user_sessions = self._user_sessions.get(user_id)
                if user_sessions:
                    user_sessions.discard(session_id)

                # If creator leaves during WAITING → cancel
                if user_id == session.creator_id or len(session.players) == 0:
                    session.state = SessionState.CANCELLED
                    session.activity_log.append("Session cancelled (creator left)")
                    self._db_save_session(session)
                    return session

                # Transfer ownership
                if user_id == session.creator_id and session.players:
                    session.creator_id = session.players[0].user_id

            elif session.state == SessionState.ACTIVE:
                # Mark as dead rather than remove (preserve turn order)
                player.is_alive = False
                player.is_turn = False
                session.activity_log.append(f"Player {user_id} disconnected")

                # If it was their turn, advance
                if player.is_turn:
                    self._advance_turn(session)

            session.touch()
            self._db_save_session(session)
            return session

    # ------------------------------------------------------------------
    # Game start
    # ------------------------------------------------------------------

    def start_session(self, session_id: str) -> GameSession:
        """
        Transition a WAITING session to ACTIVE.

        Calls the game plugin's ``init_game`` to bootstrap game state,
        assigns the first turn, and sends the initial game message.
        """
        with self._lock:
            session = self._get_session_or_raise(session_id)

            if session.state != SessionState.WAITING:
                raise SessionError("Game has already started")

            plugin = self._get_plugin_or_raise(session.game_slug)

            if len(session.players) < plugin.min_players:
                raise SessionError(
                    f"Need at least {plugin.min_players} players to start"
                )

            # Initialise game state via plugin
            player_dicts = [p.to_dict() for p in session.players]
            init_state = plugin.init_game(
                players=player_dicts,
                mode=session.mode,
                settings={},
            )
            session.game_state = init_state if isinstance(init_state, dict) else {}

            # Sync player data from game_state if the plugin returns it
            if "players" in session.game_state:
                self._sync_players_from_state(session)

            # Mark first player's turn
            if session.players:
                session.turn_index = 0
                session.players[0].is_turn = True
                session.turn_started_at = time.time()

            session.state = SessionState.ACTIVE
            session.activity_log.append("Game started!")
            session.touch()
            self._db_save_session(session)

            return session

    # ------------------------------------------------------------------
    # Callback handling
    # ------------------------------------------------------------------

    def handle_game_callback(
        self, session_id: str, user_id: int, action: str
    ) -> GameSession:
        """
        Route an inline-keyboard callback to the game plugin.

        After the plugin processes the callback, the session state is
        updated, win conditions are checked, and the live message is
        refreshed.
        """
        with self._lock:
            session = self._get_session_or_raise(session_id)

            if session.state != SessionState.ACTIVE:
                raise SessionError("Game is not active")

            plugin = self._get_plugin_or_raise(session.game_slug)

            # Build callback context for the plugin
            cb_context = {
                "action": action,
                "user_id": user_id,
                "session_id": session_id,
                "game_state": session.game_state,
                "players": [p.to_dict() for p in session.players],
                "turn_index": session.turn_index,
            }

            # Delegate to game logic
            result = plugin.handle_callback(cb_context)

            # Apply returned state
            if isinstance(result, dict):
                if "game_state" in result:
                    session.game_state = result["game_state"]
                if "players" in result:
                    self._apply_player_updates(session, result["players"])
                if "activity_log" in result:
                    # Plugin can append log entries
                    new_entries = result["activity_log"]
                    if isinstance(new_entries, list):
                        session.activity_log.extend(new_entries)
                if "advance_turn" in result and result["advance_turn"]:
                    self._advance_turn(session)
                if "countdown" in result:
                    session.countdown = result["countdown"]

            # Sync players from game_state if plugin keeps them there
            if "players" in session.game_state:
                self._sync_players_from_state(session)

            # Check win condition
            win_result = plugin.check_win(session.game_state)
            if win_result:
                self._handle_win(session, win_result)
            else:
                session.touch()
                self._db_save_session(session)

            return session

    # ------------------------------------------------------------------
    # Live message update
    # ------------------------------------------------------------------

    def update_session_message(self, session_id: str) -> None:
        """
        Re-render the session's game message and edit the Telegram message
        in-place via ``editMessageText``.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                logger.warning("Cannot update message: session %s not found", session_id)
                return

            if session.message_id is None:
                logger.warning("Cannot update message: no message_id for session %s", session_id)
                return

        # Build render context outside the lock (renderer is pure)
        context = self._build_render_context(session)
        text, reply_markup = self.renderer.render(context)

        try:
            self.bot.edit_message_text(
                chat_id=session.chat_id,
                message_id=session.message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=None,  # Unicode art – no Markdown needed
            )
        except Exception:
            # Telegram may raise BadRequest if message content hasn't changed
            logger.debug(
                "edit_message_text failed for session %s (may be identical content)",
                session_id,
            )

    def send_initial_message(self, session_id: str) -> None:
        """
        Render and send the initial game message, then store the
        ``message_id`` on the session.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return

        context = self._build_render_context(session)
        text, reply_markup = self.renderer.render(context)

        try:
            msg = self.bot.send_message(
                chat_id=session.chat_id,
                text=text,
                reply_markup=reply_markup,
            )
            with self._lock:
                session.message_id = msg.message_id
                self._db_save_session(session)
        except Exception:
            logger.exception(
                "Failed to send initial message for session %s", session_id
            )

    # ------------------------------------------------------------------
    # Session end
    # ------------------------------------------------------------------

    def end_session(self, session_id: str, winner_id: Optional[int] = None) -> GameSession:
        """
        End a session, optionally recording the winner.

        Distributes rewards via the game plugin, updates player stats,
        and refreshes the live message one last time.
        """
        with self._lock:
            session = self._get_session_or_raise(session_id)

            if session.state not in (SessionState.ACTIVE, SessionState.WAITING):
                raise SessionError("Session is not active")

            plugin = self._get_plugin_or_raise(session.game_slug)
            rewards = plugin.rewards

            session.winner_id = winner_id
            session.state = SessionState.COMPLETED

            # Reset all turn markers
            for p in session.players:
                p.is_turn = False

            # Distribute rewards
            entry_fee = float(rewards.get("entry_fee", 0))
            win_reward = float(rewards.get("win_reward", 0))

            if winner_id is not None:
                winner = self._find_player(session, winner_id)
                if winner:
                    winner.balance += win_reward
                    winner.wins += 1
                    session.activity_log.append(
                        f"🏆 {winner.name} wins! +{win_reward:.0f} coins"
                    )
                # Losers already paid the entry fee (deducted on join or start)
            else:
                # Draw / cancelled – refund entry fees
                for p in session.players:
                    p.balance += entry_fee
                session.activity_log.append("Game ended with no winner – fees refunded")

            session.touch()
            self._db_save_session(session)

            # Update the Telegram message one last time
            # (Do outside lock to avoid deadlock on bot API call)
            # We'll call update after releasing the lock

        # Refresh message outside lock
        try:
            self.update_session_message(session_id)
        except Exception:
            logger.exception("Failed to update final message for session %s", session_id)

        return session

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> Optional[GameSession]:
        """Return a session by ID, or ``None``."""
        with self._lock:
            return self._sessions.get(session_id)

    def get_active_sessions(self) -> list[GameSession]:
        """Return all sessions that are WAITING or ACTIVE."""
        with self._lock:
            return [
                s
                for s in self._sessions.values()
                if s.state in (SessionState.WAITING, SessionState.ACTIVE)
            ]

    def get_sessions_for_user(self, user_id: int) -> list[GameSession]:
        """Return all sessions a user is part of."""
        with self._lock:
            session_ids = self._user_sessions.get(user_id, set())
            return [
                self._sessions[sid]
                for sid in session_ids
                if sid in self._sessions
            ]

    def get_sessions_for_chat(self, chat_id: int) -> list[GameSession]:
        """Return all sessions in a given chat."""
        with self._lock:
            return [
                s for s in self._sessions.values() if s.chat_id == chat_id
            ]

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_stale_sessions(self) -> int:
        """
        Remove sessions that have exceeded inactivity thresholds.

        - WAITING sessions older than ``wait_timeout`` are cancelled.
        - ACTIVE sessions with no activity for ``stale_threshold`` are
          cancelled.
        - COMPLETED / CANCELLED sessions older than ``stale_threshold``
          are purged from memory.

        Returns the number of sessions cleaned up.
        """
        now = time.time()
        cleaned = 0

        with self._lock:
            to_cancel: list[str] = []
            to_purge: list[str] = []

            for sid, session in self._sessions.items():
                age = now - session.updated_at

                if session.state == SessionState.WAITING:
                    if age > self.wait_timeout:
                        to_cancel.append(sid)

                elif session.state == SessionState.ACTIVE:
                    if age > self.stale_threshold:
                        to_cancel.append(sid)
                    # Also check per-turn timeout
                    elif (
                        session.turn_started_at > 0
                        and (now - session.turn_started_at) > self.turn_timeout
                    ):
                        # Auto-advance turn on timeout
                        self._advance_turn(session)
                        session.activity_log.append("Turn auto-advanced (timeout)")
                        self._db_save_session(session)

                elif session.state in (SessionState.COMPLETED, SessionState.CANCELLED):
                    if age > self.stale_threshold:
                        to_purge.append(sid)

            # Cancel stale active / waiting sessions
            for sid in to_cancel:
                session = self._sessions[sid]
                session.state = SessionState.CANCELLED
                session.activity_log.append("Session cancelled (inactivity timeout)")
                session.touch()
                self._db_save_session(session)
                cleaned += 1

            # Purge old completed / cancelled sessions from memory
            for sid in to_purge:
                session = self._sessions.pop(sid, None)
                if session:
                    # Clean user index
                    for p in session.players:
                        user_sessions = self._user_sessions.get(p.user_id)
                        if user_sessions:
                            user_sessions.discard(sid)
                    self._db_delete_session(sid)
                    cleaned += 1

        if cleaned:
            logger.info("Cleaned up %d stale sessions", cleaned)

        return cleaned

    # ------------------------------------------------------------------
    # Turn management
    # ------------------------------------------------------------------

    def _advance_turn(self, session: GameSession) -> None:
        """
        Advance the turn to the next alive player (round-robin).

        Must be called while holding ``self._lock``.
        """
        if not session.players:
            return

        # Clear current turn
        for p in session.players:
            p.is_turn = False

        # Find next alive player
        n = len(session.players)
        for offset in range(1, n + 1):
            idx = (session.turn_index + offset) % n
            candidate = session.players[idx]
            if candidate.is_alive:
                session.turn_index = idx
                candidate.is_turn = True
                session.turn_started_at = time.time()
                session.activity_log.append(f"Turn: {candidate.name}")
                session.touch()
                return

        # No alive players found – game should end
        logger.warning("No alive players in session %s – marking completed", session.session_id)
        session.state = SessionState.COMPLETED

    # ------------------------------------------------------------------
    # Win handling
    # ------------------------------------------------------------------

    def _handle_win(self, session: GameSession, win_result: Any) -> None:
        """
        Process a win condition returned by the plugin.

        ``win_result`` can be:
        - An int (user_id of the winner)
        - A dict with keys like ``{"winner_id": int, ...}``
        - A list of user_ids (tie)
        - ``True`` (game over, no specific winner)
        """
        winner_id: Optional[int] = None

        if isinstance(win_result, int):
            winner_id = win_result
        elif isinstance(win_result, dict):
            winner_id = win_result.get("winner_id")
        elif isinstance(win_result, list) and win_result:
            # Tie – pick the first for rewards; log all
            winner_id = win_result[0]
            tied_names = []
            for uid in win_result:
                p = self._find_player(session, uid)
                if p:
                    tied_names.append(p.name)
            if tied_names:
                session.activity_log.append(f"🏆 Tie between: {', '.join(tied_names)}")
        elif win_result is True:
            # Game over with no winner
            winner_id = None

        # End the session (will distribute rewards)
        self._do_end_session_unlocked(session, winner_id)

    def _do_end_session_unlocked(
        self, session: GameSession, winner_id: Optional[int]
    ) -> None:
        """
        Internal: end session.  Must be called while holding ``self._lock``.
        """
        plugin = self._get_plugin_or_raise(session.game_slug)
        rewards = plugin.rewards

        session.winner_id = winner_id
        session.state = SessionState.COMPLETED

        for p in session.players:
            p.is_turn = False

        entry_fee = float(rewards.get("entry_fee", 0))
        win_reward = float(rewards.get("win_reward", 0))

        if winner_id is not None:
            winner = self._find_player(session, winner_id)
            if winner:
                winner.balance += win_reward
                winner.wins += 1
                session.activity_log.append(
                    f"🏆 {winner.name} wins! +{win_reward:.0f} coins"
                )
        else:
            for p in session.players:
                p.balance += entry_fee
            session.activity_log.append("Game ended – fees refunded")

        session.touch()
        self._db_save_session(session)

    # ------------------------------------------------------------------
    # Player synchronisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_player(session: GameSession, user_id: int) -> Optional[Player]:
        """Find a player by user_id."""
        for p in session.players:
            if p.user_id == user_id:
                return p
        return None

    @staticmethod
    def _sync_players_from_state(session: GameSession) -> None:
        """
        Merge player data from ``session.game_state["players"]`` into the
        session's ``Player`` objects.

        The game plugin may update balance, role, score, etc. in its state.
        """
        state_players = session.game_state.get("players")
        if not isinstance(state_players, list):
            return

        for sp in state_players:
            if not isinstance(sp, dict):
                continue
            uid = sp.get("user_id")
            if uid is None:
                continue
            for p in session.players:
                if p.user_id == uid:
                    if "name" in sp:
                        p.name = sp["name"]
                    if "badge" in sp:
                        p.badge = sp["badge"]
                    if "balance" in sp:
                        p.balance = float(sp["balance"])
                    if "wins" in sp:
                        p.wins = int(sp["wins"])
                    if "role" in sp:
                        p.role = sp["role"]
                    if "is_turn" in sp:
                        p.is_turn = bool(sp["is_turn"])
                    if "is_alive" in sp:
                        p.is_alive = bool(sp["is_alive"])
                    if "score" in sp:
                        p.score = int(sp["score"])
                    if "color" in sp:
                        p.color = sp["color"]
                    break

    @staticmethod
    def _apply_player_updates(session: GameSession, updates: list[dict]) -> None:
        """Apply a list of player-update dicts from the plugin callback result."""
        for upd in updates:
            uid = upd.get("user_id")
            if uid is None:
                continue
            for p in session.players:
                if p.user_id == uid:
                    for key in (
                        "name", "badge", "balance", "wins", "role",
                        "is_turn", "is_alive", "score", "color",
                    ):
                        if key in upd:
                            setattr(p, key, upd[key])
                    break

    # ------------------------------------------------------------------
    # Render context builder
    # ------------------------------------------------------------------

    def _build_render_context(self, session: GameSession) -> dict:
        """
        Construct the full render context dict from a GameSession.

        This merges the session's structural data with the game's
        internal state (as provided by the plugin's ``render`` method).
        """
        plugin = self._get_plugin_or_raise(session.game_slug)

        # Ask the plugin for its board / extra state
        render_data: dict = {}
        try:
            result = plugin.render(session.game_state)
            if isinstance(result, dict):
                render_data = result
        except Exception:
            logger.exception(
                "Plugin render() failed for %s", session.game_slug
            )

        # --- Header ---
        header = {
            "game_name": plugin.name,
            "room_id": session.session_id,
            "mode": session.mode,
            "visibility": session.visibility,
            "status": session.state.value,
        }
        # Allow plugin to override
        header.update(render_data.get("header", {}))

        # --- Players ---
        players = [p.to_dict() for p in session.players]
        # Plugin may supply enriched player data
        if "players" in render_data:
            players = render_data["players"]

        # --- Board ---
        board = render_data.get("board", {})

        # --- State ---
        state_section = {
            "phase": session.game_state.get("phase", ""),
            "turn_owner": "",
            "countdown": session.countdown,
            "rules_reminder": render_data.get("rules_reminder", ""),
            "win_condition": render_data.get("win_condition", ""),
            "activity_log": session.activity_log,
        }
        # Determine turn owner
        for p in session.players:
            if p.is_turn:
                state_section["turn_owner"] = p.name
                break
        state_section.update(render_data.get("state", {}))

        # --- Footer ---
        footer = render_data.get("footer", {})
        if "actions" not in footer:
            footer["actions"] = self._default_actions(session)
        if "navigation" not in footer:
            footer["navigation"] = self._default_navigation(session)

        return {
            "header": header,
            "players": players,
            "board": board,
            "state": state_section,
            "footer": footer,
        }

    @staticmethod
    def _default_actions(session: GameSession) -> list[dict]:
        """Generate default action buttons based on session state."""
        actions: list[dict] = []

        if session.state == SessionState.WAITING:
            actions.append(
                {"label": "▶️ Start", "callback": f"sess:start:{session.session_id}", "visible": True}
            )
            actions.append(
                {"label": "🚪 Leave", "callback": f"sess:leave:{session.session_id}", "visible": True}
            )
        elif session.state == SessionState.ACTIVE:
            actions.append(
                {"label": "🔄 Refresh", "callback": f"sess:refresh:{session.session_id}", "visible": True}
            )
            actions.append(
                {"label": "🏳️ Surrender", "callback": f"sess:surrender:{session.session_id}", "visible": True}
            )
        elif session.state == SessionState.COMPLETED:
            actions.append(
                {"label": "🔄 Rematch", "callback": f"sess:rematch:{session.session_id}", "visible": True}
            )

        return actions

    @staticmethod
    def _default_navigation(session: GameSession) -> list[dict]:
        """Generate default navigation buttons."""
        return [
            {"label": "🏠 Menu", "callback": "nav:menu"},
            {"label": "📊 Stats", "callback": f"nav:stats:{session.session_id}"},
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_session_or_raise(self, session_id: str) -> GameSession:
        """Look up a session or raise ``SessionError``."""
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionError(f"Session not found: {session_id}")
        return session

    def _get_plugin_or_raise(self, game_slug: str) -> GamePlugin:
        """Look up a game plugin or raise ``SessionError``."""
        plugin = self.plugin_loader.get_game(game_slug)
        if plugin is None:
            raise SessionError(f"Game plugin not loaded: {game_slug}")
        return plugin

    @staticmethod
    def _generate_room_id() -> str:
        """Generate a short, unique room ID (8 hex chars)."""
        return uuid.uuid4().hex[:8].upper()

    # ------------------------------------------------------------------
    # Persistence: load on startup
    # ------------------------------------------------------------------

    def load_persisted_sessions(self) -> int:
        """
        Load all sessions from the database into memory.

        Call this once at startup if using a database backend.

        Returns the number of sessions loaded.
        """
        if self._db_conn is None:
            return 0

        sessions = self._db_load_all_sessions()
        count = 0
        with self._lock:
            for sid, session in sessions.items():
                # Skip already-cancelled old sessions
                if session.state in (SessionState.CANCELLED, SessionState.COMPLETED):
                    self._db_delete_session(sid)
                    continue
                self._sessions[sid] = session
                for p in session.players:
                    self._user_sessions.setdefault(p.user_id, set()).add(sid)
                count += 1

        logger.info("Loaded %d persisted sessions", count)
        return count

    # ------------------------------------------------------------------
    # Enrich player info (called from bot handlers)
    # ------------------------------------------------------------------

    def enrich_player(
        self,
        session_id: str,
        user_id: int,
        name: str,
        badge: str = "",
        balance: float = 0.0,
        wins: int = 0,
    ) -> None:
        """
        Update a player's display info (name, badge, etc.).

        Typically called from the Telegram handler when we receive
        the user's full profile data.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return

            player = self._find_player(session, user_id)
            if player is None:
                return

            if name:
                player.name = name
            if badge:
                player.badge = badge
            if balance:
                player.balance = balance
            if wins:
                player.wins = wins

            session.touch()
            self._db_save_session(session)


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class SessionError(Exception):
    """Raised for invalid session operations."""
