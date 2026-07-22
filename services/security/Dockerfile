FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY policy.yaml .

RUN python -c 'import subprocess, sys, tomllib; deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *deps])'

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && chown -R app:app /app

COPY --chown=app:app src/ src/
RUN pip install --no-cache-dir --no-deps .

USER app

EXPOSE 8001

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8001"]
