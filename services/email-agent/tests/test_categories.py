from __future__ import annotations

import uuid
from pathlib import Path

from langchain_core.messages import AIMessage
from langgraph.store.memory import InMemoryStore

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


def test_default_template_catalog_has_operational_auto_drafts():
    from src.categories import auto_draft_tool_call

    cfg = load_categories(Path(__file__).resolve().parents[1] / "categories.yaml")
    by_name = {category.name: category for category in cfg.categories}
    template_names = {template.name for template in cfg.templates}

    assert by_name["hr_requests"].template == "candidate_ack_reply"
    assert by_name["finance_requests"].template == "finance_request_reply"
    assert by_name["devis"].template == "quote_request_reply"
    assert {"candidate_ack_reply", "finance_request_reply", "quote_request_reply"}.issubset(template_names)

    call = auto_draft_tool_call(
        {"author": "Candidate <candidate@example.com>", "subject": "Stage data", "email_thread": "CV attached"},
        cfg,
        "hr_requests",
    )

    assert call["name"] == "write_email"
    assert call["args"]["to"] == "candidate@example.com"
    assert "candidature" in call["args"]["content"]


def test_ceo_instance_template_catalog_parses_when_present():
    from src.categories import auto_draft_tool_call

    path = Path(__file__).resolve().parents[1] / "logs" / "instances" / "ceo-email-agent" / "categories.yaml"
    if not path.is_file():
        return

    cfg = load_categories(path)
    names = {category.name for category in cfg.categories}
    assert {"executive_meeting", "partnership_opportunity", "investor_update", "sensitive_escalation"}.issubset(names)

    call = auto_draft_tool_call(
        {"author": "Partner <partner@example.com>", "subject": "Partnership proposal", "email_thread": "Can we collaborate?"},
        cfg,
        "partnership_opportunity",
    )

    assert call["name"] == "write_email"
    assert call["args"]["to"] == "partner@example.com"
    assert "partenariat" in call["args"]["content"].lower()


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
        "instructions": None,
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
    assert devis["policy"] == "auto_draft"
    assert devis["template"] == "quote_request_reply"
    assert devis["route_to"] == ["zinebbellagnech@gmail.com"]

    # New templates resolve and carry a body.
    templates = {t.name: t for t in cfg.templates}
    assert "conge_reply" in templates and templates["conge_reply"].body.strip()
    assert "rdv_reply" in templates and templates["rdv_reply"].body.strip()


CATEGORIES_WITH_INSTRUCTIONS = """
enabled: true
categories:
  - name: refund_request
    display_name: Refund request
    priority: urgent
    policy: auto_draft
    template: refund_reply
    owner: Finance
    route_to: [finance]
    when:
      subject_contains: [refund]
    instructions:
      sla: Respond within 24h
      required_data: [order number, purchase date]
      escalation: Notify the finance manager for refunds over 1000 EUR
      blocked_cases: [refunds requested after 30 days]
      ask_for_missing: true
templates:
  - name: refund_reply
    subject: "Re: {{subject}}"
    body: "We are looking into your refund."
    variables: []
"""


def test_category_instructions_round_trip_through_dump(tmp_path):
    """Structured instructions survive load -> dump -> load (YAML round-trip)."""
    from src.categories import dump_categories

    path = tmp_path / "categories.yaml"
    path.write_text(CATEGORIES_WITH_INSTRUCTIONS)
    cfg = load_categories(path)

    instructions = cfg.categories[0].instructions
    assert instructions is not None
    assert instructions.sla == "Respond within 24h"
    assert instructions.required_data == ["order number", "purchase date"]
    assert instructions.escalation == "Notify the finance manager for refunds over 1000 EUR"
    assert instructions.blocked_cases == ["refunds requested after 30 days"]
    assert instructions.ask_for_missing is True

    reloaded_path = tmp_path / "categories_reloaded.yaml"
    reloaded_path.write_text(dump_categories(cfg))
    reloaded = load_categories(reloaded_path)
    assert reloaded.categories[0].instructions.sla == "Respond within 24h"


def test_classify_category_surfaces_instructions(tmp_path):
    path = tmp_path / "categories.yaml"
    path.write_text(CATEGORIES_WITH_INSTRUCTIONS)
    cfg = load_categories(path)

    result = classify_category(
        {"author": "client@example.com", "subject": "refund request"},
        cfg,
    )
    assert result["category"] == "refund_request"
    assert result["instructions"]["sla"] == "Respond within 24h"
    assert result["instructions"]["required_data"] == ["order number", "purchase date"]


class _SpySystemPromptLLM:
    """Captures the assembled system prompt instead of calling a real LLM."""

    def __init__(self):
        self.last_system_content: str | None = None

    def invoke(self, messages, config=None):
        self.last_system_content = messages[0]["content"]
        return AIMessage(
            content="", tool_calls=[{"name": "Done", "args": {}, "id": "c1", "type": "tool_call"}]
        )


def _llm_call_state(workflow_instructions: dict | None) -> dict:
    return {
        "email_input": {
            "author": "client@example.com",
            "to": "me@example.com",
            "subject": "Refund",
            "email_thread": "Please refund my order.",
        },
        "messages": [],
        "workflow_instructions": workflow_instructions,
    }


def test_workflow_instructions_appear_only_for_owning_workflow(monkeypatch):
    """Per-workflow instructions are injected into the draft prompt; absent when unset."""
    import src.graph as g

    spy = _SpySystemPromptLLM()
    monkeypatch.setattr(g, "llm_with_tools", spy)

    instructions = {
        "sla": "Respond within 24h",
        "required_data": ["order number"],
        "escalation": "Notify the finance manager",
        "blocked_cases": ["refunds over 1000"],
        "ask_for_missing": True,
    }
    g.llm_call(
        _llm_call_state(instructions),
        InMemoryStore(),
        config={"configurable": {"thread_id": str(uuid.uuid4())}},
    )
    assert "Workflow Instructions" in spy.last_system_content
    assert "Respond within 24h" in spy.last_system_content
    assert "order number" in spy.last_system_content

    # A run with no workflow instructions gets no section at all.
    g.llm_call(
        _llm_call_state(None),
        InMemoryStore(),
        config={"configurable": {"thread_id": str(uuid.uuid4())}},
    )
    assert "Workflow Instructions" not in spy.last_system_content


# --- Workstream D: contact model unification ---

def _directory_contact(**overrides):
    from src.contacts import Contact

    fields = {"email": "vip@company.example", "audience": "prospect", "category": "internal", "priority": "urgent"}
    fields.update(overrides)
    return Contact(**fields)


def test_classify_unchanged_for_existing_fixtures(tmp_path, monkeypatch):
    """The critical regression guard: with an empty unified directory, classify_category
    must behave byte-for-byte like it did before the directory existed."""
    monkeypatch.setattr("src.contacts.list_contacts", lambda **kwargs: [])
    path = tmp_path / "categories.yaml"
    path.write_text(CATEGORIES_YAML)
    cfg = load_categories(path)

    before_contact_override = classify_category(
        {"author": "VIP <vip@company.example>", "subject": "hello"}, cfg
    )
    before_rule_match = classify_category(
        {"author": "Client <ana@client.example>", "subject": "urgent issue"}, cfg
    )

    assert before_contact_override["category"] == "internal"
    assert before_contact_override["priority"] == "urgent"
    assert before_rule_match["category"] == "reclamation"


def test_directory_contact_matches_before_rules(tmp_path, monkeypatch):
    directory_contact = _directory_contact(email="ana@client.example", category="internal")
    monkeypatch.setattr("src.contacts.list_contacts", lambda **kwargs: [directory_contact])
    path = tmp_path / "categories.yaml"
    path.write_text(CATEGORIES_YAML)
    cfg = load_categories(path)

    result = classify_category({"author": "Client <ana@client.example>", "subject": "urgent issue"}, cfg)

    # Rule matching would have classified this as 'reclamation' (sender_domain
    # client.example) — the directory contact must win instead.
    assert result["category"] == "internal"


def test_legacy_contact_still_matches_when_absent_from_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("src.contacts.list_contacts", lambda **kwargs: [])
    path = tmp_path / "categories.yaml"
    path.write_text(CATEGORIES_YAML)
    cfg = load_categories(path)

    result = classify_category({"author": "VIP <vip@company.example>", "subject": "hello"}, cfg)
    assert result["category"] == "internal"


def test_directory_wins_over_legacy_for_same_email(tmp_path, monkeypatch):
    directory_contact = _directory_contact(email="vip@company.example", category="reclamation", priority="low")
    monkeypatch.setattr("src.contacts.list_contacts", lambda **kwargs: [directory_contact])
    path = tmp_path / "categories.yaml"
    path.write_text(CATEGORIES_YAML)
    cfg = load_categories(path)

    result = classify_category({"author": "VIP <vip@company.example>", "subject": "hello"}, cfg)

    # categories.yaml's legacy contact says 'internal'; the directory says 'reclamation'.
    assert result["category"] == "reclamation"
    assert result["priority"] == "low"


def test_exact_email_beats_domain_match(tmp_path, monkeypatch):
    # A domain-wide legacy contact says 'internal'; an exact-email legacy contact
    # for the same sender says 'reclamation'. Exact email must win (precedence 2 vs 4).
    legacy_yaml = CATEGORIES_YAML + (
        "\n  - domain: client.example\n    category: internal\n"
        "  - email: ana@client.example\n    category: reclamation\n"
    )
    monkeypatch.setattr("src.contacts.list_contacts", lambda **kwargs: [])
    path = tmp_path / "categories.yaml"
    path.write_text(legacy_yaml)
    cfg = load_categories(path)

    result = classify_category({"author": "Client <ana@client.example>", "subject": "hello"}, cfg)
    assert result["category"] == "reclamation"


def test_domain_only_legacy_contact_matches_by_domain(tmp_path, monkeypatch):
    legacy_yaml = CATEGORIES_YAML + "\n  - domain: client.example\n    category: internal\n"
    monkeypatch.setattr("src.contacts.list_contacts", lambda **kwargs: [])
    path = tmp_path / "categories.yaml"
    path.write_text(legacy_yaml)
    cfg = load_categories(path)

    # No exact-email contact for this sender, but the domain-only contact matches,
    # and would otherwise be shadowed by the 'reclamation' rule for this domain.
    result = classify_category({"author": "Someone <other@client.example>", "subject": "urgent issue"}, cfg)
    assert result["category"] == "internal"
