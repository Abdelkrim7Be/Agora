from __future__ import annotations

import uuid

from src.categories import classify_category, load_categories
from src.run_registry import list_runs, upsert_run


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


CATEGORIES_YAML = """
enabled: true
categories:
  - name: reclamation
    display_name: Reclamation
    priority: urgent
    owner: Support team
    approver: support.manager@company.example
    route_to:
      - support@company.example
      - quality@company.example
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
    assert cfg.categories[0].owner == "Support team"
    assert cfg.categories[0].approver == "support.manager@company.example"
    assert cfg.categories[0].route_to == ["support@company.example", "quality@company.example"]
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
        "owner": "Support team",
        "approver": "support.manager@company.example",
        "route_to": ["support@company.example", "quality@company.example"],
        "contact": None,
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
            "workflow_owner": "Support team",
            "workflow_approver": "support.manager@company.example",
            "workflow_route_to": ["support@company.example", "quality@company.example"],
        },
    )

    runs = list_runs(status="pending_approval", path=path)
    assert runs[0]["category"] == "reclamation"
    assert runs[0]["category_display_name"] == "Reclamation"
    assert runs[0]["priority"] == "urgent"
    assert runs[0]["template"] == "complaint_reply"
    assert runs[0]["workflow_owner"] == "Support team"
    assert runs[0]["workflow_approver"] == "support.manager@company.example"
    assert runs[0]["workflow_route_to"] == ["support@company.example", "quality@company.example"]


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


CATEGORIES_WITH_DOMAIN_CONTACT = """
enabled: true
categories:
  - name: vip
    display_name: VIP Client
    priority: urgent
    policy: auto_draft
    template: vip_reply
    when:
      sender_domain: [vip.example]
templates:
  - name: vip_reply
    subject: "Re: {{subject}}"
    body: "Dear {{name}}, thank you for reaching out."
    variables: [name, subject]
contacts:
  - domain: vip.example
    name: Important Client
    category: vip
    priority: urgent
"""


def test_contact_domain_match(tmp_path):
    from src.categories import classify_category, load_categories

    path = tmp_path / "cat.yaml"
    path.write_text(CATEGORIES_WITH_DOMAIN_CONTACT)
    cfg = load_categories(path)

    result = classify_category(
        {"author": "any@vip.example", "subject": "Hello"},
        cfg,
    )
    assert result["category"] == "vip"
    assert result["contact"] is not None
    assert result["contact"].name == "Important Client"
    assert result["contact"].domain == "vip.example"


def test_contact_domain_not_matched_when_email_present(tmp_path):
    """Domain contact ignored when email-level contact for same domain takes priority."""
    from src.categories import Contact, classify_category, load_categories, CategoriesConfig, Category
    from src.automation import RuleWhen

    cfg = CategoriesConfig(
        enabled=True,
        categories=[
            Category(name="vip", display_name="VIP", policy="notify", when=RuleWhen())
        ],
        contacts=[
            Contact(email="known@vip.example", category="vip", priority="urgent"),
            Contact(domain="vip.example", category="vip", priority="normal"),
        ],
    )
    result = classify_category({"author": "known@vip.example", "subject": "hi"}, cfg)
    assert result["contact"].email == "known@vip.example"
    assert result["priority"] == "urgent"


def test_render_template_fills_contact_name(tmp_path):
    from src.categories import Contact, render_template_text

    contact = Contact(email="a@b.com", name="Jean Dupont")
    rendered = render_template_text("Bonjour {{name}}, salut {{prenom}}!", {}, contact)
    assert rendered == "Bonjour Jean Dupont, salut Jean!"


def test_auto_draft_fills_contact_name(tmp_path):
    from src.categories import auto_draft_tool_call, Contact, load_categories

    path = tmp_path / "cat.yaml"
    path.write_text(CATEGORIES_WITH_DOMAIN_CONTACT)
    cfg = load_categories(path)

    contact = Contact(domain="vip.example", name="Alice Martin")
    call = auto_draft_tool_call(
        {"author": "alice@vip.example", "subject": "Help needed"},
        cfg,
        "vip",
        contact=contact,
    )
    assert call is not None
    assert "Alice Martin" in call["args"]["content"]


def test_unresolved_vars_detected(tmp_path):
    from src.categories import unresolved_vars

    assert unresolved_vars("Hello {{name}}, your {{ref}} is ready.") == ["name", "ref"]
    assert unresolved_vars("No placeholders here.") == []
    assert unresolved_vars("{{a}} and {{b}}") == ["a", "b"]


def test_empty_when_does_not_match(tmp_path):
    """A category with no conditions should never match (prevents catch-all footgun)."""
    from src.categories import classify_category, load_categories

    path = tmp_path / "cat.yaml"
    path.write_text("""
enabled: true
categories:
  - name: catchall
    display_name: Catch All
    policy: notify
    when: {}
templates: []
contacts: []
""")
    cfg = load_categories(path)
    result = classify_category({"author": "anyone@anywhere.com", "subject": "hi"}, cfg)
    assert result["category"] is None


def test_category_router_routes_organize_to_environment(fake_llms, monkeypatch):
    """Organize policy emits apply_label + archive tool calls and terminates."""
    import src.graph as g
    from src.categories import CategoriesConfig, Category
    from src.automation import RuleWhen
    from langchain_core.tools import tool as lc_tool

    fake_llms()
    organize_cfg = CategoriesConfig(
        enabled=True,
        categories=[Category(
            name="newsletters",
            display_name="Newsletters",
            policy="organize",
            labels=["Newsletter"],
            when=RuleWhen(sender_contains=["newsletter"]),
        )],
    )
    monkeypatch.setattr(g, "load_categories", lambda *a, **kw: organize_cfg)

    @lc_tool
    def apply_label(label: str) -> str:
        """Apply a label to the current email."""
        return f"labelled:{label}"

    @lc_tool
    def archive_email() -> str:
        """Archive the current email."""
        return "archived"

    monkeypatch.setitem(g.tools_by_name_map, "apply_label", apply_label)
    monkeypatch.setitem(g.tools_by_name_map, "archive_email", archive_email)

    email = {"author": "newsletter@promo.io", "to": "me@example.com", "subject": "Weekly digest", "email_thread": "content"}
    result = g.email_assistant.invoke({"email_input": email}, _cfg())
    assert result.get("classification_decision") == "ignore"
    assert result.get("auto_organized") is True


def test_category_router_notify_policy_terminates(fake_llms, monkeypatch):
    """Notify policy in category_router terminates without running triage LLM."""
    import src.graph as g
    from src.categories import CategoriesConfig, Category, load_categories
    from src.automation import RuleWhen

    triage_called = []
    original_router = g.llm_router

    class _SpyRouter:
        def invoke(self, *a, **kw):
            triage_called.append(True)
            return original_router.invoke(*a, **kw)

    monkeypatch.setattr(g, "llm_router", _SpyRouter())

    notify_cfg = CategoriesConfig(
        enabled=True,
        categories=[Category(
            name="invoices",
            display_name="Invoices",
            policy="notify",
            when=RuleWhen(subject_contains=["invoice"]),
        )],
    )
    monkeypatch.setattr(g, "load_categories", lambda *a, **kw: notify_cfg)

    email = {"author": "billing@corp.com", "to": "me@example.com", "subject": "Invoice #123", "email_thread": "Please pay."}
    result = g.email_assistant.invoke({"email_input": email}, _cfg())
    assert result.get("classification_decision") == "notify"
    assert not triage_called, "Triage LLM should not be called when category policy is notify"


def test_shipped_categories_classify_new_workflows():
    """Guard the shipped categories.yaml: conge/rendez_vous/devis classify as designed."""
    cfg = load_categories()

    conge = classify_category({"author": "emp@corp.com", "subject": "Demande de congé été"}, cfg)
    assert conge["category"] == "conge"
    assert conge["policy"] == "auto_draft"
    assert conge["template"] == "conge_reply"
    assert conge["route_to"] == ["abdelkrimbellagnech99@gmail.com"]

    rdv = classify_category({"author": "client@corp.com", "subject": "Rendez-vous la semaine prochaine"}, cfg)
    assert rdv["category"] == "rendez_vous"
    assert rdv["policy"] == "auto_draft"
    assert rdv["template"] == "rdv_reply"

    devis = classify_category({"author": "buyer@corp.com", "subject": "Demande de devis produit"}, cfg)
    assert devis["category"] == "devis"
    assert devis["policy"] == "notify"
    assert devis["route_to"] == ["redacted@example.com"]

    # New templates resolve and carry a body.
    templates = {t.name: t for t in cfg.templates}
    assert "conge_reply" in templates and templates["conge_reply"].body.strip()
    assert "rdv_reply" in templates and templates["rdv_reply"].body.strip()
