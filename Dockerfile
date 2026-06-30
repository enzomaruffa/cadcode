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
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglu1-mesa libxext6 libx11-6 libxrender1 libxcb1 libgomp1 \
      git ca-certificates \
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
    PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
