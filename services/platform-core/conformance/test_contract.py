"""Executable form of `docs/platform/agent-contract.md`.

One test per MUST in the document, named after the section it enforces. A new
agent type is conformant when this file passes against it.
"""

from __future__ import annotations

import pytest

from conftest import CONTRACT_VERSION, TENANT_A, TENANT_B, headers_for

pytestmark = pytest.mark.conformance


# --- §1 Health --------------------------------------------------------------

def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json().get("status") == "ok"


# --- §2 Manifest ------------------------------------------------------------

def test_manifest_declares_a_contract_version_the_platform_knows(manifest):
    assert manifest.get("contract_version") == CONTRACT_VERSION


def test_manifest_identifies_the_agent_type(manifest):
    assert isinstance(manifest.get("id"), str) and manifest["id"].strip()
    assert isinstance(manifest.get("display_name"), str) and manifest["display_name"].strip()
    assert isinstance(manifest.get("description"), str) and manifest["description"].strip()


def test_manifest_declares_at_least_one_capability(manifest):
    capabilities = manifest.get("capabilities")
    assert isinstance(capabilities, list) and capabilities
    assert all(isinstance(item, str) and item.strip() for item in capabilities)


def test_manifest_settings_sections_are_renderable(manifest):
    sections = manifest.get("settings_schema")
    assert isinstance(sections, list)

    seen_keys = set()
    for section in sections:
        key = section.get("key")
        assert isinstance(key, str) and key.strip(), f"section without a key: {section}"
        assert key not in seen_keys, f"duplicate settings key: {key}"
        seen_keys.add(key)
        assert isinstance(section.get("label"), str) and section["label"].strip()
        path = section.get("path", "")
        # The gateway silently drops anything else, so the section would vanish
        # from the workspace navigation with no error anywhere.
        assert isinstance(path, str) and path.startswith("/") and not path.startswith("//"), (
            f"settings path must be a plain relative path, got {path!r}"
        )


def test_manifest_does_not_declare_platform_owned_routing(manifest):
    # The gateway ignores these; shipping them implies an authority the agent
    # does not have.
    for field in ("base_path", "health_path", "manifest_path"):
        assert field not in manifest


def test_manifest_is_the_same_for_every_tenant(client, manifest):
    # It describes the type, not an instance. An agent that varies it per tenant
    # would be cached by the gateway and served to the wrong one.
    a = client.get("/manifest", headers=headers_for(TENANT_A)).json()
    b = client.get("/manifest", headers=headers_for(TENANT_B)).json()

    assert a == b == manifest


# --- §3 Identity headers ----------------------------------------------------

def test_identity_headers_are_accepted(client):
    response = client.get("/health", headers=headers_for(TENANT_A))

    assert response.status_code == 200


def test_runs_are_scoped_to_the_instance(client):
    # The core isolation claim: two instances the caller has never used must not
    # see each other's work. An agent that ignores the header returns the same
    # rows for both, which is the cross-tenant disclosure class.
    a = client.get("/runs", headers=headers_for(TENANT_A))
    b = client.get("/runs", headers=headers_for(TENANT_B))

    assert a.status_code == 200, f"GET /runs returned {a.status_code}"
    assert b.status_code == 200

    ids_a = _run_ids(a.json())
    ids_b = _run_ids(b.json())
    assert not (ids_a & ids_b), f"runs leaked across instances: {sorted(ids_a & ids_b)}"


def test_an_unknown_instance_starts_empty(client):
    # Anything returned here was inherited from a default rather than scoped.
    response = client.get("/runs", headers=headers_for(TENANT_A))

    assert response.status_code == 200
    assert _run_ids(response.json()) == set()


def _run_ids(payload) -> set:
    rows = payload.get("runs", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return set()
    return {row.get("run_id") for row in rows if isinstance(row, dict) and row.get("run_id")}


# --- §6 Proxy transparency --------------------------------------------------

def test_an_unknown_path_is_a_404_not_a_crash(client):
    # The gateway forwards upstream status verbatim; an agent that 500s on an
    # unknown route turns every stale client link into a platform incident.
    response = client.get("/conformance/definitely-not-a-real-route", headers=headers_for(TENANT_A))

    assert response.status_code == 404


def test_an_unknown_run_is_a_404(client):
    response = client.get("/run/conformance-run-that-does-not-exist", headers=headers_for(TENANT_A))

    assert response.status_code == 404
