import pytest

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


def test_own_component_alert_is_junk():
    """The admin alert recipient is frequently the monitored mailbox itself —
    a down/up alert must not loop back into triage as ordinary mail."""
    junk, reason = is_junk(_email(subject="Alerte Agora AI : Poller hors service"))
    assert junk
    assert reason == "system:self-alert"


def test_own_component_resolution_is_junk():
    junk, reason = is_junk(_email(subject="Resolution Agora AI : Poller de nouveau actif"))
    assert junk
    assert reason == "system:self-alert"


def test_own_alert_is_junk_even_from_an_allowed_sender():
    from src.junk_config import JunkConfig

    config = JunkConfig(allowed_senders=["jean.dupont@example.com"])
    junk, reason = is_junk(_email(subject="Alerte Agora AI : Securite hors service"), config)
    assert junk
    assert reason == "system:self-alert"


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


# --- Block candidates derived from the mailbox (plan 4.2) ---

def test_junk_suggestions_rank_real_bulk_senders():
    from src.junk_config import JunkConfig, suggest_junk_senders

    messages = (
        [{"from": "Shop <noreply@shop.example>", "subject": f"Promo {i}"} for i in range(4)]
        + [{"from": "News <newsletter@media.example>", "subject": "Hebdo"} for _ in range(2)]
        + [{"from": "Sarah <sarah@client.example>", "subject": "Devis"}]
    )
    suggestions = suggest_junk_senders(messages, JunkConfig())

    assert [s["address"] for s in suggestions] == [
        "noreply@shop.example",
        "newsletter@media.example",
    ]
    assert suggestions[0]["count"] == 4
    assert suggestions[0]["reason"].startswith("sender:")
    # Sample subjects help the owner recognise the sender before blocking it.
    assert suggestions[0]["subjects"][:1] == ["Promo 0"]


def test_junk_suggestions_skip_already_listed_senders():
    from src.junk_config import JunkConfig, suggest_junk_senders

    messages = [{"from": "Shop <noreply@shop.example>", "subject": "Promo"} for _ in range(3)]

    blocked = JunkConfig(blocked_senders=["noreply@shop.example"])
    assert suggest_junk_senders(messages, blocked) == []

    by_domain = JunkConfig(blocked_domains=["shop.example"])
    assert suggest_junk_senders(messages, by_domain) == []

    # An explicitly allowed sender must never be offered as a block candidate.
    allowed = JunkConfig(allowed_senders=["noreply@shop.example"])
    assert suggest_junk_senders(messages, allowed) == []


def test_junk_suggestions_ignore_ordinary_correspondents():
    from src.junk_config import JunkConfig, suggest_junk_senders

    messages = [{"from": "Sarah <sarah@client.example>", "subject": "Devis"} for _ in range(9)]
    assert suggest_junk_senders(messages, JunkConfig()) == []


# Senders that reached the model and were ignored anyway, taken from the live
# corpus. Each one is a triage call the deterministic path now avoids for good.
BULK_SENDERS_FROM_LIVE_MAIL = [
    ("Quora Digest <english-quora-digest@quora.com>", "sender:bulk-domain"),
    ("ByteByteGo <bytebytego@substack.com>", "sender:bulk-domain"),
    ("Bitdefender <bitdefender@hello.bitdefender.com>", "sender:bulk-subdomain"),
    ("BlaBlaCar <hello@community.blablacar.com>", "sender:bulk-subdomain"),
    ("Temu <temu@eu.temuemail.com>", "sender:esp-domain"),
]


@pytest.mark.parametrize("author,expected_reason", BULK_SENDERS_FROM_LIVE_MAIL)
def test_known_bulk_platforms_never_reach_the_model(author, expected_reason):
    junk, reason = is_junk(_email(author=author))

    assert junk
    assert reason == expected_reason


@pytest.mark.parametrize(
    "author",
    [
        # Transactional mail a business mailbox must see, on subdomains close to
        # the bulk ones. These are why "orders"/"billing"/"invoice" are not in the
        # bulk-subdomain list.
        "Fournisseur <orders@orders.fournisseur.fr>",
        "Comptabilité <invoice@billing.fournisseur.fr>",
        "Jean Dupont <jean.dupont@boutique.fr>",
    ],
)
def test_transactional_senders_still_pass(author):
    junk, _ = is_junk(_email(author=author))

    assert not junk
