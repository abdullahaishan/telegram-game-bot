# ──────────────────────────────────────────────────────────────
# Telegram Multiplayer Game Platform – Production Docker Image
# ──────────────────────────────────────────────────────────────
# Build:
#   docker build -t game-platform .
#
# Run:
#   docker run -d --name game-platform \
#     -e GAME_BOT_TOKEN=... \
#     -e ADMIN_BOT_TOKEN=... \
#     -e ADMIN_IDS=... \
#     -p 10000:10000 \
#     -v game-data:/app/data \
#     game-platform
# ──────────────────────────────────────────────────────────────

FROM python:3.11-slim

# System deps for SQLite and clean image
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory for SQLite
RUN mkdir -p /app/data

# Health check via the built-in HTTP server
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:10000/health || exit 1

# Expose health-check port
EXPOSE 10000

# Run the platform
CMD ["python", "run.py"]
