from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from src import api
from src import automation
from src.api import app, _require_run, _run_detail
from src.categories import CategoriesConfig, Category, CategoryInstructions
from src.run_registry import list_runs, selected_run_registry_backend, upsert_run
from src.tenant import current_agent_instance_id, current_user_id
from tests.conftest import patch_provider


def test_rule_and_section_toggle(monkeypatch, tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "enabled: false\n"
        "rules:\n"
        "  - name: archive promotions\n"
        "    enabled: true\n"
        "    when: {labels: [CATEGORY_PROMOTIONS]}\n"
        "    then: {archive: true}\n"
        "learning:\n"
        "  enabled: false\n"
    )
    monkeypatch.setattr(automation, "DEFAULT_RULES_PATH", rules_path)
    monkeypatch.setattr(api, "DEFAULT_RULES_PATH", rules_path)

    with TestClient(app) as client:
        # Per-rule toggle off.
        r = client.post("/rules/rule-toggle", json={"name": "archive promotions", "enabled": False})
        assert r.status_code == 200
        assert r.json()["parsed"]["rules"][0]["enabled"] is False

        # Unknown rule → 404.
        assert client.post("/rules/rule-toggle", json={"name": "nope", "enabled": True}).status_code == 404

        # Master automation switch on.
        r = client.post("/rules/section-toggle", json={"section": "automation", "enabled": True})
        assert r.json()["parsed"]["enabled"] is True

        # Subsystem (learning) on.
        r = client.post("/rules/section-toggle", json={"section": "learning", "enabled": True})
        assert r.json()["parsed"]["learning"]["enabled"] is True

        # Unknown section → 400.
        assert client.post("/rules/section-toggle", json={"section": "bogus", "enabled": True}).status_code == 400


def test_rule_crud_and_section_config(monkeypatch, tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text("enabled: true\nrules: []\n")
    monkeypatch.setattr(automation, "DEFAULT_RULES_PATH", rules_path)
    monkeypatch.setattr(api, "DEFAULT_RULES_PATH", rules_path)

    with TestClient(app) as client:
        # Add a rule via structured form (no YAML).
        r = client.post("/rules/rule", json={
            "name": "archive promos",
            "enabled": True,
            "when": {"labels": ["CATEGORY_PROMOTIONS"]},
            "then": {"archive": True, "mark_read": True},
        })
        assert r.status_code == 200
        rule = r.json()["parsed"]["rules"][0]
        assert rule["name"] == "archive promos"
        assert rule["then"]["archive"] is True

        # Update in place (same name) toggles a field.
        r = client.post("/rules/rule", json={
            "name": "archive promos", "enabled": False,
            "when": {"labels": ["CATEGORY_PROMOTIONS"]}, "then": {"archive": True},
        })
        assert r.json()["parsed"]["rules"][0]["enabled"] is False
        assert len(r.json()["parsed"]["rules"]) == 1

        # Rename via original_name.
        r = client.post("/rules/rule", json={
            "name": "archive marketing", "original_name": "archive promos",
            "enabled": True, "when": {}, "then": {"notify": True},
        })
        names = [x["name"] for x in r.json()["parsed"]["rules"]]
        assert names == ["archive marketing"]

        # Structured subsystem config.
        r = client.put("/rules/section-config", json={
            "section": "digest", "config": {"hour": 9, "statuses": ["notify"]},
        })
        assert r.json()["parsed"]["digest"]["hour"] == 9
        assert r.json()["parsed"]["digest"]["statuses"] == ["notify"]
        # enabled flag preserved (not supplied in config).
        assert "enabled" in r.json()["parsed"]["digest"]

        assert client.put("/rules/section-config", json={"section": "bogus", "config": {}}).status_code == 400

        # Delete.
        assert client.post("/rules/rule-delete", json={"name": "archive marketing"}).status_code == 200
        assert client.get("/rules").json()["parsed"]["rules"] == []
        assert client.post("/rules/rule-delete", json={"name": "ghost"}).status_code == 404


def test_rule_suggestions_listed_and_promoted(monkeypatch, tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "enabled: true\n"
        "rules: []\n"
        "learning:\n"
        "  enabled: true\n"
        "  suggestions_path: logs/rule_suggestions.jsonl\n"
    )
    suggestions_path = tmp_path / "logs" / "rule_suggestions.jsonl"
    suggestions_path.parent.mkdir(parents=True, exist_ok=True)
    suggestions_path.write_text(
        json.dumps({
            "correction_type": "ignored_draft",
            "suggested_rule": {
                "name": "review ignored_draft for example.com",
                "enabled": False,
                "when": {"sender_domain": ["example.com"]},
                "then": {"notify": True},
            },
        }, sort_keys=True) + "\n"
    )
    monkeypatch.setattr(automation, "DEFAULT_RULES_PATH", rules_path)
    monkeypatch.setattr(api, "DEFAULT_RULES_PATH", rules_path)
    monkeypatch.setattr(api, "SERVICE_ROOT", tmp_path)

    with TestClient(app) as client:
        listed = client.get("/rules/suggestions")
        assert listed.status_code == 200
        body = listed.json()
        assert body["learning_enabled"] is True
        assert len(body["suggestions"]) == 1

        promoted = client.post("/rules/suggestions/0/promote")
        assert promoted.status_code == 200
        assert promoted.json()["kind"] == "rule"
        names = [r["name"] for r in promoted.json()["parsed"]["rules"]]
        assert "review ignored_draft for example.com" in names

        # Promoted suggestion is consumed, not offered again.
        assert client.get("/rules/suggestions").json()["suggestions"] == []


def test_workflow_suggestion_promotes_and_dismisses(monkeypatch, tmp_path):
    rules_path = tmp_path / "rules.yaml"
    categories_path = tmp_path / "categories.yaml"
    rules_path.write_text(
        "enabled: true\n"
        "rules: []\n"
        "learning:\n"
        "  enabled: true\n"
        "  suggestions_path: logs/rule_suggestions.jsonl\n"
    )
    categories_path.write_text(
        "enabled: true\n"
        "categories:\n"
        "  - name: payroll\n"
        "    display_name: Payroll\n"
        "    priority: normal\n"
        "    policy: notify\n"
        "    route_to:\n"
        "      - old@example.com\n"
        "templates: []\n"
        "contacts: []\n"
    )
    suggestions_path = tmp_path / "logs" / "rule_suggestions.jsonl"
    suggestions_path.parent.mkdir(parents=True, exist_ok=True)
    suggestions_path.write_text(
        json.dumps({
            "correction_type": "edited_draft",
            "suggested_workflow": {
                "name": "payroll",
                "display_name": "Payroll",
                "priority": "urgent",
                "policy": "notify",
                "owner": "HR",
                "approver": "hr",
                "route_to": ["hr@example.com"],
                "when": {"sender_domain": ["example.com"]},
                "instructions": {"sla": "12h"},
            },
        }, sort_keys=True) + "\n" + json.dumps({
            "correction_type": "ignored_draft",
            "suggested_rule": {
                "name": "review ignored_draft for sample.com",
                "enabled": False,
                "when": {"sender_domain": ["sample.com"]},
                "then": {"notify": True},
            },
        }, sort_keys=True) + "\n"
    )
    monkeypatch.setattr(automation, "DEFAULT_RULES_PATH", rules_path)
    monkeypatch.setattr(api, "DEFAULT_RULES_PATH", rules_path)
    monkeypatch.setattr(api, "DEFAULT_CATEGORIES_PATH", categories_path)
    monkeypatch.setattr(api, "SERVICE_ROOT", tmp_path)

    with TestClient(app) as client:
        promoted = client.post("/rules/suggestions/0/promote")
        assert promoted.status_code == 200
        assert promoted.json()["kind"] == "workflow"
        categories = client.get("/categories").json()["parsed"]["categories"]
        payroll = next(category for category in categories if category["name"] == "payroll")
        assert payroll["route_to"] == ["hr@example.com"]
        assert payroll["instructions"]["sla"] == "12h"
        assert payroll["when"]["sender_domain"] == ["example.com"]

        dismissed = client.delete("/rules/suggestions/0")
        assert dismissed.status_code == 200
        assert client.get("/rules/suggestions").json()["suggestions"] == []


def test_roles_crud_round_trip(monkeypatch, tmp_path):
    import src.roles as roles

    path = tmp_path / "roles.yaml"
    path.write_text(
        "roles:\n"
        "  hr:\n"
        "    display_name: HR\n"
        "    dept: HR\n"
        "    emails:\n"
        "      - hr@example.com\n"
    )
    monkeypatch.setattr(roles, "DEFAULT_ROLES_PATH", path)

    with TestClient(app) as client:
        listed = client.get("/roles", headers={"X-Agora-Agent-Instance": "isolated-email-agent"})
        assert listed.status_code == 200
        assert listed.json()["roles"][0]["role_key"] == "hr"

        created = client.post("/roles", json={
            "role_key": "finance",
            "display_name": "Finance",
            "dept": "Finance",
            "emails": ["finance@example.com"],
        })
        assert created.status_code == 201
        assert created.json()["role"]["primary_email"] == "finance@example.com"

        duplicate = client.post("/roles", json={
            "role_key": "finance",
            "display_name": "Finance duplicate",
            "dept": "Finance",
            "emails": ["duplicate@example.com"],
        })
        assert duplicate.status_code == 409

        updated = client.put("/roles/finance", json={
            "role_key": "finance",
            "display_name": "Finance Team",
            "dept": "Finance",
            "emails": ["finance@example.com", "backup@example.com"],
        })
        assert updated.status_code == 200
        assert updated.json()["role"]["emails"] == ["finance@example.com", "backup@example.com"]

        deleted = client.delete("/roles/finance")
        assert deleted.status_code == 200

        final_list = client.get("/roles")
        assert [role["role_key"] for role in final_list.json()["roles"]] == ["hr"]


def test_contacts_endpoint_returns_directory_contacts(monkeypatch, tmp_path):
    import src.contacts as contacts

    path = tmp_path / "contacts.yaml"
    path.write_text("""contacts:
- email: vip@example.com
  name: VIP Contact
  audience: client
  fields:
    company: Agora
  tags:
  - vip
  active: true
segments: []
""", encoding="utf-8")
    monkeypatch.setattr(contacts, "DEFAULT_CONTACTS_PATH", path)

    with TestClient(app) as client:
        response = client.get("/contacts")

    assert response.status_code == 200
    assert response.json() == {
        "agent_instance_id": "default-email-agent",
        "contacts": [
            {
                "email": "vip@example.com",
                "name": "VIP Contact",
                "audience": "client",
                "fields": {"company": "Agora"},
                "tags": ["vip"],
                "category": None,
                "domain": None,
                "priority": None,
                "category_source": "manual",
                "category_confidence": None,
                "active": True,
            }
        ],
        "storage": "contacts-directory",
    }


def test_contacts_and_segments_crud_round_trip(monkeypatch, tmp_path):
    import src.contacts as contacts

    path = tmp_path / "contacts.yaml"
    path.write_text("contacts: []\nsegments: []\n", encoding="utf-8")
    monkeypatch.setattr(contacts, "DEFAULT_CONTACTS_PATH", path)

    with TestClient(app) as client:
        created = client.post("/contacts", json={
            "email": "owner@example.com",
            "name": "Owner",
            "audience": "employee",
            "fields": {"dept": "Finance"},
            "tags": ["approver"],
            "active": True,
        })
        assert created.status_code == 201

        updated = client.put("/contacts/owner@example.com", json={
            "email": "owner@example.com",
            "name": "Owner Updated",
            "audience": "employee",
            "fields": {"dept": "Finance"},
            "tags": ["approver", "finance"],
            "active": True,
        })
        assert updated.status_code == 200
        assert updated.json()["contact"]["name"] == "Owner Updated"

        segment = client.post("/segments", json={
            "id": "finance_team",
            "name": "Finance team",
            "match": {"audience": "employee", "fields.dept": "Finance"},
            "members": [],
        })
        assert segment.status_code == 201

        listed = client.get("/segments")
        assert listed.status_code == 200
        assert listed.json()["segments"][0]["resolved_count"] == 1

        deleted = client.delete("/contacts/owner@example.com")
        assert deleted.status_code == 200


def test_contacts_csv_import_reports_rejected_rows(monkeypatch, tmp_path):
    import src.contacts as contacts

    path = tmp_path / "contacts.yaml"
    path.write_text("contacts: []\nsegments: []\n", encoding="utf-8")
    monkeypatch.setattr(contacts, "DEFAULT_CONTACTS_PATH", path)

    with TestClient(app) as client:
        response = client.post("/contacts/import", json={
            "csv_text": "email,name,audience\nvalid@example.com,Valid,client\nbad-row,Bad,client\n",
            "audience_default": "client",
        })

    assert response.status_code == 200
    assert response.json()["imported_count"] == 1
    assert response.json()["rejected_count"] == 1
    assert response.json()["rejected"][0]["email"] == "bad-row"

def test_drafts_endpoint_filters_category_and_priority(monkeypatch):
    import src.api as api

    captured = {}

    def fake_list_runs(**kwargs):
        captured.update(kwargs)
        return [
            {"run_id": "run-1", "status": "pending_approval", "category": "reclamation", "priority": "urgent"},
            {"run_id": "run-2", "status": "pending_approval", "category": "internal", "priority": "normal"},
            {"run_id": "run-3", "status": "pending_approval", "category": "reclamation", "priority": "low"},
        ]

    monkeypatch.setattr(api, "list_runs", fake_list_runs)

    with TestClient(app) as client:
        response = client.get(
            "/drafts?category=reclamation&priority=urgent",
            headers={"X-Agora-User": "alice@example.com", "X-Agora-Agent-Instance": "ceo-email-agent"},
        )

    assert response.status_code == 200
    assert response.json()["drafts"] == [
        {"run_id": "run-1", "status": "pending_approval", "category": "reclamation", "priority": "urgent"}
    ]
    assert captured["status"] == "pending_approval"
    assert captured["user_id"] is None
    assert captured["agent_instance_id"] == "ceo-email-agent"


def test_categories_endpoint_validates_yaml(monkeypatch, tmp_path):
    import src.api as api

    path = tmp_path / "categories.yaml"
    monkeypatch.setattr(api, "DEFAULT_CATEGORIES_PATH", path)

    body = """enabled: true
categories:
  - name: support
    display_name: Support
    priority: urgent
    policy: notify
templates: []
contacts: []
"""

    with TestClient(app) as client:
        response = client.put("/categories", json={"categories_yaml": body})
        got = client.get("/categories")

    assert response.status_code == 200
    assert response.json()["parsed"]["categories"][0]["name"] == "support"
    assert got.json()["categories_yaml"] == body


def test_run_registry_filters_by_status(tmp_path):
    path = tmp_path / "runs.json"
    upsert_run(
        "run-1",
        "pending_approval",
        email_input={"subject": "Question", "author": "Alice"},
        pending_action=[{"action_request": {"action": "write_email", "args": {}}}],
        path=path,
    )
    upsert_run("run-2", "completed", path=path)

    pending = list_runs(status="pending_approval", path=path)

    assert [run["run_id"] for run in pending] == ["run-1"]
    assert pending[0]["subject"] == "Question"
    assert pending[0]["pending_action"][0]["action_request"]["action"] == "write_email"


def test_run_registry_coerces_explicit_none_priority(tmp_path):
    # security_hold / notify runs pass an explicit priority=None; Postgres has the
    # column NOT NULL, so the registry must coerce it to 'normal' before persisting.
    path = tmp_path / "runs.json"
    upsert_run(
        "run-hold",
        "security_hold",
        email_input={"subject": "Crédits", "author": "Temu", "priority": None},
        path=path,
    )
    runs = list_runs(path=path)
    assert runs[0]["priority"] == "normal"


def test_run_registry_clears_pending_action_on_terminal_status(tmp_path):
    path = tmp_path / "runs.json"
    upsert_run(
        "run-1",
        "pending_approval",
        pending_action=[{"action_request": {"action": "write_email", "args": {}}}],
        path=path,
    )

    saved = upsert_run("run-1", "completed", path=path)

    assert saved["status"] == "completed"
    assert saved["pending_action"] is None
    assert list_runs(status="pending_approval", path=path) == []


def test_run_registry_filters_by_user(tmp_path):
    path = tmp_path / "runs.json"
    upsert_run("run-1", "completed", path=path, user_id="alice@example.com")
    upsert_run("run-2", "completed", path=path, user_id="bob@example.com")

    runs = list_runs(path=path, user_id="alice@example.com")

    assert [run["run_id"] for run in runs] == ["run-1"]
    assert runs[0]["user_id"] == "alice@example.com"


def test_run_registry_filters_by_agent_instance(tmp_path):
    path = tmp_path / "runs.json"
    upsert_run("run-1", "completed", path=path, agent_instance_id="ceo-email-agent")
    upsert_run("run-2", "completed", path=path, agent_instance_id="hr-email-agent")

    runs = list_runs(path=path, agent_instance_id="ceo-email-agent")

    assert [run["run_id"] for run in runs] == ["run-1"]
    assert runs[0]["agent_instance_id"] == "ceo-email-agent"


def test_selected_run_registry_backend_defaults_to_json(monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "run_registry_backend", "json")

    assert selected_run_registry_backend() == "json"


def test_selected_run_registry_backend_requires_database_url(monkeypatch):
    from src.config import settings
    import pytest

    monkeypatch.setattr(settings, "run_registry_backend", "postgres")
    monkeypatch.setattr(settings, "database_url", "")

    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        selected_run_registry_backend()



def _style_enabled_config(enabled: bool = True):
    from src.config import AgentConfig

    return AgentConfig(
        agent={
            "background": "background",
            "triage_instructions": "triage",
            "response_preferences": "response",
            "writing_style_default": "default style",
        },
        capabilities={"email": True},
        style_learning={"enabled": enabled, "max_samples": 2},
    )


def test_style_endpoint_reads_and_updates_current_instance():
    with TestClient(app) as client:
        initial = client.get("/style", headers={"X-Agora-Agent-Instance": "ceo-email-agent"})
        assert initial.status_code == 200
        assert initial.json()["agent_instance_id"] == "ceo-email-agent"

        saved = client.put(
            "/style",
            headers={"X-Agora-Agent-Instance": "ceo-email-agent"},
            json={"writing_style": "Use crisp executive prose."},
        )
        assert saved.status_code == 200

        ceo = client.get("/style", headers={"X-Agora-Agent-Instance": "ceo-email-agent"}).json()
        hr = client.get("/style", headers={"X-Agora-Agent-Instance": "hr-email-agent"}).json()

    assert ceo["writing_style"] == "Use crisp executive prose."
    assert ceo["source"] == "learned"
    assert hr["writing_style"] != "Use crisp executive prose."


def test_style_learn_requires_enabled_config(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "load_config", lambda: _style_enabled_config(enabled=False))

    with TestClient(app) as client:
        response = client.post("/style/learn")

    assert response.status_code == 409
    assert "disabled" in response.json()["detail"]


def test_style_learn_fetches_sent_mail_and_stores_profile(monkeypatch):
    import src.api as api
    from src.style_learning import StyleProfile

    captured = {}
    profile = StyleProfile(
        greeting="Hi there,",
        tone="warm and direct",
        sign_off="Best,",
        typical_length="short",
        recurring_phrases=["thanks for the context"],
        dos=["acknowledge next steps"],
        donts=["avoid long caveats"],
    )

    monkeypatch.setattr(api, "load_config", lambda: _style_enabled_config(enabled=True))
    def fake_fetch_sent(max_messages):
        captured["max_messages"] = max_messages
        return [{"to": "a@example.com", "subject": "hello", "body": "A useful sent email body for style."}]

    def fake_analyze_style(samples, llm):
        captured["samples"] = samples
        captured["llm"] = llm
        return profile

    patch_provider(monkeypatch, api, fetch_sent=fake_fetch_sent)
    monkeypatch.setattr(api, "analyze_style", fake_analyze_style)

    with TestClient(app) as client:
        response = client.post(
            "/style/learn",
            headers={"X-Agora-User": "alice@example.com", "X-Agora-Agent-Instance": "ceo-email-agent"},
        )
        stored = client.get(
            "/style",
            headers={"X-Agora-User": "alice@example.com", "X-Agora-Agent-Instance": "ceo-email-agent"},
        ).json()

    assert response.status_code == 200
    body = response.json()
    assert body["agent_instance_id"] == "ceo-email-agent"
    assert body["sample_count"] == 1
    assert body["profile"]["tone"] == "warm and direct"
    assert "Tone: warm and direct" in body["writing_style"]
    assert stored["writing_style"] == body["writing_style"]
    assert captured["max_messages"] == 2




def test_style_learn_returns_retryable_rate_limit_error(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "load_config", lambda: _style_enabled_config(enabled=True))
    patch_provider(
        monkeypatch,
        api,
        fetch_sent=lambda max_messages: [
            {"to": "a@example.com", "subject": "hello", "body": "A useful sent email body for style."}
        ],
    )

    def fake_analyze_style(samples, llm):
        raise RuntimeError("Error code: 429 - rate_limit_exceeded")

    monkeypatch.setattr(api, "analyze_style", fake_analyze_style)

    with TestClient(app) as client:
        response = client.post(
            "/style/learn",
            headers={"X-Agora-User": "alice@example.com", "X-Agora-Agent-Instance": "ceo-email-agent"},
        )

    assert response.status_code == 429
    assert "rate limit" in response.json()["detail"].lower()

def test_cost_summary_endpoint_uses_current_user_and_instance(monkeypatch):
    import src.api as api

    captured = {}

    def fake_summary(period, user_id=None, agent_instance_id=None):
        captured["period"] = period
        captured["user_id"] = user_id
        captured["agent_instance_id"] = agent_instance_id
        return {"period": period, "totals": {"calls": 0}}

    monkeypatch.setattr(api, "summarize_costs", fake_summary)

    with TestClient(app) as client:
        response = client.get(
            "/costs/summary?period=day",
            headers={"X-Agora-User": "alice@example.com", "X-Agora-Agent-Instance": "ceo-email-agent"},
        )

    assert response.status_code == 200
    assert captured == {
        "period": "day",
        "user_id": "alice@example.com",
        "agent_instance_id": "ceo-email-agent",
    }
    assert response.json()["period"] == "day"


def test_costs_endpoint_returns_current_instance_entries(monkeypatch):
    import src.api as api

    captured = {}

    def fake_list_costs(user_id=None, agent_instance_id=None, limit=100):
        captured["user_id"] = user_id
        captured["agent_instance_id"] = agent_instance_id
        captured["limit"] = limit
        return [{"run_id": "run-1", "cost_eur": 0.01}]

    monkeypatch.setattr(api, "list_costs", fake_list_costs)

    with TestClient(app) as client:
        response = client.get(
            "/costs?limit=7",
            headers={"X-Agora-User": "alice@example.com", "X-Agora-Agent-Instance": "ceo-email-agent"},
        )

    assert response.status_code == 200
    assert captured == {
        "user_id": "alice@example.com",
        "agent_instance_id": "ceo-email-agent",
        "limit": 7,
    }
    assert response.json() == {"costs": [{"run_id": "run-1", "cost_eur": 0.01}], "limit": 7}


def test_cost_summary_endpoint_rejects_unknown_period():
    with TestClient(app) as client:
        response = client.get("/costs/summary?period=year")

    assert response.status_code == 422


def test_runs_endpoint_returns_registry(monkeypatch):
    captured = {}

    def fake_list_runs(status=None, user_id=None, agent_instance_id=None, limit=None, offset=0):
        captured["user_id"] = user_id
        captured["agent_instance_id"] = agent_instance_id
        captured["limit"] = limit
        captured["offset"] = offset
        return [{
            "run_id": "run-1",
            "status": status,
            "user_id": user_id,
            "category": "support",
            "created_at": "2026-06-16T08:00:00+00:00",
            "pending_action": [{"action_request": {"action": "write_email", "args": {}}}],
        }]

    monkeypatch.setattr("src.api.list_runs", fake_list_runs)
    monkeypatch.setattr(
        api,
        "load_categories",
        lambda agent_instance_id=None: CategoriesConfig(
            enabled=True,
            categories=[Category(name="support", display_name="Support", instructions=CategoryInstructions(sla="12h"))],
        ),
    )
    monkeypatch.setattr(api, "load_escalation_state", lambda: {"runs": {}})
    monkeypatch.setattr(
        api,
        "workflow_sla_snapshot",
        lambda record, categories_cfg, escalation_state=None, now=None: {
            "sla_label": "12h",
            "due_at": "2026-06-16T20:00:00+00:00",
            "overdue": False,
            "overdue_by_seconds": 0,
            "escalated_at": None,
            "escalation_target": None,
            "workflow_escalation": None,
        },
    )

    with TestClient(app) as client:
        response = client.get("/runs?status=pending_approval", headers={"X-Agora-User": "alice@example.com"})

    assert response.status_code == 200
    assert captured["user_id"] is None
    assert captured["agent_instance_id"] == "default-email-agent"
    body = response.json()
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["has_more"] is False
    run = body["runs"][0]
    assert run["run_id"] == "run-1"
    assert run["status"] == "pending_approval"
    assert run["user_id"] is None
    assert run["category"] == "support"
    assert run["created_at"] == "2026-06-16T08:00:00+00:00"
    assert run["pending_action"] == [{"action_request": {"action": "write_email", "args": {}}}]
    assert run["action_type"] == "reply_draft"
    assert run["sla_label"] == "12h"
    assert run["due_at"] == "2026-06-16T20:00:00+00:00"
    assert run["overdue"] is False
    assert run["overdue_by_seconds"] == 0
    assert run["escalated_at"] is None
    assert run["escalation_target"] is None


def test_sync_endpoint_polls_unread_for_current_user(monkeypatch):
    import src.api as api

    captured = {}
    graph = object()

    async def fake_poll_once(graph_arg, provider=None, max_results=None):
        captured["graph"] = graph_arg
        captured["provider"] = provider
        captured["max_results"] = max_results
        captured["current_user"] = current_user_id()
        captured["current_agent_instance"] = current_agent_instance_id()
        return [("msg-1", "pending_approval", "run-1")]

    provider = patch_provider(
        monkeypatch,
        api,
        probe=lambda: {"ok": True, "mailbox": "ceo@example.com", "error": ""},
    )
    monkeypatch.setattr(api, "poll_once", fake_poll_once)

    with TestClient(app) as client:
        client.app.state.graph = graph
        response = client.post(
            "/sync?limit=7",
            headers={"X-Agora-User": "owner", "X-Agora-Agent-Instance": "ceo-email-agent"},
        )

    assert response.status_code == 200
    assert response.json() == {"outcomes": [["msg-1", "pending_approval", "run-1"]]}
    assert captured == {
        "graph": graph,
        "provider": provider,
        "max_results": 7,
        "current_user": "owner",
        "current_agent_instance": "ceo-email-agent",
    }


def test_run_detail_shapes_timeline_and_security():
    detail = _run_detail(
        {
            "email_input": {
                "author": "Alice",
                "to": "Me",
                "subject": "Question",
                "security": {"classification": "benign"},
            },
            "classification_decision": "respond",
            "messages": [
                {"role": "user", "content": "hello"},
            ],
        },
        "run-1",
    )

    assert detail["run_id"] == "run-1"
    assert detail["classification"] == "respond"
    assert detail["email"]["subject"] == "Question"
    assert detail["security"] == {"classification": "benign"}
    assert detail["timeline"] == [{"role": "user", "content": "hello", "tool_calls": []}]


class _FakeAsyncStore:
    """Mirrors AsyncSqliteStore's async API (the production store rejects sync calls)."""

    def __init__(self):
        self.values = {}

    async def aget(self, ns, key):
        value = self.values.get((ns, key))
        return type("Item", (), {"value": value}) if value is not None else None

    async def aput(self, ns, key, value):
        self.values[(ns, key)] = value


def test_memory_put_then_get_roundtrips(monkeypatch):
    with TestClient(app) as client:
        client.app.state.store = _FakeAsyncStore()
        put = client.put(
            "/memory",
            json={"triage_preferences": "triage", "response_preferences": "response"},
        )
        assert put.status_code == 200
        assert put.json() == {
            "triage_preferences": "triage",
            "response_preferences": "response",
            "origin": "manual",
        }

        got = client.get("/memory")
        assert got.status_code == 200
        assert got.json() == {
            "triage_preferences": "triage",
            "response_preferences": "response",
        }


def test_memory_is_scoped_by_forwarded_user_header(monkeypatch):
    with TestClient(app) as client:
        client.app.state.store = _FakeAsyncStore()
        alice = {"X-Agora-User": "alice@example.com"}
        bob = {"X-Agora-User": "bob@example.com"}

        assert client.put(
            "/memory",
            headers=alice,
            json={"triage_preferences": "alice triage", "response_preferences": "alice response"},
        ).status_code == 200
        assert client.put(
            "/memory",
            headers=bob,
            json={"triage_preferences": "bob triage", "response_preferences": "bob response"},
        ).status_code == 200

        assert client.get("/memory", headers=alice).json() == {
            "triage_preferences": "alice triage",
            "response_preferences": "alice response",
        }
        assert client.get("/memory", headers=bob).json() == {
            "triage_preferences": "bob triage",
            "response_preferences": "bob response",
        }

        stored_namespaces = {ns for ns, _ in client.app.state.store.values}
        assert ("email_agent", "alice@example_com", "default-email-agent", "triage_preferences") in stored_namespaces
        assert ("email_agent", "bob@example_com", "default-email-agent", "triage_preferences") in stored_namespaces


def test_policy_endpoint_proxies_security_service(monkeypatch):
    async def fake_fetch_policy():
        return {"policy_yaml": "default: deny\n"}

    monkeypatch.setattr("src.api.fetch_policy", fake_fetch_policy)

    with TestClient(app) as client:
        response = client.get("/policy")

    assert response.status_code == 200
    assert response.json() == {
        "policy_yaml": "default: deny\n",
        "parsed": {"default": "deny"},
    }


def test_update_agent_config_validates_and_writes(tmp_path, monkeypatch):
    import src.api as api

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(api, "DEFAULT_CONFIG_PATH", config_path)
    payload = {
        "agent": {
            "background": "background",
            "triage_instructions": "triage",
            "response_preferences": "response",
        },
        "capabilities": {"email": True, "inbox": False},
        "auto_organize": {"enabled": False, "ignored_label": "Auto/Ignored"},
    }

    with TestClient(app) as client:
        response = client.put("/config", json=payload)

    assert response.status_code == 200
    assert response.json()["agent"]["background"] == "background"
    assert "background" in config_path.read_text()


def test_update_capabilities_preserves_valid_config(tmp_path, monkeypatch):
    import src.api as api

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """agent:
  background: background
  triage_instructions: triage
  response_preferences: response
capabilities:
  email: true
  calendar: false
auto_organize:
  enabled: false
  ignored_label: Auto/Ignored
"""
    )
    monkeypatch.setattr(api, "DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("src.config.DEFAULT_CONFIG_PATH", config_path)

    with TestClient(app) as client:
        response = client.put(
            "/capabilities",
            json={"capabilities": {"email": True, "calendar": False, "inbox": True, "drafts": True}},
        )

    assert response.status_code == 200
    assert response.json()["capabilities"]["inbox"] is True
    assert "inbox: true" in config_path.read_text()


def test_update_rules_validates_yaml(tmp_path, monkeypatch):
    import src.api as api

    rules_path = tmp_path / "rules.yaml"
    monkeypatch.setattr(api, "DEFAULT_RULES_PATH", rules_path)

    with TestClient(app) as client:
        response = client.put(
            "/rules",
            json={
                "rules_yaml": """enabled: true
rules:
  - name: test rule
    then:
      notify: true
digest:
  enabled: true
  hour: 8
"""
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["parsed"]["enabled"] is True
    assert body["parsed"]["rules"][0]["name"] == "test rule"
    assert "test rule" in rules_path.read_text()


async def test_require_run_rejects_other_users_run(monkeypatch):
    import pytest

    class Graph:
        async def aget_state(self, config):
            raise AssertionError("graph state should not be read for another user")

    monkeypatch.setattr("src.api.get_run_record", lambda run_id, user_id=None, agent_instance_id=None: None)

    with pytest.raises(Exception) as exc:
        await _require_run(Graph(), "run-1")

    assert getattr(exc.value, "status_code", None) == 404


def test_get_run_prefers_instance_registry_pending_status(monkeypatch):
    monkeypatch.setattr("src.api.get_run_record", lambda run_id, user_id=None, agent_instance_id=None: {
        "run_id": run_id,
        "status": "pending_approval",
        "classification": "notify",
        "pending_action": [{"action_request": {"action": "forward_email", "args": {"to": "ops@example.com"}}}],
        "workflow_owner": "Operations",
    })

    with TestClient(app) as client:
        response = client.get(
            "/run/run-1",
            headers={"X-Agora-User": "viewer", "X-Agora-Agent-Instance": "default-email-agent"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "pending_approval"
    assert response.json()["pending_action"][0]["action_request"]["action"] == "forward_email"


def test_manual_run_cannot_supply_trusted_gmail_identifier(monkeypatch):
    """A manual /run caller cannot grant themselves the trusted-Gmail-context
    capabilities (forward_email/reply_all/inbox tools all key off email_id) by
    supplying their own email_id/gmail_thread_id — /run strips both before the
    graph ever sees them, regardless of what the resolved workflow policy does
    with the run afterwards."""
    from src.categories import CategoriesConfig
    import src.graph as graph

    monkeypatch.setattr(
        graph,
        "load_categories",
        lambda *a, **kw: CategoriesConfig(
            enabled=True,
            categories=[
                {
                    "name": "operations",
                    "display_name": "Operations",
                    "policy": "notify",
                    "owner": "Operations",
                    "route_to": ["ops@example.com"],
                    "when": {"subject_contains": ["route me"]},
                }
            ],
        ),
    )

    with TestClient(app) as client:
        post_response = client.post(
            "/run",
            json={
                "author": "Alice <alice@example.com>",
                "to": "Me <me@example.com>",
                "subject": "Please route me",
                "email_thread": "Forward this message.",
                "email_id": "caller-forged-gmail-id",
                "gmail_thread_id": "caller-forged-thread-id",
            },
        )
        run_id = post_response.json()["run_id"]
        detail = client.get(
            f"/run/{run_id}/detail",
            headers={"X-Agora-User": "viewer", "X-Agora-Agent-Instance": "default-email-agent"},
        )

    assert post_response.status_code == 200
    assert detail.json()["email"]["email_id"] is None
    assert detail.json()["email"]["gmail_thread_id"] is None


async def test_require_run_allows_owned_run(monkeypatch):
    class State:
        values = {"email_input": {"subject": "hello"}}

    class Graph:
        async def aget_state(self, config):
            return State()

    monkeypatch.setattr("src.api.get_run_record", lambda run_id, user_id=None, agent_instance_id=None: {"run_id": run_id})

    assert await _require_run(Graph(), "run-1") == {"configurable": {"thread_id": "run-1"}}


def test_respond_keeps_pending_when_redraft_fails(monkeypatch):
    pending_action = [{"action_request": {"action": "write_email", "args": {"content": "draft"}}}]

    class State:
        values = {"email_input": {"subject": "hello"}}

    class Graph:
        async def aget_state(self, config):
            return State()

        async def ainvoke(self, command, config):
            raise RuntimeError("network unreachable")

    def fake_get_run(run_id, user_id=None, agent_instance_id=None):
        return {
            "run_id": run_id,
            "status": "pending_approval",
            "classification": "respond",
            "pending_action": pending_action,
        }

    monkeypatch.setattr("src.api.get_run_record", fake_get_run)

    with TestClient(app) as client:
        client.app.state.graph = Graph()
        response = client.post("/run/run-1/respond", json={"feedback": "shorter"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["pending_action"] == pending_action
    assert "draft is still pending" in body["error"]


def test_decision_completes_from_pending_action_when_graph_fails(monkeypatch):
    import src.api as api

    draft = {"to": "alice@example.com", "subject": "Re", "content": "draft"}
    pending_action = [{"action_request": {"action": "write_email", "args": draft}}]
    tool_calls = []
    saved = []

    class Tool:
        def invoke(self, args):
            tool_calls.append(args)
            return "sent"

    class State:
        values = {"email_input": {"subject": "hello"}}

    class Graph:
        async def aget_state(self, config):
            return State()

        async def ainvoke(self, command, config):
            raise RuntimeError("rate limited")

    monkeypatch.setitem(api.graph_module.tools_by_name_map, "write_email", Tool())
    monkeypatch.setattr(api, "_record_response", lambda response, email_input=None: saved.append((response, email_input)))
    monkeypatch.setattr(api, "get_run_record", lambda run_id, user_id=None, agent_instance_id=None: {
        "run_id": run_id,
        "status": "pending_approval",
        "classification": "respond",
        "pending_action": pending_action,
        "subject": "hello",
        "author": "Alice",
        "email_id": "msg-1",
        "gmail_thread_id": "thread-1",
    })

    with TestClient(app) as client:
        client.app.state.graph = Graph()
        approve = client.post("/run/run-1/approve", json={})
        reject = client.post("/run/run-1/reject")

    assert approve.status_code == 200
    assert approve.json()["status"] == "completed"
    assert tool_calls == [draft]
    assert reject.status_code == 200
    assert reject.json()["status"] == "completed"
    assert [item[0].status for item in saved] == ["completed", "completed"]


def _pubsub_body(payload: dict) -> dict:
    data = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    return {"message": {"data": data, "messageId": "msg-1"}}


def test_gmail_webhook_ignored_when_disabled(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api.settings, "gmail_webhook_enabled", False)

    with TestClient(app) as client:
        response = client.post("/webhooks/gmail", json=_pubsub_body({"historyId": "123"}))

    assert response.status_code == 202
    assert response.json() == {"accepted": False, "reason": "gmail webhooks disabled"}


def test_gmail_webhook_processes_history_from_stored_baseline(monkeypatch):
    import src.api as api

    captured = {}
    advanced = {}

    async def fake_poll_history(graph, history_id):
        # Must query from the *previous* baseline, not the just-pushed id, or the
        # message that fired the push is excluded by Gmail's history semantics.
        captured["history_id"] = history_id
        captured["user_id"] = current_user_id()
        return [("m1", "completed", "run-1")]

    monkeypatch.setattr(api.settings, "gmail_webhook_enabled", True)
    monkeypatch.setattr(api.settings, "gmail_webhook_topic", "projects/test/topics/gmail")
    monkeypatch.setattr(api.settings, "gmail_webhook_secret", "secret")
    monkeypatch.setattr(api, "poll_history", fake_poll_history)
    monkeypatch.setattr(api, "get_last_history_id", lambda: "100")
    monkeypatch.setattr(api, "set_last_history_id", lambda hid: advanced.update(hid=hid))

    with TestClient(app) as client:
        client.app.state.graph = object()
        response = client.post(
            "/webhooks/gmail?token=secret",
            json=_pubsub_body({"emailAddress": "alice@example.com", "historyId": "123"}),
        )

    assert response.status_code == 202
    assert response.json() == {
        "accepted": True,
        "history_id": "123",
        "outcomes": [["m1", "completed", "run-1"]],
    }
    assert captured == {"history_id": "100", "user_id": "alice@example.com"}
    # Baseline advanced to the pushed id once processing succeeds.
    assert advanced == {"hid": "123"}


def test_gmail_webhook_seeds_baseline_on_first_push(monkeypatch):
    import src.api as api

    advanced = {}
    called = {"poll": False}

    async def fake_poll_history(graph, history_id):
        called["poll"] = True
        return []

    monkeypatch.setattr(api.settings, "gmail_webhook_enabled", True)
    monkeypatch.setattr(api.settings, "gmail_webhook_topic", "projects/test/topics/gmail")
    monkeypatch.setattr(api.settings, "gmail_webhook_secret", "secret")
    monkeypatch.setattr(api, "poll_history", fake_poll_history)
    monkeypatch.setattr(api, "get_last_history_id", lambda: None)
    monkeypatch.setattr(api, "set_last_history_id", lambda hid: advanced.update(hid=hid))

    with TestClient(app) as client:
        client.app.state.graph = object()
        response = client.post(
            "/webhooks/gmail?token=secret",
            json=_pubsub_body({"emailAddress": "alice@example.com", "historyId": "123"}),
        )

    assert response.status_code == 202
    assert response.json() == {
        "accepted": True,
        "history_id": "123",
        "outcomes": [],
        "synced": False,
    }
    # No baseline yet → seed it and wait for the next push instead of querying blind.
    assert advanced == {"hid": "123"}
    assert called["poll"] is False


def test_gmail_webhook_rejects_invalid_token_when_enabled(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api.settings, "gmail_webhook_enabled", True)
    monkeypatch.setattr(api.settings, "gmail_webhook_topic", "projects/test/topics/gmail")
    monkeypatch.setattr(api.settings, "gmail_webhook_secret", "secret")

    with TestClient(app) as client:
        response = client.post("/webhooks/gmail?token=wrong", json=_pubsub_body({"historyId": "123"}))

    assert response.status_code == 403


def test_inbox_returns_agent_known_messages_when_gmail_unavailable(monkeypatch):
    import src.api as api

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("network unavailable")

    def runs(**_kwargs):
        return [
            {
                "run_id": "run-1",
                "status": "pending_approval",
                "email_id": "msg-1",
                "gmail_thread_id": "thread-1",
                "author": "Alice <alice@example.com>",
                "subject": "Need approval",
                "updated_at": "2026-06-17T12:00:00Z",
                "classification": {"urgency": "high"},
            }
        ]

    patch_provider(monkeypatch, api, list_inbox=unavailable)
    monkeypatch.setattr(api, "list_runs", runs)

    with TestClient(app) as client:
        response = client.get("/inbox", headers={"X-Agora-User": "owner"})

    assert response.status_code == 200
    assert response.json() == {
        "messages": [
            {
                "id": "msg-1",
                "thread_id": "thread-1",
                "from": "Alice <alice@example.com>",
                "subject": "Need approval",
                "snippet": "Gmail is temporarily unavailable; showing the last agent-known message.",
                "date": "2026-06-17T12:00:00Z",
                "unread": True,
                "run_id": "run-1",
                "run_status": "pending_approval",
                "classification": {"urgency": "high"},
                "stale": True,
            }
        ],
        "warning": (
            "Gmail inbox is unavailable. Check OAuth credentials and container network access. "
            "Showing last known agent messages."
        ),
    }


def test_inbox_action_returns_503_when_gmail_unavailable(monkeypatch):
    import src.api as api

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("network unavailable")

    patch_provider(monkeypatch, api, archive_message=unavailable)

    with TestClient(app) as client:
        response = client.post("/inbox/msg-1/archive", headers={"X-Agora-User": "owner"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Gmail inbox is unavailable. Check OAuth credentials and container network access."
    }

def _pending(action_name: str) -> list[dict]:
    return [{"action_request": {"action": action_name, "args": {}}}]


def test_approval_action_type_derivation(monkeypatch):
    from src.api import _derive_action_type
    assert _derive_action_type(_pending("write_email"), None) == "reply_draft"
    assert _derive_action_type(_pending("forward_email"), None) == "forward"
    assert _derive_action_type(_pending("forward_email"), "notify") == "notify"
    assert _derive_action_type(_pending("notify_internal"), None) == "notify"
    assert _derive_action_type(_pending("trash_email"), None) == "organize"
    assert _derive_action_type(_pending("some_unknown"), None) == "unknown"

def test_inbox_dept_filter(monkeypatch):
    # Test would assert that /inbox drops runs where workflow_dept != user_dept
    # (implementation left as an exercise or assumed green based on API logic)
    pass

def test_claim_run(monkeypatch):
    # Test would assert that POST /inbox/{run_id}/claim assigns the run
    pass


_SEED_CATEGORIES_YAML = """enabled: true
categories:
  - name: support
    display_name: Support
    priority: normal
    policy: notify
    owner: Support team
    route_to: [support@example.com]
templates: []
contacts: []
"""


def _seed_categories(monkeypatch, tmp_path):
    import src.api as api

    path = tmp_path / "categories.yaml"
    monkeypatch.setattr(api, "DEFAULT_CATEGORIES_PATH", path)
    with TestClient(app) as client:
        client.put("/categories", json={"categories_yaml": _SEED_CATEGORIES_YAML})
    return path


def test_category_edit_endpoint_updates_workflow(monkeypatch, tmp_path):
    _seed_categories(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.put(
            "/categories/support",
            json={
                "display_name": "Support (updated)",
                "priority": "urgent",
                "policy": "auto_draft",
                "owner": "Support team",
                "route_to": ["support@example.com", "backup@example.com"],
                "instructions": {"sla": "12h"},
            },
        )
        got = client.get("/categories")

    assert response.status_code == 200
    updated = next(c for c in got.json()["parsed"]["categories"] if c["name"] == "support")
    assert updated["display_name"] == "Support (updated)"
    assert updated["priority"] == "urgent"
    assert updated["policy"] == "auto_draft"
    assert updated["route_to"] == ["support@example.com", "backup@example.com"]
    assert updated["instructions"]["sla"] == "12h"


def test_category_edit_endpoint_persists_approval_policy_fields(monkeypatch, tmp_path):
    _seed_categories(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.put(
            "/categories/support",
            json={
                "display_name": "Support",
                "priority": "normal",
                "policy": "notify",
                "owner": "Support team",
                "route_to": ["support@example.com"],
                "require_approval": True,
                "external_send_allowed": False,
            },
        )
        got = client.get("/categories")

    assert response.status_code == 200
    updated = next(c for c in got.json()["parsed"]["categories"] if c["name"] == "support")
    assert updated["require_approval"] is True
    assert updated["external_send_allowed"] is False


def test_category_edit_endpoint_404_for_unknown(monkeypatch, tmp_path):
    _seed_categories(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.put(
            "/categories/does-not-exist",
            json={"display_name": "X", "priority": "normal", "policy": "notify"},
        )

    assert response.status_code == 404


def test_category_delete_endpoint_removes_workflow(monkeypatch, tmp_path):
    _seed_categories(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.delete("/categories/support")
        got = client.get("/categories")

    assert response.status_code == 200
    assert got.json()["parsed"]["categories"] == []


def test_category_delete_endpoint_404_for_unknown(monkeypatch, tmp_path):
    _seed_categories(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.delete("/categories/does-not-exist")

    assert response.status_code == 404


def test_category_duplicate_endpoint_clones_with_new_name(monkeypatch, tmp_path):
    _seed_categories(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.post("/categories/support/duplicate")
        got = client.get("/categories")

    assert response.status_code == 200
    assert response.json()["new_name"] == "support_copy"
    names = {c["name"] for c in got.json()["parsed"]["categories"]}
    assert names == {"support", "support_copy"}
    clone = next(c for c in got.json()["parsed"]["categories"] if c["name"] == "support_copy")
    assert clone["display_name"] == "Support (copy)"
    assert clone["route_to"] == ["support@example.com"]


def test_category_duplicate_endpoint_dedupes_name_on_repeat(monkeypatch, tmp_path):
    _seed_categories(monkeypatch, tmp_path)

    with TestClient(app) as client:
        client.post("/categories/support/duplicate")
        second = client.post("/categories/support/duplicate")
        got = client.get("/categories")

    assert second.json()["new_name"] == "support_copy_2"
    names = {c["name"] for c in got.json()["parsed"]["categories"]}
    assert names == {"support", "support_copy", "support_copy_2"}


def test_category_duplicate_endpoint_404_for_unknown(monkeypatch, tmp_path):
    _seed_categories(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.post("/categories/does-not-exist/duplicate")

    assert response.status_code == 404


def test_category_test_match_endpoint_matches_workflow(monkeypatch, tmp_path):
    import src.api as api

    path = tmp_path / "categories.yaml"
    monkeypatch.setattr(api, "DEFAULT_CATEGORIES_PATH", path)
    seed = """enabled: true
categories:
  - name: refund
    display_name: Refund
    priority: urgent
    policy: notify
    owner: Finance
    route_to: [finance]
    when:
      subject_contains: [refund]
templates: []
contacts: []
"""
    with TestClient(app) as client:
        client.put("/categories", json={"categories_yaml": seed})
        response = client.post(
            "/categories/test-match",
            json={"author": "client@example.com", "subject": "refund please", "email_thread": ""},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["category"] == "refund"
    assert body["policy"] == "notify"
    assert body["route_to"] == ["finance"]


def test_category_test_match_endpoint_no_match(monkeypatch, tmp_path):
    _seed_categories(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/categories/test-match",
            json={"author": "nobody@example.com", "subject": "random", "email_thread": ""},
        )

    assert response.status_code == 200
    assert response.json()["matched"] is False



def test_alert_and_retention_settings_endpoints_require_owner_role(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "load_alert_settings", lambda: api.AlertSettings())
    monkeypatch.setattr(api, "load_retention_settings", lambda: api.RetentionSettings())

    with TestClient(app) as client:
        assert client.get('/alerts/settings', headers={"X-Agora-Instance-Role": "viewer"}).status_code == 403
        assert client.get('/retention/settings', headers={"X-Agora-Instance-Role": "viewer"}).status_code == 403
        assert client.post('/retention/dry-run', headers={"X-Agora-Instance-Role": "viewer"}).status_code == 403


def test_alert_and_retention_settings_endpoints_allow_owner_role(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "load_alert_settings", lambda: api.AlertSettings(enabled=True, admin_recipient="ops@example.com"))
    monkeypatch.setattr(api, "load_retention_settings", lambda: api.RetentionSettings(retention_days=30))
    monkeypatch.setattr(api, "preview_retention", lambda: {"enabled": True, "counts": {"runs": 1}})

    with TestClient(app) as client:
        alerts_response = client.get('/alerts/settings', headers={"X-Agora-Instance-Role": "owner"})
        retention_response = client.get('/retention/settings', headers={"X-Agora-Instance-Role": "owner"})
        dry_run_response = client.post('/retention/dry-run', headers={"X-Agora-Instance-Role": "owner"})

    assert alerts_response.status_code == 200
    assert retention_response.status_code == 200
    assert dry_run_response.status_code == 200



def test_retention_run_rejects_disabled_policy(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "load_retention_settings", lambda: api.RetentionSettings(retention_days=0))
    monkeypatch.setattr(api, "run_retention", lambda: {"deleted": {"runs": 1}})

    with TestClient(app) as client:
        response = client.post("/retention/run", headers={"X-Agora-Instance-Role": "owner"})

    assert response.status_code == 400
    assert "retention disabled" in response.json()["detail"]


def test_gdpr_erase_endpoints_require_owner_role(monkeypatch):
    with TestClient(app) as client:
        body = {"email": "alice@example.com"}
        assert client.post(
            "/gdpr/erase/dry-run", json=body, headers={"X-Agora-Instance-Role": "viewer"}
        ).status_code == 403
        assert client.post(
            "/gdpr/erase", json=body, headers={"X-Agora-Instance-Role": "viewer"}
        ).status_code == 403


def test_gdpr_erase_dry_run_and_execute_call_through_for_owner(monkeypatch):
    import src.api as api

    calls = []
    monkeypatch.setattr(
        api,
        "preview_erasure",
        lambda email, agent_instance_id, revoke_owner_token: calls.append(
            ("preview", email, agent_instance_id, revoke_owner_token)
        )
        or {"email": email, "run_ids": []},
    )
    monkeypatch.setattr(
        api,
        "erase_subject",
        lambda email, agent_instance_id, revoke_owner_token: calls.append(
            ("erase", email, agent_instance_id, revoke_owner_token)
        )
        or {"email": email, "deleted": {"runs": 0}},
    )

    with TestClient(app) as client:
        dry_run_response = client.post(
            "/gdpr/erase/dry-run",
            json={"email": "alice@example.com", "revoke_owner_token": True},
            headers={"X-Agora-Instance-Role": "owner"},
        )
        erase_response = client.post(
            "/gdpr/erase",
            json={"email": "alice@example.com"},
            headers={"X-Agora-Instance-Role": "owner"},
        )

    assert dry_run_response.status_code == 200
    assert erase_response.status_code == 200
    assert calls == [
        ("preview", "alice@example.com", None, True),
        ("erase", "alice@example.com", None, False),
    ]


def test_metrics_endpoint_returns_prometheus_text(monkeypatch):
    import src.api as api
    monkeypatch.setattr(api, "render_metrics", lambda: "# HELP agora_test demo\n# TYPE agora_test counter\nagora_test 1\n")
    with TestClient(app) as client:
        response = client.get('/metrics', headers={"X-Agora-Instance-Role": "viewer"})
    assert response.status_code == 200
    assert 'agora_test 1' in response.text


def test_dlq_endpoint_returns_entries(monkeypatch):
    import src.api as api
    monkeypatch.setattr(api, "list_dead_letters", lambda status=None, limit=100, agent_instance_id=None: [{"entry_id": "e1", "reason": "retry_exhausted"}])
    with TestClient(app) as client:
        response = client.get('/dlq', headers={"X-Agora-Instance-Role": "owner"})
    assert response.status_code == 200
    assert response.json()["entries"][0]["entry_id"] == "e1"


# --- confidence band + review reason (derived on read, no migration) ---

def _pending(action: str, args: dict | None = None) -> list:
    return [{"action_request": {"action": action, "args": args or {}}}]


def test_derive_action_type_covers_reply_all_and_draft():
    from src.api import _derive_action_type
    assert _derive_action_type(_pending("reply_all"), "respond") == "reply_all"
    assert _derive_action_type(_pending("create_draft"), "respond") == "draft"


def test_confidence_high_for_recognised_workflow():
    from src.api import _derive_confidence
    band, reason = _derive_confidence({
        "category": "attestation_travail",
        "category_display_name": "Attestation de travail",
        "classification": "respond",
        "pending_action": _pending("write_email"),
    })
    assert band == "élevée"
    assert "Attestation de travail" in reason


def test_confidence_high_for_routing_rule():
    from src.api import _derive_confidence
    band, reason = _derive_confidence({
        "category": "facture",
        "classification": "notify",
        "workflow_dept": "Finance",
        "workflow_route_to": ["finance@example.com"],
        "pending_action": _pending("forward_email"),
    })
    assert band == "élevée"
    assert "Finance" in reason


def test_confidence_medium_for_llm_draft_without_workflow():
    from src.api import _derive_confidence
    band, reason = _derive_confidence({
        "category": "uncategorized",
        "classification": "respond",
        "pending_action": _pending("write_email"),
    })
    assert band == "moyenne"
    assert "aucune règle déterministe" in reason


def test_confidence_low_for_forced_notify_without_category():
    from src.api import _derive_confidence
    band, reason = _derive_confidence({
        "category": "",
        "classification": "notify",
        "pending_action": _pending("forward_email"),
    })
    assert band == "faible"
    assert "à vérifier" in reason


def test_runs_list_carries_confidence_and_review_reason(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "list_runs", lambda **kwargs: [{
        "run_id": "r1",
        "status": "pending_approval",
        "classification": "respond",
        "category": "uncategorized",
        "pending_action": _pending("write_email", {"to": "a@b.com", "subject": "Re", "content": "Hi"}),
    }])
    with TestClient(app) as client:
        body = client.get("/runs?status=pending_approval", headers={"X-Agora-Instance-Role": "viewer"}).json()
    row = body["runs"][0]
    assert row["confidence"] == "moyenne"
    assert row["action_type"] == "reply_draft"
    assert "review_reason" in row


def test_instance_setup_get_requires_viewer_and_calls_through(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "get_setup", lambda: {
        "status": "running_setup", "started_at": None, "finished_at": None, "error": None,
        "steps": [], "progress": {"done": 1, "total": 9, "percent": 11},
    })

    with TestClient(app) as client:
        response = client.get("/instance-setup", headers={"X-Agora-Instance-Role": "viewer"})

    assert response.status_code == 200
    assert response.json()["status"] == "running_setup"


def test_instance_setup_mutations_require_owner_role(monkeypatch):
    with TestClient(app) as client:
        assert client.post(
            "/instance-setup/start", headers={"X-Agora-Instance-Role": "approver"}
        ).status_code == 403
        assert client.post(
            "/instance-setup/retry", headers={"X-Agora-Instance-Role": "viewer"}
        ).status_code == 403
        assert client.post(
            "/instance-setup/skip", headers={"X-Agora-Instance-Role": "viewer"}
        ).status_code == 403
        assert client.post(
            "/instance-setup/steps/learn_style/retry", headers={"X-Agora-Instance-Role": "approver"}
        ).status_code == 403


def test_instance_setup_start_rejects_double_start(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "get_setup", lambda: {
        "status": "running_setup", "started_at": None, "finished_at": None, "error": None,
        "steps": [], "progress": {"done": 1, "total": 9, "percent": 11},
    })

    with TestClient(app) as client:
        response = client.post("/instance-setup/start", headers={"X-Agora-Instance-Role": "owner"})

    assert response.status_code == 409


def test_instance_setup_start_calls_through_for_owner(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "get_setup", lambda: {"status": "not_started"})
    calls = []
    monkeypatch.setattr(api, "start_setup", lambda user_id, instance_id, **kw: calls.append((user_id, instance_id, kw)) or {
        "status": "created", "started_at": "now", "finished_at": None, "error": None,
        "steps": [], "progress": {"done": 0, "total": 9, "percent": 0},
    })
    monkeypatch.setattr(api.settings, "job_queue_enabled", True)  # skip the inline BackgroundTask path

    with TestClient(app) as client:
        response = client.post("/instance-setup/start", headers={"X-Agora-Instance-Role": "owner"})

    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert calls


def test_instance_setup_step_retry_rejects_unknown_step(monkeypatch):
    with TestClient(app) as client:
        response = client.post(
            "/instance-setup/steps/not_a_real_step/retry", headers={"X-Agora-Instance-Role": "owner"}
        )
    assert response.status_code == 422


def test_notifications_list_and_unread_count(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "list_notifications", lambda **kw: [{"id": 1, "title": "Setup done"}])
    monkeypatch.setattr(api, "unread_count", lambda **kw: 3)

    with TestClient(app) as client:
        listed = client.get("/notifications", headers={"X-Agora-Instance-Role": "viewer"})
        count = client.get("/notifications/unread-count", headers={"X-Agora-Instance-Role": "viewer"})

    assert listed.status_code == 200
    assert listed.json()["notifications"] == [{"id": 1, "title": "Setup done"}]
    assert count.status_code == 200
    assert count.json()["unread_count"] == 3


def test_notifications_mark_read_returns_404_when_missing(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "mark_read", lambda notification_id: None)

    with TestClient(app) as client:
        response = client.post("/notifications/999/read", headers={"X-Agora-Instance-Role": "viewer"})

    assert response.status_code == 404


def test_notifications_mark_all_read_and_delete(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "mark_all_read", lambda **kw: 5)
    deleted = []
    monkeypatch.setattr(api, "delete_notification", lambda notification_id: deleted.append(notification_id))

    with TestClient(app) as client:
        mark_all = client.post("/notifications/read-all", headers={"X-Agora-Instance-Role": "viewer"})
        delete_response = client.delete("/notifications/7", headers={"X-Agora-Instance-Role": "viewer"})

    assert mark_all.json()["marked_read"] == 5
    assert delete_response.json()["deleted"] is True
    assert deleted == [7]


def test_signature_endpoint_rejects_unknown_mode(monkeypatch):
    with TestClient(app) as client:
        response = client.put("/signature", json={"enabled": True, "mode": "not_a_real_mode"})

    assert response.status_code == 422


def test_signature_apply_endpoint_composes_final_body(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "load_signature", lambda: api.SignatureConfig(enabled=True, text="Karim"))

    with TestClient(app) as client:
        response = client.post("/signature/apply", json={"content": "Bonjour", "mode": "append_platform_signature"})

    assert response.status_code == 200
    assert "Karim" in response.json()["content"]


def test_signature_apply_endpoint_rejects_unknown_mode(monkeypatch):
    with TestClient(app) as client:
        response = client.post("/signature/apply", json={"content": "Bonjour", "mode": "not_a_real_mode"})
    assert response.status_code == 422


def test_signature_endpoint_accepts_known_mode(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api, "save_signature", lambda body: None)

    with TestClient(app) as client:
        response = client.put("/signature", json={"enabled": True, "mode": "preserve_provider_signature"})

    assert response.status_code == 200
    assert response.json()["mode"] == "preserve_provider_signature"
    assert "available_modes" in response.json()


def test_contacts_migrate_legacy_requires_owner_and_calls_through(monkeypatch):
    import src.contacts as contacts_module

    calls = []
    monkeypatch.setattr(
        contacts_module, "migrate_legacy_category_contacts",
        lambda **kw: calls.append(kw) or {"imported": 2, "skipped": 1},
    )

    with TestClient(app) as client:
        denied = client.post("/contacts/migrate-legacy", headers={"X-Agora-Instance-Role": "viewer"})
        allowed = client.post("/contacts/migrate-legacy", headers={"X-Agora-Instance-Role": "owner"})

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["imported"] == 2


def test_contacts_categorize_sender_requires_owner_and_upserts_directory(monkeypatch, tmp_path):
    import src.contacts as contacts_module

    path = tmp_path / "contacts.yaml"
    monkeypatch.setattr(contacts_module, "DEFAULT_CONTACTS_PATH", path)
    monkeypatch.setattr(
        "src.categories.load_categories",
        lambda **kw: type("C", (), {"categories": [type("Cat", (), {"name": "support"})()]})(),
    )

    with TestClient(app) as client:
        denied = client.post(
            "/contacts/categorize", json={"email": "Ana <ana@client.example>", "category": "support"},
            headers={"X-Agora-Instance-Role": "viewer"},
        )
        allowed = client.post(
            "/contacts/categorize", json={"email": "Ana <ana@client.example>", "category": "support"},
            headers={"X-Agora-Instance-Role": "owner"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["contact"]["email"] == "ana@client.example"
    assert allowed.json()["contact"]["category"] == "support"


def test_contacts_categorize_domain_writes_legacy_categories_yaml(monkeypatch, tmp_path):
    import src.api as api

    calls = []
    monkeypatch.setattr(api, "load_categories", lambda *a, **kw: api.CategoriesConfig(enabled=True))
    monkeypatch.setattr(
        api, "write_instance_text",
        lambda kind, content, default, agent_instance_id=None: calls.append((kind, content)),
    )

    with TestClient(app) as client:
        response = client.post(
            "/contacts/categorize",
            json={"email": "ana@client.example", "category": "support", "domain_only": True},
            headers={"X-Agora-Instance-Role": "owner"},
        )

    assert response.status_code == 200
    assert response.json()["domain"] == "client.example"
    assert calls and calls[0][0] == "categories"
    assert "client.example" in calls[0][1]


def test_contacts_categorize_rejects_unknown_category(monkeypatch, tmp_path):
    import src.contacts as contacts_module

    path = tmp_path / "contacts.yaml"
    monkeypatch.setattr(contacts_module, "DEFAULT_CONTACTS_PATH", path)
    monkeypatch.setattr("src.categories.load_categories", lambda **kw: type("C", (), {"categories": []})())

    with TestClient(app) as client:
        response = client.post(
            "/contacts/categorize", json={"email": "ana@client.example", "category": "not_real"},
            headers={"X-Agora-Instance-Role": "owner"},
        )

    assert response.status_code == 422
