FROM python:3.11-slim

WORKDIR /app

# python:3.11-slim ships Debian packages that lag the security archive — the
# util-linux family (CVE-2026-53612..53615) is what fails the image scan. Refresh
# the OS layer before anything else is installed.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel

COPY pyproject.toml .
COPY policy.yaml .

RUN python -c 'import subprocess, sys, tomllib; deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *deps])'

# Floor two transitive packages the image scan flags; neither is pinned by a
# direct dependency. setuptools: CVE-2025-47273. msgpack: GHSA-6v7p-g79w-8964.
RUN pip install --no-cache-dir --upgrade "setuptools>=78.1.1" "msgpack>=1.2.1"

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && chown -R app:app /app

COPY --chown=app:app src/ src/
RUN pip install --no-cache-dir --no-deps .

# pip vendors its own copies of msgpack and setuptools (pip/_vendor/vendor.txt),
# which no pip install can upgrade — they are what keeps the image scan red
# (GHSA-6v7p-g79w-8964, CVE-2025-47273). Nothing in the runtime uses pip, so
# drop it once the last install is done.
RUN python -m pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.11/ensurepip

USER app

EXPOSE 8001

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8001"]
