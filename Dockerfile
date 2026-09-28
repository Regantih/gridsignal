# One container runs everything: the FastAPI api/ serves the built React front end.
# No API keys are needed; prices come from the bundled ERCOT sample and the fleet is simulated.

FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
COPY pyproject.toml README.md ./
COPY src/ src/
COPY data/ data/
COPY scenarios/ scenarios/
COPY api/ api/
RUN pip install --no-cache-dir ".[api]"
COPY --from=web /web/dist web/dist
EXPOSE 8000
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
