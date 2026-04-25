import os
import sys
from pathlib import Path

# ── Auto-load .env file if python-dotenv is available ──────────
try:
    from dotenv import load_dotenv
    # Load .env from the project root (same dir as this file)
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    # python-dotenv not installed — rely on shell environment variables
    pass

# === TOKEN VALIDATION ===
GAME_BOT_TOKEN = os.getenv("GAME_BOT_TOKEN", "")
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "")

if not GAME_BOT_TOKEN:
    print("FATAL: GAME_BOT_TOKEN environment variable is not set. Refusing to start.")
    sys.exit(1)
if not ADMIN_BOT_TOKEN:
    print("FATAL: ADMIN_BOT_TOKEN environment variable is not set. Refusing to start.")
    sys.exit(1)

# === PATHS ===
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "game_platform.db"
GAMES_DIR = BASE_DIR / "games"

# === DATABASE ===
DB_TIMEOUT = 30

# === DEPLOYMENT ===
# Health-check HTTP server port (Render requires PORT env var)
HEALTH_PORT = int(os.getenv("PORT", os.getenv("HEALTH_PORT", "10000")))
# Webhook mode (future use — currently polling only)
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # e.g. https://yourapp.onrender.com/webhook
USE_WEBHOOK = bool(WEBHOOK_URL)

# === CURRENCY ===
CURRENCY_NAME = "SAR"
CURRENCY_SYMBOL = "SAR"
WIN_REWARD = 2
SHARE_REWARD = 2
REFERRAL_BONUS = 1
PARTICIPATION_BONUS = 0.5

# === PROMOTIONS ===
PROMOTION_MIN_SAR = 20
PROMOTION_MAX_ACTIVE = 3

# === WITHDRAWALS ===
WITHDRAWAL_MIN_SAR = 200
WITHDRAWAL_METHODS = ["Western Union", "PayPal", "Crypto"]

# === ADMIN ===
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

# === CHANNELS ===
REQUIRED_CHANNELS_ENABLED = True

# === SESSION ===
SESSION_TIMEOUT_SECONDS = 3600  # 1 hour
STALE_ROOM_TIMEOUT_SECONDS = 7200  # 2 hours

# === REWARD COOLDOWN ===
REWARD_CLAIM_COOLDOWN = 60  # seconds between reward claims

# === BACKGROUND ===
BACKGROUND_INTERVAL_SECONDS = 60

# === LOG ROTATION ===
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))  # 10 MB
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))

# Ensure data directory exists
(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
