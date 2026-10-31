FROM python:3.12-slim AS tests
WORKDIR /app
# Dev-зависимости (pytest и пр.) пекутся в образ, а не ставятся pip'ом на
# каждый запуск: install внутри контейнера с 800m cgroup убивался OOM'ом
# на однорядном хосте, который параллельно обслуживает прод.
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt
COPY . .

FROM tests AS prod
# Прод-кульминация: compose без target собирает последний stage.
# tests-stage KEEP: docker compose -f docker-compose.test.yml --target tests.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD python -m utils.healthcheck
CMD ["python", "-m", "bot"]
