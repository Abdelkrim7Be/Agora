FROM python:3.11-slim

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel

COPY platform-core/ /tmp/platform-core/
RUN pip install --no-cache-dir /tmp/platform-core

COPY email-agent/pyproject.toml .
RUN python -c 'import subprocess, sys, tomllib; deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *deps])'

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir -p /app/data /app/media \
    && chown -R app:app /app

COPY --chown=app:app email-agent/src/ ./src/
COPY --chown=app:app email-agent/config/ ./config/
COPY --chown=app:app email-agent/migrations/ ./migrations/
COPY --chown=app:app email-agent/alembic.ini .
COPY --chown=app:app email-agent/config.yaml .
COPY --chown=app:app email-agent/rules.yaml .
COPY --chown=app:app email-agent/categories.yaml .
COPY --chown=app:app email-agent/campaigns.yaml .
COPY --chown=app:app email-agent/entrypoint.sh .
RUN pip install --no-cache-dir --no-deps .
RUN chmod +x entrypoint.sh

USER app

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
