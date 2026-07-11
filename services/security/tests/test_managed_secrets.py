from __future__ import annotations

import json

import pytest

from src import managed_secrets


@pytest.fixture(autouse=True)
def _clear_cache():
    managed_secrets.clear_cache()
    yield
    managed_secrets.clear_cache()


def test_bundle_secret_resolution(monkeypatch, tmp_path):
    bundle = tmp_path / "secrets.json"
    bundle.write_text(json.dumps({"GROQ_API_KEY": "bundle-key"}), encoding="utf-8")
    monkeypatch.setenv("SECURITY_SECRET_MANAGER_BACKEND", "bundle")
    monkeypatch.setenv("SECURITY_SECRET_BUNDLE_PATH", str(bundle))

    assert managed_secrets.get_secret("SECURITY", "GROQ_API_KEY") == "bundle-key"

def test_required_managed_secret_rejects_legacy_env_fallback(monkeypatch):
    monkeypatch.setenv("SECURITY_SECRET_MANAGER_BACKEND", "bundle")
    monkeypatch.setenv("SECURITY_SECRET_MANAGER_REQUIRED", "true")
    monkeypatch.setenv("GROQ_API_KEY", "plaintext-env")

    with pytest.raises(RuntimeError, match="Managed secret 'GROQ_API_KEY' is required"):
        managed_secrets.get_secret("SECURITY", "GROQ_API_KEY")

