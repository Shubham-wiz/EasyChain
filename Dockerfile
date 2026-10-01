# One image with everything: the built web app served by the Python API server.
#   docker compose up        (or: docker build -t easychain . && docker run -p 8000:8000 easychain)

FROM node:22-slim AS web
WORKDIR /src
RUN corepack enable
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile --filter @easychain/web
COPY apps/web apps/web
RUN pnpm --filter @easychain/web build

FROM python:3.12-slim AS app
RUN pip install --no-cache-dir uv==0.8.17
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /app/python
COPY python/pyproject.toml python/uv.lock python/README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY python/src ./src
COPY --from=web /src/apps/web/dist ./src/easychain/server/static
RUN uv sync --frozen --no-dev

ENV EASYCHAIN_HOME=/data \
    EASYCHAIN_HOST=0.0.0.0 \
    EASYCHAIN_PORT=8000
VOLUME ["/data"]
EXPOSE 8000
RUN useradd --create-home --uid 1000 easychain && mkdir -p /data && chown easychain /data
USER easychain
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["easychain", "dev"]
