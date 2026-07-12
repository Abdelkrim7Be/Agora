from __future__ import annotations

from src import contacts as contacts_module
from src.contacts import import_contacts_csv, list_contacts, list_segments, resolve_segment


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
