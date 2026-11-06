FROM oven/bun:1.4.4 AS frontend
WORKDIR /build
COPY caress/web/package.json caress/web/bun.lock ./
RUN bun install --frozen-lockfile
COPY caress/web ./
RUN bun run build

FROM frontend AS backend
COPY caress /backend/caress
RUN rm -rf /backend/caress/web

FROM python:3.15.2-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 1000 caress && mkdir -p /data /media && chown caress:caress /data
COPY --from=backend --chown=1000:1000 /backend/caress ./caress
COPY --from=frontend --chown=1000:1000 /build/dist ./caress/web/dist
RUN python -c "from caress.images import validate_resources; validate_resources()"
USER caress
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9307/api/health', timeout=5)"]
CMD ["python", "-m", "caress"]
