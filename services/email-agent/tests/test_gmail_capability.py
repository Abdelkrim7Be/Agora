from src.capabilities.email_tools import write_email


def test_write_email_dry_run():
    """With AGENT_DRY_RUN=true (default), write_email returns a dry-run string without touching Gmail."""
    result = write_email.invoke({"to": "test@example.com", "subject": "hi", "content": "body"})
    assert "Email sent to test@example.com" in result
    assert "dry run" in result
