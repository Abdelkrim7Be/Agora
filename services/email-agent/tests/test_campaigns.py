from __future__ import annotations

from fastapi.testclient import TestClient

from src import campaigns as campaigns_module
from src import contacts as contacts_module
from src.api import app
from src.campaigns import (
    CampaignTemplate,
    Group,
    GroupMember,
    audience_guard,
    contacts_for_segment,
    load_campaigns,
    render_campaign,
    render_campaign_for_segment,
    render_for_member,
)


SEED_CAMPAIGNS_YAML = """
groups:
- id: team
  name: Team
  type: employees
  segment_id: team
- id: clients
  name: Clients
  type: clients
  segment_id: clients
templates:
- name: announce
  audience:
  - employee
  category: communication_interne
  subject: 'Info {{name}}'
  body_markdown: |
    ## Bonjour {{name}}

    Mise à jour pour l'équipe **{{dept}}**.

    - point un
    - point deux
  variables:
  - name
  - dept
- name: promo
  audience:
  - client
  category: newsletter
  subject: 'Bonjour {{name}}'
  body_markdown: |
    Une offre pour {{company}}.
  variables:
  - name
  - company
- name: broken
  audience:
  - employee
  category: communication_interne
  subject: 'Bonjour {{name}}'
  body_markdown: |
    Montant: {{amount}}
  variables:
  - name
  - amount
"""

SEED_CONTACTS_YAML = """
contacts:
- email: a@corp.com
  name: Alice Martin
  audience: employee
  fields:
    dept: HR
  tags: []
  active: true
- email: b@corp.com
  name: Bob Durand
  audience: employee
  fields:
    dept: Finance
  tags: []
  active: true
- email: c@client.com
  name: Claire Client
  audience: client
  fields:
    company: ACME
  tags: []
  active: true
segments:
- id: team
  name: Team
  members:
  - a@corp.com
  - b@corp.com
- id: clients
  name: Clients
  members:
  - c@client.com
"""


def _seed(tmp_path, monkeypatch):
    campaigns_path = tmp_path / 'campaigns.yaml'
    campaigns_path.write_text(SEED_CAMPAIGNS_YAML, encoding='utf-8')
    monkeypatch.setattr(campaigns_module, 'DEFAULT_CAMPAIGNS_PATH', campaigns_path)

    contacts_path = tmp_path / 'contacts.yaml'
    contacts_path.write_text(SEED_CONTACTS_YAML, encoding='utf-8')
    monkeypatch.setattr(contacts_module, 'DEFAULT_CONTACTS_PATH', contacts_path)
    return campaigns_path, contacts_path


def test_render_personalizes_each_member():
    group = Group(
        id='g',
        name='G',
        type='clients',
        members=[
            GroupMember(email='a@corp.com', name='Alice Martin', fields={'dept': 'HR'}),
            GroupMember(email='b@corp.com', name='Bob Durand', fields={'dept': 'Finance'}),
        ],
    )
    template = CampaignTemplate(
        name='t',
        subject='Hi {{name}}',
        body_markdown='## {{name}}\n\nDept: {{dept}}',
        variables=['name', 'dept'],
        audience=['employee'],
        category='internal',
    )
    rendered = render_campaign(group, template)
    assert [r.email for r in rendered] == ['a@corp.com', 'b@corp.com']
    assert rendered[0].subject == 'Hi Alice Martin'
    assert 'Alice Martin' in rendered[0].html and '<h2' in rendered[0].html
    assert rendered[1].subject == 'Hi Bob Durand'
    assert 'Finance' in rendered[1].html
    assert rendered[0].unresolved == []


def test_render_flags_unresolved_vars():
    member = GroupMember(email='x@corp.com', name='X')
    template = CampaignTemplate(
        name='t',
        subject='Hi {{name}}',
        body_markdown='Owe {{amount}}',
        variables=['amount'],
        audience=['employee'],
        category='internal',
    )
    rendered = render_for_member(template, member)
    assert 'amount' in rendered.unresolved


def test_loader_roundtrip(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    cfg = load_campaigns()
    assert [g.id for g in cfg.groups] == ['team', 'clients']
    assert cfg.groups[0].segment_id == 'team'
    assert cfg.templates[0].audience == ['employee']
    assert cfg.templates[0].category == 'communication_interne'


def test_render_campaign_for_segment_uses_contact_directory(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    template = load_campaigns().templates[0]
    rendered = render_campaign_for_segment('team', template)
    assert [item.email for item in rendered] == ['a@corp.com', 'b@corp.com']
    assert rendered[0].subject == 'Info Alice Martin'


def test_audience_guard_allows_intersection(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    template = load_campaigns().templates[0]
    guard = audience_guard(template, contacts_for_segment('team'), segment_name='Team')
    assert guard.allowed is True
    assert guard.segment_audiences == ['employee']


def test_audience_guard_blocks_mismatch_with_french_reason(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    template = load_campaigns().templates[1]
    guard = audience_guard(template, contacts_for_segment('team'), segment_name='Team')
    assert guard.allowed is False
    assert 'Audience incompatible' in guard.reason
    assert 'client' in guard.reason
    assert 'employee' in guard.reason


def test_campaign_preview_returns_first_email_and_count(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with TestClient(app) as client:
        preview = client.post('/campaigns/preview', json={'segment_id': 'team', 'template_name': 'announce'})
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body['recipient_count'] == 2
        assert body['segment_id'] == 'team'
        assert body['segment_name'] == 'Team'
        assert body['template_audience'] == ['employee']
        assert body['preview']['subject'] == 'Info Alice Martin'
        assert body['audience_match'] is True
        assert body['missing_variables'] == []


def test_campaign_preview_flags_missing_variables_before_send(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with TestClient(app) as client:
        preview = client.post('/campaigns/preview', json={'segment_id': 'team', 'template_name': 'broken'})
        assert preview.status_code == 200, preview.text
        missing = preview.json()['missing_variables']
        assert missing
        assert missing[0]['email'] == 'a@corp.com'
        assert 'amount' in missing[0]['unresolved']


def test_campaign_prepare_blocks_audience_mismatch(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with TestClient(app) as client:
        prep = client.post('/campaigns/prepare', json={'segment_id': 'team', 'template_name': 'promo'})
        assert prep.status_code == 422
        assert 'Audience incompatible' in prep.text


def test_campaign_prepare_blocks_missing_variables(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with TestClient(app) as client:
        prep = client.post('/campaigns/prepare', json={'segment_id': 'team', 'template_name': 'broken'})
        assert prep.status_code == 422
        assert 'Variables manquantes' in prep.text


def test_campaign_prepare_and_approve_dry_run(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with TestClient(app) as client:
        prep = client.post('/campaigns/prepare', json={'segment_id': 'team', 'template_name': 'announce'})
        assert prep.status_code == 200, prep.text
        body = prep.json()
        assert body['recipient_count'] == 2
        assert body['segment_id'] == 'team'
        assert body['preview']['subject'] == 'Info Alice Martin'
        cid = body['campaign_id']

        listing = client.get('/campaigns').json()
        assert any(c['campaign_id'] == cid for c in listing['campaigns'])

        approve = client.post(f'/campaigns/{cid}/approve')
        assert approve.status_code == 200, approve.text
        result = approve.json()['result']
        assert result['dry_run'] is True
        assert len(result['sent']) == 2
        assert result['denied'] == [] and result['failed'] == []
        assert all(c['campaign_id'] != cid for c in client.get('/campaigns').json()['campaigns'])


def test_campaign_reject_removes_pending(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with TestClient(app) as client:
        cid = client.post('/campaigns/prepare', json={'segment_id': 'team', 'template_name': 'announce'}).json()['campaign_id']
        assert client.post(f'/campaigns/{cid}/reject').status_code == 200
        assert client.post(f'/campaigns/{cid}/approve').status_code == 404


def test_group_and_template_crud(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.post('/campaigns/groups', json={
            'id': 'partners', 'name': 'Partners', 'type': 'clients',
            'members': [{'email': 'partner@corp.com', 'name': 'Partner', 'fields': {'company': 'PartnerCo'}}],
        })
        assert r.status_code == 200
        assert r.json()['group']['segment_id'] == 'partners'
        groups = client.get('/campaigns/groups').json()['groups']
        assert {g['id'] for g in groups} == {'team', 'clients', 'partners'}
        assert groups[-1]['members'][0]['email'] == 'partner@corp.com'

        assert client.delete('/campaigns/groups/partners').status_code == 200
        assert {g['id'] for g in client.get('/campaigns/groups').json()['groups']} == {'team', 'clients'}

        t = client.post('/campaigns/templates', json={
            'name': 'promo_bis',
            'subject': 'S',
            'body_markdown': '## Hi {{name}}',
            'variables': ['name'],
            'audience': ['client'],
            'category': 'newsletter',
        })
        assert t.status_code == 200
        templates = {x['name']: x for x in client.get('/campaigns/templates').json()['templates']}
        assert templates['promo_bis']['audience'] == ['client']
        assert templates['promo_bis']['category'] == 'newsletter'
