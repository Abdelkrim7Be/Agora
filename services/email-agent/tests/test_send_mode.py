from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import src.send_mode as sm
from src.api import app
from src.capabilities import email_tools
from src.config import settings
from src.send_mode import SIMULATED_NOTE, effective_dry_run, get_send_mode, set_send_mode


@pytest.fixture
def file_backend(monkeypatch, tmp_path):
    """Route instance-config reads/writes to a temp dir (no postgres, no repo files).

    Also undoes the conftest autouse compat patch (which pins get_send_mode to
    'live' for legacy dry_run tests) so the real lookup is exercised here — the
    module-level import in this file kept a reference to the real function.
    """
    import src.instance_config as ic

    monkeypatch.setattr(ic.settings, "database_url", "")
    monkeypatch.setattr(sm, "_DEFAULT_PATH", tmp_path / "send_mode.yaml")
    monkeypatch.setattr(sm, "get_send_mode", get_send_mode)
    return tmp_path


def test_default_send_mode_is_simulation(file_backend):
    assert sm.get_send_mode() == "simulation"


def test_set_send_mode_round_trip(file_backend):
    assert set_send_mode("live") == "live"
    assert sm.get_send_mode() == "live"
    assert set_send_mode("simulation") == "simulation"
    assert sm.get_send_mode() == "simulation"


def test_set_send_mode_rejects_unknown_value(file_backend):
    with pytest.raises(ValueError):
        set_send_mode("production")


def test_global_dry_run_lock_wins_over_live_mode(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", True)
    monkeypatch.setattr(sm, "get_send_mode", lambda agent_instance_id=None: "live")
    assert effective_dry_run() is True


def test_live_mode_without_lock_sends_for_real(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(sm, "get_send_mode", lambda agent_instance_id=None: "live")
    assert effective_dry_run() is False


def test_simulation_mode_forces_dry_run_even_without_lock(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(sm, "get_send_mode", lambda agent_instance_id=None: "simulation")
    assert effective_dry_run() is True


def test_write_email_simulated_message_is_french(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", True)
    result = email_tools.write_email.invoke(
        {"to": "alice@example.com", "subject": "Re", "content": "Bonjour"}
    )
    assert SIMULATED_NOTE in result
    assert "dry run" not in result


def test_send_mode_endpoints_round_trip(monkeypatch, tmp_path):
    import src.instance_config as ic

    monkeypatch.setattr(ic.settings, "database_url", "")
    monkeypatch.setattr(sm, "_DEFAULT_PATH", tmp_path / "send_mode.yaml")

    with TestClient(app) as client:
        current = client.get("/send-mode").json()
        assert current["send_mode"] == "simulation"

        updated = client.put("/send-mode", json={"send_mode": "live"}).json()
        assert updated["send_mode"] == "live"
        assert client.get("/send-mode").json()["send_mode"] == "live"

        invalid = client.put("/send-mode", json={"send_mode": "bogus"})
        assert invalid.status_code == 400


def test_send_mode_put_requires_owner_role(monkeypatch, tmp_path):
    import src.instance_config as ic

    monkeypatch.setattr(ic.settings, "database_url", "")
    monkeypatch.setattr(sm, "_DEFAULT_PATH", tmp_path / "send_mode.yaml")

    with TestClient(app) as client:
        denied = client.put(
            "/send-mode",
            json={"send_mode": "live"},
            headers={"X-Agora-Instance-Role": "viewer"},
        )
        assert denied.status_code == 403
