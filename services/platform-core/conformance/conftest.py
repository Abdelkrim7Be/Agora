from __future__ import annotations

import os

import httpx
import pytest

CONTRACT_VERSION = 1

# Two distinct tenants used to prove isolation. Neither should exist as a real
# instance; the point is that unknown ones stay empty rather than inheriting.
TENANT_A = ("conformance-user-a", "conformance-instance-a")
TENANT_B = ("conformance-user-b", "conformance-instance-b")


def pytest_configure(config):
    config.addinivalue_line("markers", "conformance: platform agent contract check")


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.environ.get("AGENT_CONFORMANCE_URL", "").rstrip("/")
    if not url:
        pytest.skip("set AGENT_CONFORMANCE_URL to the agent's base URL to run the conformance suite")
    return url


@pytest.fixture(scope="session")
def client(base_url: str):
    # Generous: a cold agent may be opening a database or a checkpointer.
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client


@pytest.fixture(scope="session")
def manifest(client) -> dict:
    response = client.get("/manifest")
    if response.status_code != 200:
        pytest.fail(f"GET /manifest returned {response.status_code}; the contract requires 200")
    return response.json()


def headers_for(tenant: tuple[str, str], role: str = "owner") -> dict[str, str]:
    user, instance = tenant
    return {
        "X-Agora-User": user,
        "X-Agora-Agent-Instance": instance,
        "X-Agora-Instance-Role": role,
    }
