# radon-services — Hetzner-resident scheduler runtime.
#
# Hosts the schedulers that today live as macOS launchd plists on the
# operator's laptop. Single image with Python 3.13 + Node 22 + Playwright
# Chromium so it can run the MenthorQ Playwright scrape, all CRI/GEX/VCG
# scans, journal/portfolio sync, and the data-refresh cycle. Scheduling
# is handled by systemd timers inside the container (started by the
# tini-managed entrypoint that calls `systemctl --user start radon.target`
# via systemd-in-docker).
#
# The image is intentionally fat — it's a multi-purpose runtime, not a
# single-binary microservice. We trade image size for operational
# simplicity (one container instead of nine).

FROM python:3.13-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NODE_VERSION=22.13.1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# Base packages: systemd-as-PID-1, build tools for libsql, Chromium deps,
# rsync (used by the newsfeed image push), curl/ca-certs for HTTPS.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg \
        build-essential libssl-dev libffi-dev pkg-config \
        rsync openssh-client \
        tini \
        # Chromium runtime deps (Playwright bakes the browser binary itself)
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
        libpango-1.0-0 libcairo2 libasound2 \
        # systemd userspace (we run timers under systemd inside the container)
        systemd \
    && rm -rf /var/lib/apt/lists/*

# Node 22 LTS — Playwright vendors Chromium for Node, and our newsfeed
# scraper itself is Node, so we need a real Node runtime.
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
        | tar -xJ -C /usr/local --strip-components=1 \
    && node --version

WORKDIR /app

# Copy manifests first for cache friendliness — application code lands
# below and edits don't bust the dep layers.
COPY requirements.txt ./
COPY package.json ./
RUN python3.13 -m pip install --upgrade pip \
    && python3.13 -m pip install -r requirements.txt \
    && python3.13 -m playwright install chromium \
    && npm install --omit=optional --no-fund --no-audit \
    && npm cache clean --force

# Application code — schedulers, scrapers, FastAPI server, DB writers.
COPY scripts ./scripts
COPY web ./web
COPY data ./data
COPY docker/services/services /etc/systemd/system/
COPY docker/services/timers   /etc/systemd/system/
COPY docker/services/entrypoint.sh /usr/local/bin/radon-services-entrypoint
RUN chmod +x /usr/local/bin/radon-services-entrypoint \
    && systemctl enable \
        radon-cri-scan.timer \
        radon-data-refresh.timer \
        radon-monitor.timer \
        radon-exit-orders.timer \
        radon-cta-sync.timer \
        radon-portfolio-sync.timer \
        radon-analyst-ratings.timer

VOLUME ["/app/data"]

# Tini reaps zombies; entrypoint runs `systemd` so the timers fire.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/radon-services-entrypoint"]
