# Multi-stage Dockerfile for mailfiler
# Stage 1: Build
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml README.md ./
COPY mailfiler/ mailfiler/

RUN uv sync --no-dev --frozen

# Stage 2: Runtime
FROM python:3.12-slim

# Run as non-root user
RUN useradd --create-home --shell /bin/bash mailfiler
USER mailfiler
WORKDIR /home/mailfiler

COPY --from=builder /app /home/mailfiler/app
COPY config.toml.example /home/mailfiler/app/config.toml

WORKDIR /home/mailfiler/app

# Create data directory
RUN mkdir -p /home/mailfiler/.mailfiler

ENTRYPOINT ["uv", "run", "mailfiler"]
CMD ["run"]
