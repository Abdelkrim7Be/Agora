FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN python -c 'import subprocess, sys, tomllib; deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *deps])'

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir -p /app/data /app/media \
    && chown -R app:app /app

COPY --chown=app:app src/ ./src/
COPY --chown=app:app config/ ./config/
COPY --chown=app:app migrations/ ./migrations/
COPY --chown=app:app alembic.ini .
COPY --chown=app:app config.yaml .
COPY --chown=app:app rules.yaml .
COPY --chown=app:app categories.yaml .
COPY --chown=app:app campaigns.yaml .
COPY --chown=app:app entrypoint.sh .
RUN pip install --no-cache-dir --no-deps .
RUN chmod +x entrypoint.sh

USER app

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
