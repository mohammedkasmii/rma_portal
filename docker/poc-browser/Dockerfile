# Phase 0 PoC: Camoufox + Xvfb + openbox + x11vnc + noVNC.
# Build context is the repository root (see compose.poc.yaml).

# Pinned by digest (ubuntu:26.04 multi-arch index, resolved 2026-09-24).
FROM ubuntu:26.04@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78

ARG UV_VERSION=0.11
# Pinned Camoufox browser build (`camoufox fetch <spec>`); override with
# --build-arg only to test another build.
ARG CAMOUFOX_BROWSER_SPEC=official/152.0.4-beta.31
ARG POC_UID=10001

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=manual \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/opt/rma-poc \
    HOME=/home/pocuser \
    XDG_CACHE_HOME=/home/pocuser/.cache \
    DISPLAY=:99

# Package names are the Ubuntu 24.04+/26.04 (t64) names.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl tzdata \
        xvfb x11vnc novnc websockify openbox supervisor procps \
        fonts-liberation fonts-dejavu-core fonts-noto-core \
        libgtk-3-0t64 libasound2t64 libdbus-glib-1-2 libx11-xcb1 libxt6t64 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxcursor1 libxi6 \
        libpci3 libpulse0 libnss3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker.io/astral/uv:0.11 /uv /usr/local/bin/uv

RUN useradd --uid ${POC_UID} --create-home --home-dir /home/pocuser --shell /usr/sbin/nologin pocuser \
    && install -d -o pocuser -g pocuser \
        /home/pocuser /home/pocuser/.cache /home/pocuser/.cache/camoufox \
        /var/lib/rma-poc /var/lib/rma-poc/profile /var/lib/rma-poc/state /tmp/rma-poc \
    && install -d -m 1777 /tmp/.X11-unix

# Locked dependency install (same uv.lock as the application), Python 3.14.
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv python install 3.14 \
    && uv sync --frozen --no-dev \
    && chmod -R a+rX /opt/python /opt/venv /app

COPY docker/poc-browser/rma_poc /opt/rma-poc/rma_poc
COPY docker/poc-browser/supervisord.conf /etc/rma-poc/supervisord.conf
COPY docker/poc-browser/entrypoint.sh docker/poc-browser/healthcheck.sh docker/poc-browser/wait-for-x.sh /opt/rma-poc/bin/
RUN chmod 0755 /opt/rma-poc/bin/*.sh

# The Camoufox browser build lives in the image, in the non-root user's cache.
USER pocuser
RUN set -eu; \
    attempt=1; \
    while [ "$attempt" -le 5 ]; do \
        echo "Camoufox fetch attempt ${attempt}/5"; \
        if camoufox fetch ${CAMOUFOX_BROWSER_SPEC} \
            && python -c "from camoufox.pkgman import launch_path; print('camoufox executable', launch_path())"; then \
            break; \
        fi; \
        if [ "$attempt" -eq 5 ]; then \
            echo "Camoufox browser was not installed after 5 attempts" >&2; \
            exit 1; \
        fi; \
        attempt=$((attempt + 1)); \
        sleep 5; \
    done; \
    camoufox version; \
    python -c "import camoufox, playwright; print('camoufox pkg', camoufox.__file__)"

WORKDIR /home/pocuser
EXPOSE 6080
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["/opt/rma-poc/bin/healthcheck.sh"]
ENTRYPOINT ["/opt/rma-poc/bin/entrypoint.sh"]
