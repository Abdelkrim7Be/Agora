FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY policy.yaml .
COPY src/ src/

RUN pip install --no-cache-dir .

EXPOSE 8001

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8001"]
