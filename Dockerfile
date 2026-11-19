# The Retimer API in a container: Python 3.13, ffmpeg, and the dependencies as
# they are locked in uv.lock.
#
#   docker compose up -d --build        see compose.yaml and the README
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never
# The dependencies first, so that a change to the code does not install them again.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --locked

# The service runs as an account of its own, and writes to /data only.
RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin retimer \
    && mkdir /data && chown retimer /data
USER retimer
ENV PATH=/app/.venv/bin:$PATH \
    RETIMER_DATA_DIR=/data
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
# Plain HTTP or HTTPS is decided by RETIMER_ALLOW_HTTP and RETIMER_TRUSTED_PROXIES.
CMD ["retimer", "serve", "--host", "0.0.0.0", "--port", "8000"]
