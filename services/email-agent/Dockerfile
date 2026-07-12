FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ ./src/
COPY config/ ./config/
COPY migrations/ ./migrations/
COPY alembic.ini .
COPY config.yaml .
COPY rules.yaml .
COPY categories.yaml .
COPY campaigns.yaml .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
