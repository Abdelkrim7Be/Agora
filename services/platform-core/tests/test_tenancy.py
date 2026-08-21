from platform_core.tenancy import TenantScope


def _scope() -> TenantScope:
    return TenantScope(
        default_user_id=lambda: "default",
        default_agent_instance_id=lambda: "default-agent",
        user_map=lambda: {"alice@example.com": "alice"},
    )


def test_normalizes_user_and_instance_ids():
    scope = _scope()

    assert scope.normalize_user_id(" alice+crm@example.com ") == "alice_crm@example.com"
    assert scope.normalize_agent_instance_id(" sales agent ") == "sales_agent"
    assert scope.normalize_user_id("...") == "default"


def test_resolves_external_user_map_before_normalizing():
    assert _scope().resolve_user_id("Alice@Example.com") == "alice"


def test_contexts_are_scoped_and_restore_defaults():
    scope = _scope()

    with scope.user_context("owner"), scope.agent_instance_context("ceo"):
        assert scope.current_user_id() == "owner"
        assert scope.current_agent_instance_id() == "ceo"

    assert scope.current_user_id() == "default"
    assert scope.current_agent_instance_id() == "default-agent"
