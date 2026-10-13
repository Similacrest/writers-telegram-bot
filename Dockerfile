FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first for layer caching.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project --extra discord

COPY src ./src
COPY scripts ./scripts
RUN uv sync --locked --no-dev --extra discord

RUN useradd --system --uid 10001 --home-dir /app bot && mkdir -p /app/data && chown bot /app/data
USER bot

# Persistent state (SQLite, Google token) lives in /app/data: mount a volume there.
ENV PATH="/app/.venv/bin:$PATH" \
    DB_PATH=/app/data/bot.sqlite3 \
    GOOGLE_TOKEN_FILE=/app/data/token.json \
    HEALTH_PORT=8080
VOLUME ["/app/data"]

# /health answers 200 while the bot is receiving updates and the database works (127.0.0.1 only by
# default; set HEALTH_HOST=0.0.0.0 and publish the port for outside monitoring).
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"HEALTH_PORT\"]}/health', timeout=5)"]

EXPOSE 80
# Telegram by default; run the Discord bot from the same image with `writers-bot-discord`.
CMD ["writers-bot-telegram"]
