# syntax=docker/dockerfile:1

# --- stage 1: build the frontend (+ the @cadcode/viewer renderer package) ---
FROM node:22-slim AS frontend
WORKDIR /build
# The renderer is a sibling package consumed via a Vite alias (../viewer/src);
# install its deps and place it next to frontend/ so the alias resolves.
COPY viewer/package.json viewer/package-lock.json ./viewer/
RUN cd viewer && npm ci
COPY viewer/ ./viewer/
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci
COPY frontend/ ./frontend/
RUN cd frontend && npm run build          # -> /build/frontend/dist

# --- stage 2: backend + served SPA ---
FROM python:3.12-slim AS app

# uv for fast, locked installs
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

# OpenCASCADE (build123d / cadquery-ocp) runtime libs + git (for checkpoints)
# + prusa-slicer (headless CLI, fallback slicer for exact print times)
# + libcairo2 (cairosvg rasterizes the agent's vision-check thumbnails)
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglu1-mesa libxext6 libx11-6 libxrender1 libxcb1 libgomp1 \
      git ca-certificates prusa-slicer libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# OrcaSlicer (preferred slicer — matches the daily driver). No apt package: pull
# the Linux AppImage and extract it (no FUSE in Docker). It's a GTK/wxWidgets app
# that inits a display even in CLI mode, so we also install Xvfb + software GL
# (mesa/llvmpipe) and the GTK/WebKit/GStreamer runtime libs (trixie t64 names).
# The AppImage is Ubuntu-24.04-built (glibc 2.39); our base is trixie (2.41) ✓.
ARG ORCA_VERSION=2.4.1
RUN apt-get update && apt-get install -y --no-install-recommends \
      wget xvfb xauth \
      libgtk-3-0t64 libwebkit2gtk-4.1-0 libglib2.0-0t64 \
      libgl1-mesa-dri libegl1 libosmesa6 \
      libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
      libnotify4 libsecret-1-0 libsoup-3.0-0 \
      fonts-dejavu-core \
    && wget -q -O /tmp/orca.AppImage \
        "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v${ORCA_VERSION}/OrcaSlicer_Linux_AppImage_Ubuntu2404_V${ORCA_VERSION}.AppImage" \
    && chmod +x /tmp/orca.AppImage \
    && cd /opt && /tmp/orca.AppImage --appimage-extract \
    && mv squashfs-root orcaslicer \
    && ln -s /opt/orcaslicer/AppRun /usr/local/bin/orca-slicer \
    && rm /tmp/orca.AppImage \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
ENV UV_HTTP_TIMEOUT=600 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install deps first (cached layer), then the app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --extra agent --no-install-project --no-dev
COPY backend/ ./
RUN uv sync --extra agent --no-dev

# Built SPA, served same-origin by FastAPI
COPY --from=frontend /build/frontend/dist ./static

ENV CAD_STATIC_DIR=/app/backend/static \
    CAD_WORKSPACE=/data/workspace \
    CAD_PROJECTS=/data/projects \
    PORT=8000 \
    PYTHONUTF8=1
EXPOSE 8000

CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
