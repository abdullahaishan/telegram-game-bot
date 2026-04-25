"""
Lightweight HTTP health-check server for deployment platforms.

Runs on a separate thread so it doesn't interfere with the async bot loop.
Provides endpoints for:
  - /health        → liveness probe (is the process alive?)
  - /ready         → readiness probe (are both bots connected?)
  - /status        → JSON detail (uptime, bot status, DB size, active sessions)
"""

import json
import os
import threading
import time
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Global state updated by run.py ──────────────────────────────────
_state = {
    "started_at": None,
    "game_bot_ok": False,
    "admin_bot_ok": False,
    "db_ok": False,
    "bg_ok": False,
    "last_error": None,
}


def set_state(**kwargs):
    """Update the shared health state (called from run.py)."""
    _state.update(kwargs)


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for health/readiness probes."""

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, "ok")
        elif self.path == "/ready":
            if _state["game_bot_ok"] and _state["admin_bot_ok"] and _state["db_ok"]:
                self._respond(200, "ready")
            else:
                self._respond(503, "not ready")
        elif self.path == "/status":
            payload = {
                "service": "telegram-game-platform",
                "uptime_seconds": (
                    (datetime.now(timezone.utc) - _state["started_at"]).total_seconds()
                    if _state.get("started_at")
                    else 0
                ),
                "game_bot": _state["game_bot_ok"],
                "admin_bot": _state["admin_bot_ok"],
                "database": _state["db_ok"],
                "background_tasks": _state["bg_ok"],
                "last_error": _state.get("last_error"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            body = json.dumps(payload, indent=2)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self._respond(404, "not found")

    # ── helpers ──────────────────────────────────────────────
    def _respond(self, code: int, message: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(message.encode())

    def log_message(self, fmt, *args):
        # Suppress default access logs; use our logger at DEBUG instead
        logger.debug(fmt, *args)


def start_health_server(port: int = None):
    """
    Start the health-check HTTP server on *port* (default: 10000 or PORT env var).
    Runs in a daemon thread so it exits automatically with the main process.
    """
    if port is None:
        port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    t = threading.Thread(target=server.serve_forever, name="health-server", daemon=True)
    t.start()
    logger.info("Health-check server listening on 0.0.0.0:%d", port)
    return server
