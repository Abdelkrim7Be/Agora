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
