FROM oven/bun:1.4.3 AS bun
FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM python:3.14-slim AS app
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates fontconfig fonts-dejavu-core libatomic1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ARG APP_ORIGIN=http://localhost:8080
ENV APP_ORIGIN=${APP_ORIGIN}
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
COPY . .
RUN bun install --frozen-lockfile && uv sync --frozen --no-dev && bun run build
RUN useradd --create-home avicv && mkdir -p /app/backend/data && chown avicv:avicv /app/backend/data
USER avicv
ENV PORT=8080 DATA_DIR=/app/backend/data
EXPOSE 8080
VOLUME ["/app/backend/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD ["bun", "-e", "const r=await fetch('http://127.0.0.1:'+process.env.PORT+'/healthz');if(!r.ok)process.exit(1)"]
CMD ["bun", "run", "start"]
