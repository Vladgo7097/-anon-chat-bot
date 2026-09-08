FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD python -m utils.healthcheck
CMD ["python", "-m", "bot"]
