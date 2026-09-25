# RMA Platform V2 production images. One repository, one version, three targets:
#
#   runtime  -> rma-portal:<version>       API, worker and browser desktop (ENTRYPOINT rma-portal)
#   web      -> rma-portal-web:<version>   nginx: React static files + /api reverse proxy
#
# Build context is the repository root (see compose.prod.yaml). Base images are pinned by
# digest; the Camoufox browser build is pinned exactly like the validated Phase 0 image.

# --- React frontend --------------------------------------------------------------------------
# node:24-alpine (multi-arch index digest resolved 2026-09-25)
FROM node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
# `npm run build` = tsc -b && vite build; the API types (src/api/schema.d.ts) are committed.
RUN npm run build

# --- static web / reverse proxy -------------------------------------------------------------------
# nginx:1.28-alpine (multi-arch index digest resolved 2026-09-25)
FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236 AS web
COPY docker/prod/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/prod/rma-proxy.conf /etc/nginx/rma-proxy.conf
COPY --from=frontend-build /frontend/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1

# --- Python runtime: API, worker and the noVNC browser desktop --------------------------------
# Pinned by digest (ubuntu:26.04 multi-arch index, resolved 2026-09-24) -- same base as the
# validated docker/poc-browser image.
FROM ubuntu:26.04@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78 AS runtime

ARG UV_VERSION=0.11
# Pinned Camoufox browser build (`camoufox fetch <spec>`), identical to the Phase 0 image.
ARG CAMOUFOX_BROWSER_SPEC=official/152.0.4-beta.31
ARG RMA_UID=10001

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
    HOME=/home/rma \
    XDG_CACHE_HOME=/home/rma/.cache \
    DISPLAY=:99

# Same package set as the validated Phase 0 image (Ubuntu 24.04+/26.04 t64 names).
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

# The browser desktop and the worker share these two volumes (profile, captured session);
# /var/lib/rma-portal holds application data, /var/log/rma-portal the logs.
RUN useradd --uid ${RMA_UID} --create-home --home-dir /home/rma --shell /usr/sbin/nologin rma \
    && install -d -o rma -g rma \
        /home/rma /home/rma/.cache /home/rma/.cache/camoufox \
        /var/lib/rma-poc /var/lib/rma-poc/profile /var/lib/rma-poc/state \
        /var/lib/rma-portal /var/log/rma-portal /tmp/rma-poc \
    && install -d -m 1777 /tmp/.X11-unix

# Locked dependency install (the repository's uv.lock), Python 3.14. Alembic ships with the
# code so `rma-portal migrate` and `import-sqlite` work from the same image.
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
RUN uv python install 3.14 \
    && uv sync --frozen --no-dev \
    && chmod -R a+rX /opt/python /opt/venv /app

# The expensive browser download stays ahead of the frequently changed scripts.
USER rma
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
    camoufox version

# Validated noVNC desktop scripts (unchanged) used by the `browser` service.
USER root
COPY docker/poc-browser/rma_poc /opt/rma-poc/rma_poc
COPY docker/poc-browser/supervisord.conf /etc/rma-poc/supervisord.conf
COPY docker/poc-browser/entrypoint.sh docker/poc-browser/healthcheck.sh docker/poc-browser/wait-for-x.sh /opt/rma-poc/bin/
RUN chmod 0755 /opt/rma-poc/bin/*.sh

USER rma
WORKDIR /app
ENTRYPOINT ["rma-portal"]
CMD ["--help"]
