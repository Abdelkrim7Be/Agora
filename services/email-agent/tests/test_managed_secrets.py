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
    bundle.write_text(json.dumps({"OPENAI_API_KEY": "bundle-key"}), encoding="utf-8")
    monkeypatch.setenv("AGENT_SECRET_MANAGER_BACKEND", "bundle")
    monkeypatch.setenv("AGENT_SECRET_BUNDLE_PATH", str(bundle))

    assert managed_secrets.get_secret("AGENT", "OPENAI_API_KEY") == "bundle-key"


def test_required_managed_secret_fails_fast(monkeypatch):
    monkeypatch.setenv("AGENT_SECRET_MANAGER_BACKEND", "bundle")
    monkeypatch.setenv("AGENT_SECRET_MANAGER_REQUIRED", "true")
    monkeypatch.setenv("AGENT_SECRET_BUNDLE_PATH", "missing.json")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="Managed secret 'OPENAI_API_KEY' is required"):
        managed_secrets.get_secret("AGENT", "OPENAI_API_KEY")


def test_vault_secret_resolution_uses_vault_payload(monkeypatch):
    monkeypatch.setenv("AGENT_SECRET_MANAGER_BACKEND", "vault")
    monkeypatch.setenv("AGENT_SECRET_MANAGER_URL", "https://vault.example.com")
    monkeypatch.setenv("AGENT_SECRET_MANAGER_PATH", "secret/data/agora/email-agent")
    monkeypatch.setenv("AGENT_SECRET_MANAGER_TOKEN", "vault-token")
    monkeypatch.setattr(managed_secrets, "_fetch_vault", lambda prefix: {"OPENAI_API_KEY": "vault-key"})

    assert managed_secrets.get_secret("AGENT", "OPENAI_API_KEY") == "vault-key"

def test_required_managed_secret_rejects_legacy_env_fallback(monkeypatch):
    monkeypatch.setenv("AGENT_SECRET_MANAGER_BACKEND", "bundle")
    monkeypatch.setenv("AGENT_SECRET_MANAGER_REQUIRED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "plaintext-env")

    with pytest.raises(RuntimeError, match="Managed secret 'OPENAI_API_KEY' is required"):
        managed_secrets.get_secret("AGENT", "OPENAI_API_KEY")



def test_vault_request_writes_kv_v2_payload(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["token"] = req.headers["X-vault-token"]
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return Response()

    monkeypatch.setenv("AGENT_SECRET_MANAGER_URL", "https://vault.example.com")
    monkeypatch.setenv("AGENT_SECRET_MANAGER_TOKEN", "vault-token")
    monkeypatch.setattr(managed_secrets.request, "urlopen", fake_urlopen)

    result = managed_secrets.vault_request(
        "AGENT",
        "secret/data/agora/gmail-tokens/default",
        method="POST",
        payload={"data": {"token_envelope": "encrypted"}},
    )

    assert result == {}
    assert captured == {
        "url": "https://vault.example.com/v1/secret/data/agora/gmail-tokens/default",
        "method": "POST",
        "token": "vault-token",
        "payload": {"data": {"token_envelope": "encrypted"}},
    }


def test_vault_request_rejects_plain_http(monkeypatch):
    monkeypatch.setenv("AGENT_SECRET_MANAGER_URL", "http://vault.example.com")
    monkeypatch.setenv("AGENT_SECRET_MANAGER_TOKEN", "vault-token")

    with pytest.raises(RuntimeError, match="must use HTTPS"):
        managed_secrets.vault_request("AGENT", "secret/data/agora/tokens/default")


def test_vault_capabilities_parses_policy_response(monkeypatch):
    monkeypatch.setattr(
        managed_secrets,
        "vault_request",
        lambda *_args, **_kwargs: {
            "capabilities": ["create", "read", "update", "delete"]
        },
    )

    assert managed_secrets.vault_capabilities("AGENT", "secret/data/token") == {
        "create",
        "read",
        "update",
        "delete",
    }


def test_managed_vault_backend_rejects_plain_http(monkeypatch):
    monkeypatch.setenv("AGENT_SECRET_MANAGER_BACKEND", "vault")
    monkeypatch.setenv("AGENT_SECRET_MANAGER_URL", "http://vault.example.com")
    monkeypatch.setenv("AGENT_SECRET_MANAGER_PATH", "secret/data/agora/email-agent")
    monkeypatch.setenv("AGENT_SECRET_MANAGER_TOKEN", "vault-token")

    with pytest.raises(RuntimeError, match="must use HTTPS"):
        managed_secrets.get_secret("AGENT", "OPENAI_API_KEY")
