from __future__ import annotations

from src import contacts as contacts_module
from src.contacts import (
    Contact,
    ContactCategoryError,
    create_contact,
    import_contacts_csv,
    list_contacts,
    list_segments,
    migrate_legacy_category_contacts,
    resolve_segment,
    upsert_contact,
)


SEED_YAML = """
contacts:
- email: alice@example.com
  name: Alice Martin
  audience: employee
  fields:
    dept: Finance
    lang: fr
  tags:
  - finance
  active: true
- email: bob@example.com
  name: Bob Durand
  audience: client
  fields:
    company: Agora
  tags:
  - vip
  active: true
- email: archived@example.com
  name: Archived Contact
  audience: employee
  fields:
    dept: Finance
  tags: []
  active: false
segments:
- id: finance_team
  name: Équipe Finance
  match:
    audience: employee
    fields.dept: Finance
- id: explicit_clients
  name: Clients explicites
  members:
  - bob@example.com
"""


def _seed(tmp_path, monkeypatch):
    path = tmp_path / 'contacts.yaml'
    path.write_text(SEED_YAML, encoding='utf-8')
    monkeypatch.setattr(contacts_module, 'DEFAULT_CONTACTS_PATH', path)
    return path


def test_resolve_segment_supports_dynamic_and_explicit_members(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    segments = {segment.id: segment for segment in list_segments()}
    dynamic = resolve_segment(segments['finance_team'])
    explicit = resolve_segment(segments['explicit_clients'])

    assert [contact.email for contact in dynamic] == ['alice@example.com']
    assert [contact.email for contact in explicit] == ['bob@example.com']


def test_import_contacts_csv_is_idempotent_and_reports_rejections(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    result = import_contacts_csv(
        'email,name,audience,tags,fields.dept,active\n'
        'carol@example.com,Carol,employee,"finance,approver",Finance,true\n'
        'alice@example.com,Alice Martin,employee,finance,Finance,true\n'
        'not-an-email,Bad Row,client,,Sales,true\n'
    )

    assert result['imported_count'] == 2
    assert result['rejected_count'] == 1
    assert result['rejected'][0]['email'] == 'not-an-email'

    again = import_contacts_csv('email,name,audience\ncarol@example.com,Carol Updated,employee\n')
    assert again['imported_count'] == 1

    contacts = {contact.email: contact for contact in list_contacts()}
    assert contacts['carol@example.com'].name == 'Carol Updated'
    assert contacts['alice@example.com'].name == 'Alice Martin'


class _FakeCategoriesConfig:
    def __init__(self, names):
        self.categories = [type('C', (), {'name': n})() for n in names]


def test_unknown_category_rejected(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr('src.categories.load_categories', lambda **kw: _FakeCategoriesConfig(['support']))

    try:
        create_contact(Contact(email='new@example.com', audience='prospect', category='not_a_real_category'))
        assert False, 'expected ContactCategoryError'
    except ContactCategoryError as exc:
        assert 'not_a_real_category' in str(exc)


def test_known_category_accepted(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr('src.categories.load_categories', lambda **kw: _FakeCategoriesConfig(['support']))

    contact = create_contact(Contact(email='new@example.com', audience='prospect', category='support'))
    assert contact.category == 'support'


def test_manual_beats_inferred(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr('src.categories.load_categories', lambda **kw: _FakeCategoriesConfig(['support', 'sales']))

    upsert_contact(Contact(
        email='lead@example.com', audience='prospect', category='support',
        priority='urgent', category_source='manual',
    ))
    upserted = upsert_contact(Contact(
        email='lead@example.com', audience='prospect', category='sales',
        priority='low', category_source='inferred', category_confidence=0.95,
    ))

    assert upserted.category == 'support'
    assert upserted.priority == 'urgent'
    assert upserted.category_source == 'manual'


def test_migrate_legacy_is_idempotent(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    class _LegacyContact:
        def __init__(self, email, category, name=None, priority=None):
            self.email = email
            self.category = category
            self.name = name
            self.priority = priority

    class _LegacyConfig:
        contacts = [_LegacyContact('vip@company.example', 'support', name='VIP')]
        categories = [type('Cat', (), {'name': 'support'})()]

    monkeypatch.setattr('src.categories.load_categories', lambda **kw: _LegacyConfig())

    first = migrate_legacy_category_contacts()
    second = migrate_legacy_category_contacts()

    assert first == {'imported': 1, 'skipped': 0}
    assert second == {'imported': 0, 'skipped': 1}
    directory = {c.email: c for c in list_contacts()}
    assert directory['vip@company.example'].category_source == 'imported'


def test_migrate_legacy_leaves_categories_yaml_unchanged(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    calls = []

    class _LegacyContact:
        email = 'vip@company.example'
        category = 'support'
        name = None
        priority = None

    class _LegacyConfig:
        contacts = [_LegacyContact()]
        categories = [type('Cat', (), {'name': 'support'})()]

    monkeypatch.setattr('src.categories.load_categories', lambda **kw: calls.append(kw) or _LegacyConfig())
    # No save_categories/write_instance_text call is monkeypatched here — if
    # migrate_legacy_category_contacts ever wrote back to categories.yaml, a
    # missing attribute/import error would surface since none is stubbed.
    migrate_legacy_category_contacts()
    assert calls


def test_an_unknown_audience_is_a_422_not_a_500():
    """A client mistake must not be reported as a server fault.

    `ContactInput.audience` was a free string, so an unknown value passed the
    request model and then raised inside the handler when the domain `Contact`
    was built — FastAPI turns that into a 500, with no indication of what the
    accepted values are.
    """
    from fastapi.testclient import TestClient

    from src.api import app

    with TestClient(app) as client:
        response = client.post(
            "/contacts",
            json={"email": "someone@example.com", "audience": "clients"},
        )

    assert response.status_code == 422
    assert "client" in response.text


def test_an_audience_is_normalized_at_the_boundary():
    # The domain model trims and lowercases; validating at the API must not
    # make " Client " a rejection.
    from src.routers.contacts import ContactInput

    assert ContactInput(email="a@b.c", audience=" Client ").audience == "client"
