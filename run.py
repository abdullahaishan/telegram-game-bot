"""
Telegram Multiplayer Game Platform - Main Runner

Starts both the Game Bot and Admin Bot concurrently.
Uses python-telegram-bot v20+ async API.

Production-ready features:
  - SIGTERM / SIGINT graceful shutdown (Docker / Render compatible)
  - Health-check HTTP server for deployment probes
  - Rotating log files (10 MB × 5 backups)
  - Proper DB connection cleanup on shutdown
  - python-dotenv auto-loading
  - Auto-restart on fatal errors (supervisor mode)
"""

import asyncio
import logging
import logging.handlers
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

# ──────────────────────────────────────────────
# CONFIG VALIDATION
# ──────────────────────────────────────────────

# This import triggers the token validation in config.py
# If tokens are missing, the process will exit immediately
try:
    import config
except SystemExit:
    print("\nStartup aborted: Required configuration is missing.")
    print("Please set GAME_BOT_TOKEN and ADMIN_BOT_TOKEN environment variables.")
    sys.exit(1)

# ──────────────────────────────────────────────
# LOGGING SETUP (with rotation)
# ──────────────────────────────────────────────

_log_dir = Path(config.BASE_DIR) / "data"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file = _log_dir / "platform.log"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            _log_file,
            maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# DATABASE INITIALIZATION
# ──────────────────────────────────────────────

from database import init_db, close_db

logger.info("Initializing database...")
init_db()
logger.info("Database initialized successfully")

# ──────────────────────────────────────────────
# HEALTH CHECK SERVER
# ──────────────────────────────────────────────

from health_server import start_health_server, set_state

# Start the HTTP health-check server in a daemon thread
_health_server = start_health_server(config.HEALTH_PORT)

# ──────────────────────────────────────────────
# BOT IMPORTS
# ──────────────────────────────────────────────

from game_bot.bot import create_game_bot
from admin_bot.bot import create_admin_bot
from background import BackgroundTaskManager

# ──────────────────────────────────────────────
# GLOBAL STATE
# ──────────────────────────────────────────────

_shutdown_event = asyncio.Event()
_game_app = None
_admin_app = None
_bg_manager = None


# ──────────────────────────────────────────────
# SIGNAL HANDLERS (SIGTERM / SIGINT)
# ──────────────────────────────────────────────

def _signal_handler(signum, frame):
    """Handle termination signals gracefully (SIGTERM, SIGINT)."""
    sig_name = signal.Signals(signum).name
    logger.info("Received %s — initiating graceful shutdown...", sig_name)
    _shutdown_event.set()


# Register signal handlers for containerized environments
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)

# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────


async def main():
    """Start both bots and background tasks."""
    global _game_app, _admin_app, _bg_manager

    logger.info("=" * 60)
    logger.info("Telegram Multiplayer Game Platform Starting")
    logger.info("=" * 60)

    # Update health state
    set_state(
        started_at=datetime.now(timezone.utc),
        game_bot_ok=False,
        admin_bot_ok=False,
        db_ok=True,
        bg_ok=False,
    )

    # Create bot applications
    _game_app = create_game_bot()
    _admin_app = create_admin_bot()

    # Initialize applications
    await _game_app.initialize()
    await _admin_app.initialize()

    # Set up SessionManager with actual bot instance
    from game_bot.engine.session import SessionManager
    plugin_loader = _game_app.bot_data["plugin_loader"]
    renderer = _game_app.bot_data["renderer"]
    session_manager = SessionManager(
        bot=_game_app.bot,
        plugin_loader=plugin_loader,
        renderer=renderer,
    )
    _game_app.bot_data["session_manager"] = session_manager
    logger.info("SessionManager initialized with bot instance")

    # Start background tasks
    _bg_manager = BackgroundTaskManager()
    await _bg_manager.set_bots(_game_app.bot, _admin_app.bot)
    await _bg_manager.start()
    logger.info("Background tasks started")

    # Update health: background tasks running
    set_state(bg_ok=True)

    # Start both bots
    logger.info("Starting Game Bot...")
    logger.info("Starting Admin Bot...")

    await _game_app.start()
    await _admin_app.start()

    # Start polling for both bots
    await asyncio.gather(
        _game_app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"]
        ),
        _admin_app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"]
        ),
    )

    # Update health: both bots running
    set_state(game_bot_ok=True, admin_bot_ok=True)
    logger.info("Both bots are running! Platform is ready.")

    # Wait for shutdown signal
    try:
        await _shutdown_event.wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received...")
    finally:
        await _shutdown()


async def _shutdown():
    """Perform graceful shutdown of all components."""
    global _game_app, _admin_app, _bg_manager

    logger.info("Shutting down platform...")

    # Update health: going down
    set_state(game_bot_ok=False, admin_bot_ok=False, bg_ok=False)

    # 1. Stop background tasks
    if _bg_manager:
        try:
            await _bg_manager.stop()
            logger.info("Background tasks stopped")
        except Exception as e:
            logger.error("Error stopping background tasks: %s", e)

    # 2. Stop polling (updater)
    if _game_app and _game_app.updater.running:
        try:
            await _game_app.updater.stop()
        except Exception as e:
            logger.error("Error stopping game bot updater: %s", e)
    if _admin_app and _admin_app.updater.running:
        try:
            await _admin_app.updater.stop()
        except Exception as e:
            logger.error("Error stopping admin bot updater: %s", e)

    # 3. Stop applications
    if _game_app:
        try:
            await _game_app.stop()
        except Exception as e:
            logger.error("Error stopping game bot: %s", e)
    if _admin_app:
        try:
            await _admin_app.stop()
        except Exception as e:
            logger.error("Error stopping admin bot: %s", e)

    # 4. Shutdown applications
    if _game_app:
        try:
            await _game_app.shutdown()
        except Exception as e:
            logger.error("Error shutting down game bot: %s", e)
    if _admin_app:
        try:
            await _admin_app.shutdown()
        except Exception as e:
            logger.error("Error shutting down admin bot: %s", e)

    # 5. Close database connections
    try:
        close_db()
        logger.info("Database connections closed")
    except Exception as e:
        logger.error("Error closing database: %s", e)

    # 6. Stop health check server
    try:
        _health_server.shutdown()
        logger.info("Health server stopped")
    except Exception as e:
        logger.error("Error stopping health server: %s", e)

    logger.info("Platform shutdown complete")


def run_with_restart(max_restarts: int = 5, cooldown: int = 10):
    """
    Run the platform with auto-restart on fatal errors.

    This acts as a lightweight process supervisor. If the main()
    coroutine crashes with an unhandled exception, it will be
    restarted up to *max_restarts* times with a *cooldown* pause
    between attempts.

    For production, prefer an external supervisor (systemd, Docker
    restart policy, Render's built-in restart) over this.
    """
    restart_count = 0

    while restart_count < max_restarts:
        try:
            asyncio.run(main())
            # Clean exit (SIGTERM/SIGINT) — don't restart
            logger.info("Clean exit — not restarting.")
            break
        except KeyboardInterrupt:
            logger.info("Platform stopped by user")
            break
        except SystemExit:
            logger.info("Platform exited with SystemExit — not restarting.")
            break
        except Exception as e:
            restart_count += 1
            logger.critical(
                "Fatal error (attempt %d/%d): %s",
                restart_count, max_restarts, e,
                exc_info=True,
            )
            set_state(last_error=str(e))

            if restart_count < max_restarts:
                logger.info("Restarting in %d seconds...", cooldown)
                import time
                time.sleep(cooldown)
                # Increase cooldown for subsequent restarts
                cooldown = min(cooldown * 2, 120)
            else:
                logger.critical("Max restart attempts reached. Exiting.")
                sys.exit(1)


if __name__ == "__main__":
    # Use supervisor mode for auto-restart on fatal errors
    supervisor = os.getenv("AUTO_RESTART", "true").lower() == "true"

    if supervisor:
        run_with_restart()
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info("Platform stopped by user")
        except Exception as e:
            logger.critical("Fatal error: %s", e, exc_info=True)
            sys.exit(1)
