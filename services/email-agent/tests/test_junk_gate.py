from src.junk_gate import is_junk


def _email(**overrides):
    base = {
        "author": "Jean Dupont <jean.dupont@example.com>",
        "to": "Support <support@agora.ai>",
        "subject": "Question sur mon dossier",
        "email_thread": "Bonjour, où en est mon dossier ?",
        "labels": ["INBOX", "UNREAD"],
    }
    base.update(overrides)
    return base


def test_human_sender_passes():
    junk, reason = is_junk(_email())
    assert not junk
    assert reason == ""


def test_gmail_promotions_label_is_junk():
    junk, reason = is_junk(_email(labels=["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"]))
    assert junk
    assert reason.startswith("gmail:")


def test_gmail_social_label_is_junk():
    junk, _ = is_junk(_email(labels=["INBOX", "UNREAD", "CATEGORY_SOCIAL"]))
    assert junk


def test_noreply_sender_is_junk():
    junk, reason = is_junk(_email(author="Acme <no-reply@acme.com>"))
    assert junk
    assert reason == "sender:noreply"


def test_notifications_sender_is_junk():
    junk, _ = is_junk(_email(author="LinkedIn <notifications@linkedin.com>"))
    assert junk


def test_bulk_domain_is_junk():
    junk, reason = is_junk(_email(author="Somebody <updates-noise@facebookmail.com>"))
    assert junk


def test_list_unsubscribe_header_is_junk():
    junk, reason = is_junk(_email(list_unsubscribe=True))
    assert junk
    assert reason == "header:list-unsubscribe"


def test_precedence_bulk_is_junk():
    junk, reason = is_junk(_email(precedence_bulk=True))
    assert junk
    assert reason == "header:precedence-bulk"


def test_partial_input_is_safe():
    junk, _ = is_junk({})
    assert not junk


def test_marketing_local_part_is_junk():
    junk, _ = is_junk(_email(author="Boutique <marketing@boutique.fr>"))
    assert junk


def test_junk_reason_survives_into_the_run_record(tmp_path, monkeypatch):
    """The gate's verdict has to be auditable after the fact.

    A gated message is recorded as an ignored run. Without the reason there is no
    way to tell a correct call from a false positive — which sender rule or bulk
    header fired is exactly what an owner needs when a real message goes missing.
    """
    from src.config import settings
    from src.run_registry import list_runs, upsert_run

    monkeypatch.setattr(settings, "run_registry_backend", "json")
    monkeypatch.setattr(settings, "database_url", "")
    index = tmp_path / "runs.json"

    upsert_run(
        "run-junk-1",
        "completed",
        email_input={
            "subject": "Votre newsletter",
            "author": "news@example.com",
            "email_id": "m_junk",
            "category": "junk_auto",
            "junk_reason": "header:list-unsubscribe",
        },
        classification="ignore",
        path=index,
    )

    record = next(r for r in list_runs(path=index) if r["email_id"] == "m_junk")
    assert record["junk_reason"] == "header:list-unsubscribe"


def test_a_normal_run_carries_no_junk_reason(tmp_path, monkeypatch):
    from src.config import settings
    from src.run_registry import list_runs, upsert_run

    monkeypatch.setattr(settings, "run_registry_backend", "json")
    monkeypatch.setattr(settings, "database_url", "")
    index = tmp_path / "runs.json"

    upsert_run(
        "run-normal-1",
        "pending_approval",
        email_input={"subject": "Devis", "author": "client@example.com", "email_id": "m_ok"},
        classification="respond",
        path=index,
    )

    record = next(r for r in list_runs(path=index) if r["email_id"] == "m_ok")
    assert record["junk_reason"] is None
