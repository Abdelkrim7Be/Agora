from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import app
from src.redact import redact, restore

client = TestClient(app)

SAMPLE = (
    "Bonjour, merci de virer le solde sur FR76 3000 6000 0112 3456 7890 189 "
    "(BIC AGRIFRPP). Carte 4111 1111 1111 1111. NIR 1 84 12 76 451 089 46. "
    "Tel 06 12 34 56 78, contact jean.dupont@client.fr. Commande 99887766."
)


def test_redacts_financial_and_personal_identifiers():
    result = redact(SAMPLE)
    for secret in (
        "FR76 3000 6000 0112 3456 7890 189",
        "4111 1111 1111 1111",
        "1 84 12 76 451 089 46",
        "jean.dupont@client.fr",
    ):
        assert secret not in result.text
    kinds = result.kinds()
    assert kinds["IBAN"] == 1
    assert kinds["CARD"] == 1
    assert kinds["NIR"] == 1
    assert kinds["EMAIL"] == 1


def test_redaction_round_trips_exactly():
    result = redact(SAMPLE)
    assert restore(result.text, result.mapping) == SAMPLE


def test_repeated_value_gets_one_stable_placeholder():
    text = "Ecrire a a@b.fr puis relancer a@b.fr"
    result = redact(text)
    assert result.count == 1
    assert result.text.count("[EMAIL_1]") == 2


def test_order_reference_is_not_mistaken_for_a_card():
    """A 16-digit reference that fails Luhn is not a payment card."""
    result = redact("Reference 1234567812345678")
    assert "CARD" not in result.kinds()


def test_restore_handles_double_digit_placeholders():
    mapping = {f"[EMAIL_{i}]": f"user{i}@x.fr" for i in range(1, 12)}
    text = " ".join(mapping)
    restored = restore(text, mapping)
    assert "user11@x.fr" in restored
    assert "[EMAIL_1]" not in restored


def test_sanitize_returns_redacted_content_and_map():
    response = client.post(
        "/sanitize",
        json={"content": SAMPLE, "sender": "a@b.fr", "subject": "Facture"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "4111 1111 1111 1111" not in body["redacted_text"]
    assert body["redaction_map"]
    # cleaned_text keeps the real values; only redacted_text is safe to ship out.
    assert "4111 1111 1111 1111" in body["cleaned_text"]


def test_redact_and_restore_endpoints_round_trip():
    redacted = client.post("/redact", json={"text": SAMPLE}).json()
    assert "FR76" not in redacted["redacted_text"]
    assert redacted["counts"]["IBAN"] == 1
    restored = client.post(
        "/restore",
        json={"text": redacted["redacted_text"], "mapping": redacted["mapping"]},
    ).json()
    assert restored["text"] == SAMPLE


def test_redaction_can_be_disabled(monkeypatch):
    from src import sanitize as sanitize_module

    monkeypatch.setattr(sanitize_module.settings, "redact_pii", False)
    body = client.post(
        "/sanitize", json={"content": SAMPLE, "sender": "a@b.fr", "subject": "Facture"}
    ).json()
    assert body["redaction_map"] == {}
    assert body["redacted_text"] == body["cleaned_text"]
