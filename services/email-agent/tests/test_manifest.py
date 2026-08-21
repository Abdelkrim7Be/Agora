"""The agent's side of the platform agent contract.

These assert the shape the gateway parses in `AgentRegistryService.fetchManifest`.
Breaking one of them silently demotes the platform to the gateway's offline
fallback, which is exactly the failure this endpoint exists to remove.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import app
from src.manifest import CONTRACT_VERSION, build_manifest


def test_manifest_is_served_without_authentication():
    # The gateway calls the upstream directly, before any JWT exists. It also
    # carries no tenant data, which is why it can be open.
    with TestClient(app) as client:
        response = client.get("/manifest")

    assert response.status_code == 200
    assert response.json() == build_manifest()


def test_manifest_declares_the_fields_the_gateway_reads():
    manifest = build_manifest()

    assert manifest["contract_version"] == CONTRACT_VERSION
    assert manifest["id"] == "email-agent"
    assert manifest["display_name"]
    assert manifest["description"]
    assert manifest["capabilities"]
    assert manifest["settings_schema"]


def test_manifest_does_not_declare_its_own_routing():
    # Routing is the gateway's decision — an agent that could name its own proxy
    # prefix could claim another agent's traffic. The gateway ignores these keys
    # anyway; this keeps us from shipping them and implying otherwise.
    manifest = build_manifest()

    for owned_by_the_platform in ("base_path", "health_path", "manifest_path", "color", "icon"):
        assert owned_by_the_platform not in manifest


def test_settings_paths_stay_on_origin():
    # The gateway drops anything that is not a plain relative path; a section
    # dropped there disappears from the workspace navigation without a word.
    for section in build_manifest()["settings_schema"]:
        assert section["key"]
        assert section["label"]
        assert section["path"].startswith("/")
        assert not section["path"].startswith("//")


def test_manifest_is_independent_of_tenant_context():
    # It describes the agent type, not one tenant's instance. Two different
    # instance headers must not produce two different manifests.
    with TestClient(app) as client:
        first = client.get("/manifest", headers={"X-Agora-Agent-Instance": "ceo-mailbox"})
        second = client.get("/manifest", headers={"X-Agora-Agent-Instance": "hr-mailbox"})

    assert first.json() == second.json()
