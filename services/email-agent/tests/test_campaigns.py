from __future__ import annotations

from fastapi.testclient import TestClient

from src import campaigns as campaigns_module
from src import contacts as contacts_module
from src.api import app
from src.campaigns import (
    CampaignTemplate,
    Group,
    GroupMember,
    load_campaigns,
    render_campaign,
    render_for_member,
)


SEED_CAMPAIGNS_YAML = """
groups:
- id: team
  name: Team
  type: employees
  segment_id: team
templates:
- name: announce
  subject: 'Info {{name}}'
  body_markdown: |
    ## Bonjour {{name}}

    Mise à jour pour l'équipe **{{dept}}**.

    - point un
    - point deux
  variables:
  - name
  - dept
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
segments:
- id: team
  name: Team
  members:
  - a@corp.com
  - b@corp.com
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
        name='t', subject='Hi {{name}}', body_markdown='## {{name}}\n\nDept: {{dept}}', variables=['name', 'dept']
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
    template = CampaignTemplate(name='t', subject='Hi {{name}}', body_markdown='Owe {{amount}}', variables=['amount'])
    rendered = render_for_member(template, member)
    assert 'amount' in rendered.unresolved


def test_loader_roundtrip(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    cfg = load_campaigns()
    assert [g.id for g in cfg.groups] == ['team']
    assert cfg.groups[0].segment_id == 'team'
    assert [t.name for t in cfg.templates] == ['announce']


def test_campaign_prepare_and_approve_dry_run(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with TestClient(app) as client:
        prep = client.post('/campaigns/prepare', json={'group_id': 'team', 'template_name': 'announce'})
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
        cid = client.post('/campaigns/prepare', json={'group_id': 'team', 'template_name': 'announce'}).json()['campaign_id']
        assert client.post(f'/campaigns/{cid}/reject').status_code == 200
        assert client.post(f'/campaigns/{cid}/approve').status_code == 404


def test_group_and_template_crud(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.post('/campaigns/groups', json={
            'id': 'clients', 'name': 'Clients', 'type': 'clients',
            'members': [{'email': 'c@corp.com', 'name': 'Carol'}],
        })
        assert r.status_code == 200
        assert r.json()['group']['segment_id'] == 'clients'
        groups = client.get('/campaigns/groups').json()['groups']
        assert {g['id'] for g in groups} == {'team', 'clients'}
        assert groups[-1]['members'][0]['email'] == 'c@corp.com'

        assert client.delete('/campaigns/groups/clients').status_code == 200
        assert {g['id'] for g in client.get('/campaigns/groups').json()['groups']} == {'team'}

        t = client.post('/campaigns/templates', json={
            'name': 'promo', 'subject': 'S', 'body_markdown': '## Hi', 'variables': [],
        })
        assert t.status_code == 200
        assert 'promo' in {x['name'] for x in client.get('/campaigns/templates').json()['templates']}
