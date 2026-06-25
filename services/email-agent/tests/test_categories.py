from __future__ import annotations

from src.categories import classify_category, load_categories
from src.run_registry import list_runs, upsert_run


CATEGORIES_YAML = """
enabled: true
categories:
  - name: reclamation
    display_name: Reclamation
    priority: urgent
    policy: auto_draft
    template: complaint_reply
    when:
      sender_domain: [client.example]
      subject_contains: [urgent]
  - name: internal
    display_name: Internal
    priority: normal
    policy: notify
    when:
      sender_domain: [company.example]
templates:
  - name: complaint_reply
    subject: "Re: {{subject}}"
    body: "Thanks for reaching out."
    variables: [subject]
contacts:
  - email: vip@company.example
    category: internal
    priority: urgent
"""


def test_load_categories_parses_templates_contacts_and_categories(tmp_path):
    path = tmp_path / "categories.yaml"
    path.write_text(CATEGORIES_YAML)

    cfg = load_categories(path)

    assert cfg.enabled is True
    assert cfg.categories[0].name == "reclamation"
    assert cfg.categories[0].template == "complaint_reply"
    assert cfg.templates[0].variables == ["subject"]
    assert cfg.contacts[0].priority == "urgent"


def test_classify_category_prefers_contact_override(tmp_path):
    path = tmp_path / "categories.yaml"
    path.write_text(CATEGORIES_YAML)
    cfg = load_categories(path)

    result = classify_category(
        {"author": "VIP <vip@company.example>", "subject": "hello"},
        cfg,
    )

    assert result["category"] == "internal"
    assert result["priority"] == "urgent"
    assert result["policy"] == "notify"


def test_classify_category_matches_rule_when(tmp_path):
    path = tmp_path / "categories.yaml"
    path.write_text(CATEGORIES_YAML)
    cfg = load_categories(path)

    result = classify_category(
        {"author": "Client <ana@client.example>", "subject": "urgent issue"},
        cfg,
    )

    assert result == {
        "category": "reclamation",
        "category_display_name": "Reclamation",
        "priority": "urgent",
        "template": "complaint_reply",
        "policy": "auto_draft",
    }


def test_run_registry_persists_category_metadata(tmp_path):
    path = tmp_path / "runs.json"

    upsert_run(
        "run-1",
        "pending_approval",
        path=path,
        email_input={
            "subject": "urgent issue",
            "category": "reclamation",
            "category_display_name": "Reclamation",
            "priority": "urgent",
            "template": "complaint_reply",
        },
    )

    runs = list_runs(status="pending_approval", path=path)
    assert runs[0]["category"] == "reclamation"
    assert runs[0]["category_display_name"] == "Reclamation"
    assert runs[0]["priority"] == "urgent"
    assert runs[0]["template"] == "complaint_reply"


def test_auto_draft_tool_call_renders_template(tmp_path):
    from src.categories import auto_draft_tool_call

    path = tmp_path / "categories.yaml"
    path.write_text(CATEGORIES_YAML)
    cfg = load_categories(path)

    call = auto_draft_tool_call(
        {
            "author": "Client <ana@client.example>",
            "subject": "urgent issue",
            "email_thread": "The system is down.",
        },
        cfg,
        "reclamation",
    )

    assert call["name"] == "write_email"
    assert call["args"] == {
        "to": "ana@client.example",
        "subject": "Re: urgent issue",
        "content": "Thanks for reaching out.",
    }
