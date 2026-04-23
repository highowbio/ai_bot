FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install runtime dependencies first so the layer is cached when only code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Run as a non-root user for safety.
RUN useradd --create-home --shell /bin/bash bot
USER bot

COPY --chown=bot:bot . .

# Persist whitelist and PTB persistence files in a named volume.
VOLUME ["/app/data"]
ENV WHITELIST_FILE=/app/data/whitelist.json \
    PERSISTENCE_FILE=/app/data/bot_persistence.pickle

CMD ["python", "-u", "main.py"]
