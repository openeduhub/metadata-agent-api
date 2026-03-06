# =============================================================================
# Metadata Agent API — Docker Image
# =============================================================================
# Includes Playwright + Chromium for privacy-safe screenshot capture.
# On Vercel, Playwright is not available — only the 'pageshot' method works.
#
# Build:  docker build -t metadata-agent-api .
# Run:    docker run -d -p 8000:8000 -e B_API_KEY=... metadata-agent-api
# =============================================================================

# Build stage — install Python dependencies
FROM python:3.12-slim AS builder

WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.12-slim

WORKDIR /app

# Install Playwright system dependencies (Chromium)
# These are the minimal packages needed for headless Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libx11-xcb1 \
    fonts-liberation \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Install Playwright Chromium browser into shared location
# (must be accessible by non-root appuser at runtime)
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN mkdir -p $PLAYWRIGHT_BROWSERS_PATH \
    && playwright install chromium

# Copy application code (includes src/static/widget/ if present)
COPY src/ ./src/

# Create non-root user and grant access to Playwright browsers
RUN useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app \
    && chmod -R o+rx $PLAYWRIGHT_BROWSERS_PATH
USER appuser

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run with uvicorn
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
